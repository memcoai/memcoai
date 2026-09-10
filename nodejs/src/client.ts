/**
 * The Memco client.
 *
 * Node has no synchronous gRPC, so there is one client and every call it makes
 * returns a promise.
 */

import { Metadata, status } from '@grpc/grpc-js'
import type { CallOptions } from '@grpc/grpc-js'

import {
  MemcoAuthenticationError,
  MemcoConfigError,
  MemcoError,
  MemcoUnhealthyError,
  fromServiceError
} from './errors.js'
import { buildTransport, type Transport } from './internal/channel.js'
import { deadline, resolve, type ResolveOptions } from './internal/config.js'
import { getLogger, ROOT, setLevel } from './internal/logging.js'
import { provenance as readProvenance } from './internal/provenance.js'
import {
  ServingStatus,
  servingStatusName,
  type HealthCheckResponse
} from './internal/wire.js'
import { MemoryOperations, type Caller, type Invoke } from './operations.js'
import type { Provenance } from './types.js'

const log = getLogger(`${ROOT}.client`)

/**
 * Settings for a {@link Memco}, every one of them optional.
 *
 * An argument always wins over the environment, and the environment over the
 * built-in default. Three settings have an environment variable behind them:
 * `token` reads `MEMCO_API_TOKEN` — falling back to the deprecated
 * `MEMCO_API_KEY`, which warns — `host` reads `MEMCO_API_HOST`, and `logLevel`
 * reads `MEMCO_LOG`. The rest fall straight through to their built-in values —
 * TLS on, and
 * {@link DEFAULT_TIMEOUT} seconds as the deadline every call inherits. The
 * credential is the one connection setting with no default at all: a client
 * that finds none is refused rather than left to fail on its first call.
 *
 * All of it is resolved by the constructor, before a socket is opened, so a
 * blank host, an unparseable port or a timeout that is not a positive number is
 * a {@link MemcoConfigError} out of `new Memco(...)` rather than a transport
 * failure later on.
 *
 * {@link MemcoOptions.logLevel} is the odd one out: everything else here
 * configures this client, and that configures the process.
 */
export interface MemcoOptions extends ResolveOptions {
  /**
   * Log level to apply before anything else happens, one of `critical`,
   * `error`, `warning`, `info`, `debug`, or `none` to silence the SDK.
   *
   * Setting it is process-wide, because a logger is. Prefer the `MEMCO_LOG`
   * environment variable in an application that has its own logging: it says
   * the same thing without a library reaching into global state on the
   * application's behalf.
   *
   * @throws MemcoConfigError If it does not name a level. A bad `MEMCO_LOG`
   *   warns and falls back; a bad argument here is the caller's own code.
   */
  logLevel?: string
}

/**
 * A connection to Memco Shared Memory.
 *
 * Constructing one resolves settings and opens a channel but sends nothing.
 * {@link Memco.connect} runs the two checks that prove the connection works —
 * and, on the way, teaches the client the limits the service enforces, which is
 * what lets later calls refuse an over-long field before spending a round trip
 * on it. Nothing else runs them: a client is not awaitable, so
 * `await using client = new Memco(...)` opens no connection at all — the
 * `await` in the example below belongs to {@link Memco.connect}, and `using`
 * only closes.
 *
 * @example
 * ```ts
 * import { Memco } from '@memcoai/memcoai'
 *
 * await using client = await new Memco().connect()  // reads MEMCO_API_TOKEN
 * const { domains } = await client.memory.listDomains()
 * ```
 */
export class Memco {
  /** The memory operations. */
  readonly memory: MemoryOperations

  private readonly config
  private readonly transport: Transport
  private closed = false
  private closing: Promise<void> | undefined
  private inflight = 0
  private drained: (() => void)[] = []

  /**
   * @param options Credential, endpoint and per-call deadline, each falling
   *   back to the environment where one supplies it and then to a built-in
   *   default.
   * @throws MemcoConfigError If no credential is available, if the host is
   *   blank or carries an invalid port, or if the timeout is not positive.
   */
  constructor(options: MemcoOptions = {}) {
    if (options.logLevel !== undefined) {
      setLevel(options.logLevel)
    }
    this.config = resolve(options)
    // Opened here rather than lazily. Constructing a channel dials nothing —
    // grpc-js connects on first use — so this is still a constructor that
    // performs no I/O.
    this.transport = buildTransport(this.config)
    this.memory = new MemoryOperations(this.transport.memory, this.call)
  }

  /**
   * Verify the connection.
   *
   * Probes the health endpoint, then calls
   * {@link MemoryOperations.listDomains}, which proves the credential and
   * records the limits the service enforces. Safe to call more than once; it
   * simply repeats both.
   *
   * @returns This client.
   * @throws MemcoUnavailableError If the service cannot be reached.
   * @throws MemcoUnhealthyError If it answers but is not serving.
   * @throws MemcoAuthenticationError If the credential is rejected.
   * @throws MemcoSunsetError If what this client uses is past its sunset date.
   * @throws MemcoConfigError If the client has been closed.
   */
  async connect(): Promise<this> {
    // Checked here as well as in `call`, because the health probe does not go
    // through `call` — and grpc-js throws synchronously from a closed channel,
    // so without this the raw "Channel has been shut down" would escape and
    // break the guarantee this module's header makes.
    this.ensureOpen()
    try {
      await this.checkHealth()
      // The result is discarded: what is worth keeping — the limits and the
      // per-domain tag cap — is retained by the call itself.
      await this.memory.listDomains()
    } catch (error) {
      if (error instanceof MemcoAuthenticationError) {
        log.error(`credential rejected by ${this.config.target}`)
      }
      // The channel is deliberately left open. A grpc-js channel reconnects on
      // its own backoff, so keeping it is what makes retrying this method
      // cheap rather than a fresh TCP handshake.
      throw error
    }
    log.info(`connected to ${this.config.target} (tls=${this.config.tls})`)
    return this
  }

  /**
   * Close the channel.
   *
   * Waits for calls already in flight rather than cancelling them, then
   * releases the connection. Safe to call more than once. Any call made
   * afterwards throws {@link MemcoConfigError}.
   */
  async close(): Promise<void> {
    // Shared rather than short-circuited. `closed` is set before the drain, so
    // an early return on it would let a second concurrent close() report
    // success while the first is still waiting and the channel is still open.
    this.closing ??= this.shutdown()
    await this.closing
  }

  private async shutdown(): Promise<void> {
    this.closed = true
    await this.drain()
    this.transport.close()
    log.info('closed connection to %s', this.config.target)
  }

  /**
   * Close the client at the end of an `await using` block.
   *
   * @returns When the channel is closed.
   */
  async [Symbol.asyncDispose](): Promise<void> {
    await this.close()
  }

  /**
   * Report which version of the contract this SDK was generated from.
   *
   * @returns The provenance recorded when this package was built.
   *
   * @example
   * ```ts
   * client.provenance().serverCommit  // the commit this package was built from
   * ```
   */
  provenance(): Provenance {
    return readProvenance()
  }

  /** Refuse to start anything on a channel that has been closed. */
  private ensureOpen(): void {
    if (this.closed) {
      throw new MemcoConfigError(
        'this client is closed; create a new one to make more calls'
      )
    }
  }

  /** Wait for calls already in flight to finish. */
  private async drain(): Promise<void> {
    if (this.inflight === 0) {
      return
    }
    // Waited for rather than cancelled: cancelling a call in flight is
    // indistinguishable, at the call site, from the caller cancelling it
    // themselves.
    await new Promise<void>(resolve => this.drained.push(resolve))
  }

  private finish(): void {
    this.inflight -= 1
    if (this.inflight === 0) {
      for (const waiter of this.drained.splice(0)) {
        waiter()
      }
    }
  }

  /** Probe the standard gRPC health endpoint. */
  private async checkHealth(): Promise<void> {
    const started = performance.now()
    const response = await new Promise<HealthCheckResponse>((settle, fail) => {
      this.transport.health.check(
        // An empty service name asks after the server as a whole, which is what
        // a client wants to know before it sends anything of its own.
        { service: '' },
        { deadline: this.deadlineAt(undefined) },
        (error, value) => {
          if (error) {
            fail(fromServiceError(error))
            return
          }
          settle(value)
        }
      )
    }).catch((error: unknown) => {
      throw typed(error)
    })
    if (response.status !== ServingStatus.SERVING) {
      throw new MemcoUnhealthyError(
        status.UNAVAILABLE,
        `${this.config.target} reported health status ${servingStatusName(response.status)}`
      )
    }
    log.debug(
      'health check on %s ok in %sms',
      this.config.target,
      elapsed(started)
    )
  }

  /** Turn a per-call timeout in seconds into the epoch deadline grpc-js wants. */
  private deadlineAt(timeout: number | undefined): number {
    return Date.now() + deadline(timeout, this.config.timeout) * 1000
  }

  /**
   * Invoke one RPC, translating any failure into a typed error.
   *
   * An arrow property rather than a method, so the operations can hold it
   * without binding it.
   */
  private readonly call: Caller = async (invoke, request, timeout, rpc) => {
    this.ensureOpen()
    // Built fresh, never spread from anything a caller supplied. grpc-js lets
    // an `interceptors` key in a call's options REPLACE the client-level list
    // rather than compose with it, so a caller-supplied options object reaching
    // here would silently take the credential off this one call.
    const options = { deadline: this.deadlineAt(timeout) }
    const started = performance.now()
    this.inflight += 1
    try {
      const response = await unary(invoke, request, options)
      log.debug('%s ok in %sms', rpc, elapsed(started))
      return response
    } catch (error) {
      const name = error instanceof Error ? error.name : typeof error
      log.debug('%s failed in %sms: %s', rpc, elapsed(started), name)
      throw error
    } finally {
      this.finish()
    }
  }
}

/**
 * Await one unary call.
 *
 * Extracted rather than inlined because the arrow implementing {@link Caller}
 * is contextually typed and so cannot name its own type parameters — and
 * `Response` unqualified in that position resolves to the global one from
 * `@types/node`, not to the call's.
 *
 * @param invoke The generated client's method.
 * @param request The request message.
 * @param options Call options carrying the deadline.
 * @returns The response.
 * @throws MemcoAPIError Translated once, here, so the caller never sees a raw
 *   transport error and the log record below names what they will catch.
 */
function unary<Request, Response>(
  invoke: Invoke<Request, Response>,
  request: Request,
  options: Partial<CallOptions>
): Promise<Response> {
  return new Promise<Response>((settle, fail) => {
    invoke(request, new Metadata(), options, (error, value) => {
      if (error) {
        fail(fromServiceError(error))
        return
      }
      settle(value)
    })
    // A synchronous throw from the executor — which is how grpc-js reports a
    // closed channel — rejects the promise, so `typed` below is what stops it
    // reaching the caller raw.
  }).catch((error: unknown) => {
    throw typed(error)
  })
}

/**
 * Last line of defence: turn anything that is not already one of ours into one.
 *
 * `errors.ts` promises that no raw transport error reaches a caller, and the
 * gRPC paths that throw synchronously rather than calling back are the ones
 * that would otherwise break it.
 */
function typed(error: unknown): MemcoError {
  if (error instanceof MemcoError) {
    return error
  }
  return fromServiceError({
    details: error instanceof Error ? error.message : String(error)
  })
}

function elapsed(started: number): string {
  return (performance.now() - started).toFixed(0)
}
