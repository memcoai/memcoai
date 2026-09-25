/**
 * The Memco client.
 *
 * Node has no synchronous gRPC, so there is one client and every call it makes
 * returns a promise.
 */

import { Metadata, status } from '@grpc/grpc-js'
import type { CallOptions } from '@grpc/grpc-js'

import { NetworkOperations, UserOperations } from './administration.js'
import {
  MemcoAuthenticationError,
  MemcoConfigError,
  MemcoError,
  MemcoNotFoundError,
  MemcoUnhealthyError,
  fromServiceError
} from './errors.js'
import {
  RENEW_AFTER,
  Minted,
  Renewing,
  clock,
  metadata
} from './internal/auth.js'
import { buildTransport, type Transport } from './internal/channel.js'
import {
  deadline,
  resolve,
  type ClientConfig,
  type ResolveOptions
} from './internal/config.js'
import { getLogger, ROOT, setLevel } from './internal/logging.js'
import { provenance as readProvenance } from './internal/provenance.js'
import type * as pb from './internal/gen.js'
import * as requests from './internal/requests.js'
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
 * built-in default. The credential is either a token or an API client's
 * `clientId` and `clientSecret`: `token` reads `MEMCO_API_TOKEN` — falling back
 * to the deprecated `MEMCO_API_KEY`, which warns — and the pair reads
 * `MEMCO_CLIENT_ID` and `MEMCO_CLIENT_SECRET`, which win over
 * `MEMCO_API_TOKEN` when both are set and no credential is passed. `host` reads
 * `MEMCO_API_HOST`, `tls` reads `MEMCO_API_TLS` — TLS stays on unless one of
 * them turns it off — and `logLevel` reads `MEMCO_LOG`. The rest have no
 * variable: `timeout` defaults to {@link DEFAULT_TIMEOUT} seconds, the deadline
 * every call inherits, and `tokenLifetime` to the service's own. The credential
 * is the one connection setting with no default at all: a client that finds
 * none is refused rather than left to fail on its first call.
 *
 * All of it is resolved by the constructor, before a socket is opened, so a
 * blank host, an unparseable port, half a client pair or a timeout that is not
 * a positive number is a {@link MemcoConfigError} out of `new Memco(...)`
 * rather than a transport failure later on.
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
 * One impersonation key the client minted and has not seen ended.
 *
 * Kept so that closing the client can end the keys of sessions nobody closed,
 * and so that a key whose end failed, or whose session was dropped, is ended
 * later rather than left live against the user's cap.
 */
interface LiveKey {
  /** The user the key acts as. */
  readonly externalId: string
  /**
   * When the key expires, as a {@link clock} monotonic reading, so a step in
   * the wall clock cannot make a key still in use look expired.
   */
  readonly expiresAt: number
  /**
   * The session credential the key was minted for, by a token that does not
   * keep that credential alive.
   */
  readonly owner: object
  /** Whether ending it was refused, so the next mint for its user retries. */
  endFailed: boolean
  /**
   * The end of it in flight, if one is: shared by anything else that would
   * end it, and waited for by a mint for its user.
   */
  ending: Promise<void> | undefined
}

/**
 * A connection to Memco Shared Memory.
 *
 * Constructing one resolves settings and opens a channel but sends nothing.
 * {@link Memco.connect} runs the two checks that prove the connection works —
 * and, with a token, teaches the client the limits the service enforces on the
 * way, which is what lets later calls refuse an over-long field before
 * spending a round trip on it. Nothing else runs them: a client is not
 * awaitable, so `await using client = new Memco(...)` opens no connection at
 * all — the `await` in the example below belongs to {@link Memco.connect}, and
 * `using` only closes.
 *
 * A client built from an API client's `clientId` and `clientSecret` exchanges
 * them for a token, and renews it before it expires. Such a token carries no
 * content role of its own, so the service refuses memory calls made with it:
 * that client administers {@link Memco.networks} and {@link Memco.users}, and
 * reaches memory through sessions acting for your users —
 * {@link MemoryOperations.startSession} with an `externalId`.
 *
 * @example
 * ```ts
 * import { Memco } from '@memco/memcoai'
 *
 * await using client = await new Memco().connect()  // reads MEMCO_API_TOKEN
 * const { domains } = await client.memory.listDomains()
 * ```
 */
export class Memco {
  /** The memory operations. */
  readonly memory: MemoryOperations

  /**
   * The network administration: networks, their members and their groups.
   * Needs an API client's credentials.
   */
  readonly networks: NetworkOperations

  /**
   * The user administration: external users and their API keys. Needs an API
   * client's credentials.
   */
  readonly users: UserOperations

  private readonly config: ClientConfig
  private readonly transport: Transport
  private closed = false
  private closing: Promise<void> | undefined
  private inflight = 0
  private drained: (() => void)[] = []

  /**
   * The client's own credential, leased by every call not made for a session
   * acting as one of your users. A token is never due; an issued one is renewed
   * through {@link Memco.issue}. Neither has an end: nothing in the contract
   * revokes an issued token.
   */
  private readonly credential: Renewing

  /** The impersonation keys minted and not yet ended, by key id. */
  private readonly live = new Map<string, LiveKey>()

  /**
   * The session credentials garbage-collected since the last call, by their
   * owner tokens: their keys are ended by the next call or mint this client
   * makes. Never by the collection itself, which must not send anything.
   */
  private readonly dropped: object[] = []

  private readonly abandoned = new FinalizationRegistry<object>(owner => {
    // Once closed, the client's close has ended every key already.
    if (!this.closed) {
      this.dropped.push(owner)
    }
  })

  /**
   * @param options Credential, endpoint and per-call deadline, each falling
   *   back to the environment where one supplies it and then to a built-in
   *   default.
   * @throws MemcoConfigError If no credential is available, if a token is
   *   passed beside client credentials, if only half of the client pair is
   *   available, if `tokenLifetime` is given without client credentials or is
   *   not a positive whole number, if the host is blank or carries an invalid
   *   port, if `MEMCO_API_TLS` is neither `true` nor `false`, or if the timeout
   *   is not positive.
   */
  constructor(options: MemcoOptions = {}) {
    if (options.logLevel !== undefined) {
      setLevel(options.logLevel)
    }
    this.config = resolve(options)
    if (!this.config.tls) {
      // Said at construction, before anything is sent, and at WARNING so it
      // shows by default: the credential crosses the wire readable.
      log.warning(
        'TLS is off: %s is dialled in plaintext, credentials included',
        this.config.target
      )
    }
    // Opened here rather than lazily. Constructing a channel dials nothing —
    // grpc-js connects on first use — so this is still a constructor that
    // performs no I/O.
    this.transport = buildTransport(this.config)
    if (this.config.clientId === null) {
      const token = new Minted(this.config.token, Number.POSITIVE_INFINITY)
      this.credential = new Renewing(async () => token)
    } else {
      this.credential = new Renewing(() => this.issue())
    }
    this.memory = new MemoryOperations(
      this.transport.memory,
      this.call,
      this.impersonate
    )
    this.networks = new NetworkOperations(this.transport.admin, this.call)
    this.users = new UserOperations(this.transport.admin, this.call)
  }

  /**
   * Verify the connection.
   *
   * Probes the health endpoint, then proves the credential. With a token that
   * is {@link MemoryOperations.listDomains}, which also records the limits the
   * service enforces. With client credentials it is the exchange for a token,
   * and no memory method is called, since an issued token carries no content
   * role. Safe to call more than once; it repeats both, except that a token
   * already issued is reused until it is due for renewal.
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
      if (this.config.clientId === null) {
        // The result is discarded: what is worth keeping — the limits and the
        // per-domain tag cap — is retained by the call itself.
        await this.memory.listDomains()
      } else {
        // The exchange is the proof. A memory call would be refused whatever
        // the credentials, since an issued token carries no content role.
        // Counted, so a close made meanwhile waits for it.
        await this.counted(() => this.credential.lease(async () => undefined))
      }
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
   * Close the client.
   *
   * Waits for calls already in flight rather than cancelling them, then ends
   * the keys of sessions acting for your users that are still live — no key is
   * ended under a call still using it — and then releases the connection. Safe
   * to call more than once, and never throws: a key that cannot be ended is
   * logged by its id at `WARNING`, and expires on its own. Any call made
   * afterwards throws {@link MemcoConfigError}, and closing a session changes
   * nothing.
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
    const now = clock.monotonic()
    // Expired keys are dropped rather than ended: the service has let them go
    // already, and ending each would cost a round trip for nothing.
    const live = [...this.live].filter(([, key]) => key.expiresAt > now)
    this.live.clear()
    await this.endLive(live)
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

  /**
   * The epoch deadline in milliseconds for a call made now.
   *
   * @param timeout The call's own deadline in seconds, or `undefined` for the
   *   client's.
   * @returns When the call must be answered by.
   * @throws MemcoConfigError If a deadline was given but is not a positive,
   *   finite number of seconds.
   */
  private deadlineAt(timeout?: number): number {
    return Date.now() + deadline(timeout, this.config.timeout) * 1000
  }

  /**
   * Run work counted as in flight, so {@link Memco.close} waits for it.
   *
   * Everything that changes the registry of live keys runs inside one of these
   * together with the call it follows from, so a close can never find a key
   * between being ended and being forgotten — and end it a second time.
   *
   * @param work What to run.
   * @returns What `work` returned.
   * @throws MemcoConfigError If the client has been closed.
   */
  private async counted<T>(work: () => Promise<T>): Promise<T> {
    this.ensureOpen()
    this.inflight += 1
    try {
      return await work()
    } finally {
      this.finish()
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
        { deadline: this.deadlineAt() },
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

  /**
   * Exchange the API client's credentials for a token.
   *
   * Sent directly rather than through {@link Memco.call}: it runs while the
   * client's own credential is minting, and it is the one call that carries no
   * bearer, since the credentials in its request are what authenticate it.
   *
   * @returns The token, due for renewal at {@link RENEW_AFTER} of its
   *   lifetime and expiring at the end of it. The lifetime is counted from
   *   before the request was sent, so both can only fall early, never late.
   * @throws MemcoAPIError If the exchange is refused or fails.
   */
  private async issue(): Promise<Minted> {
    const issued = clock.monotonic()
    const response = await this.send(
      this.transport.tokens.issueToken.bind(this.transport.tokens),
      requests.issueTokenRequest(this.config),
      this.deadlineAt(),
      'IssueToken',
      undefined
    )
    return new Minted(
      response.accessToken,
      issued + RENEW_AFTER * response.expiresIn * 1000,
      '',
      issued + response.expiresIn * 1000
    )
  }

  /**
   * Hold the key a session acting for one external user sends.
   *
   * An arrow property, so the memory operations can hold it without binding it.
   * The credential is watched for collection: a session dropped without
   * closing still has its key ended, by the next call this client makes.
   *
   * @param externalId The user the session acts as.
   * @returns The session's credential, which mints its first key on first use.
   */
  private readonly impersonate = (externalId: string): Renewing => {
    // A token standing for the credential in the registry of live keys, which
    // must not keep the credential itself alive.
    const owner = {}
    const key = new Renewing(
      () => this.mintKey(externalId, owner),
      minted => this.endKey(externalId, minted.keyId)
    )
    this.abandoned.register(key, owner)
    return key
  }

  /**
   * Mint a key acting as an external user, under the client's own credential.
   *
   * First ends what should no longer be live — the keys of dropped sessions,
   * and this user's keys whose end failed earlier — and waits for every end of
   * this user's keys in flight, whoever started it, so the service's cap on
   * live keys per user counts only keys still in use when the mint reaches it.
   * The key is registered in the same in-flight window as the call that mints
   * it, so closing the client ends it even if the session it was minted for
   * never is.
   *
   * @param externalId The user the key acts as.
   * @param owner The token standing for the session credential it is minted for.
   * @returns The key, due for renewal at {@link RENEW_AFTER} of its lifetime
   *   and expiring at the end of it. The lifetime is the service's own
   *   `expiresIn`, counted on the monotonic clock from before the request was
   *   sent, so no clock skew can move it; a service that does not send it yet
   *   has `expiresAt` read against this machine's wall clock instead.
   * @throws MemcoAPIError If the service refuses to mint it.
   * @throws MemcoConfigError If, without `expiresIn`, the key arrives already
   *   expired by this machine's clock, which is then too far ahead of the
   *   service's to hold one. The key is ended first.
   */
  private mintKey(externalId: string, owner: object): Promise<Minted> {
    return this.counted(async () => {
      const now = clock.monotonic()
      for (const [keyId, key] of this.live) {
        if (key.expiresAt <= now) {
          this.live.delete(keyId)
        }
      }
      // Other users' dropped keys are ended without this mint waiting on them.
      void this.endDropped()
      await Promise.all(
        [...this.live]
          .filter(
            ([, key]) =>
              key.externalId === externalId &&
              (key.ending !== undefined || key.endFailed)
          )
          .map(([keyId]) => this.endKey(externalId, keyId))
      )
      // Taken before the request, so the lifetime counted from it can only
      // make the key's expiry and renewal point fall early, never late.
      const asked = clock.monotonic()
      const key = await this.call(
        this.transport.admin.impersonateExternalUser.bind(this.transport.admin),
        requests.impersonateRequest(externalId),
        undefined,
        'ImpersonateExternalUser'
      )
      if (key.expiresIn > 0) {
        // The service's own count of what the key has left, which needs no
        // clock of this machine's but the monotonic one.
        return this.register(
          externalId,
          owner,
          key,
          asked,
          key.expiresIn * 1000
        )
      }
      // A service that does not send expiresIn yet: `expiresAt` is absolute
      // Unix time, converted once, on receipt, by this machine's wall clock.
      const left = key.expiresAt * 1000 - clock.now()
      if (left <= 0) {
        // Only this machine's clock can make a key the service has just issued
        // read as expired. Used anyway, every call would mint another, and
        // closing the client would skip the last as expired; so it is ended,
        // and the clock named.
        await this.sendEnd(externalId, key.keyId, undefined)
        throw new MemcoConfigError(
          `impersonation key ${key.keyId} for ${JSON.stringify(externalId)} ` +
            `arrived already expired by this machine's clock, ` +
            `${Math.ceil(-left / 1000)}s past its expiry: the local clock is ` +
            "ahead of the service's by at least the key's lifetime. Correct " +
            'the clock, for example with NTP.'
        )
      }
      return this.register(externalId, owner, key, clock.monotonic(), left)
    })
  }

  /**
   * Record a key just minted as live, and hold it for its session.
   *
   * @param externalId The user the key acts as.
   * @param owner The token standing for the session credential it is for.
   * @param key The key, as the service issued it.
   * @param from The monotonic reading its lifetime is counted from.
   * @param lifetime Its lifetime in milliseconds, counted from `from`.
   * @returns The key, due for renewal at {@link RENEW_AFTER} of its lifetime
   *   and expiring at the end of it; the registry holds the same expiry.
   */
  private register(
    externalId: string,
    owner: object,
    key: pb.admin.ImpersonationKey,
    from: number,
    lifetime: number
  ): Minted {
    this.live.set(key.keyId, {
      externalId,
      expiresAt: from + lifetime,
      owner,
      endFailed: false,
      ending: undefined
    })
    return new Minted(
      key.value,
      from + RENEW_AFTER * lifetime,
      key.keyId,
      from + lifetime
    )
  }

  /**
   * End a key a session has renewed, closed or dropped. Never throws.
   *
   * Sent under the client's own credential and the client's own deadline,
   * whatever the call that set it off was given: the end belongs to the key's
   * holder, and cutting it short would strand a live key against the user's
   * cap. The registry is updated in the same in-flight window as the call.
   *
   * @param externalId The user the key acts as.
   * @param keyId The key to end.
   * @returns When the key is ended, or its end has failed. One already being
   *   ended — by a retry, a dropped session's end or a renewal — is not ended
   *   again: its end in flight is returned instead.
   */
  private endKey(externalId: string, keyId: string): Promise<void> {
    const key = this.live.get(keyId)
    if (key?.ending !== undefined) {
      return key.ending
    }
    const ending = this.sendEnd(externalId, keyId, key)
    if (key !== undefined) {
      key.ending = ending
    }
    return ending
  }

  /**
   * Send the end of one key, and record what became of it. Never throws.
   *
   * @param externalId The user the key acts as.
   * @param keyId The key to end.
   * @param key Its entry in the registry, if it has one.
   */
  private async sendEnd(
    externalId: string,
    keyId: string,
    key: LiveKey | undefined
  ): Promise<void> {
    let failure: unknown
    try {
      await this.counted(async () => {
        try {
          await this.call(
            this.transport.admin.endImpersonation.bind(this.transport.admin),
            requests.endImpersonationRequest(externalId, keyId),
            undefined,
            'EndImpersonation'
          )
        } catch (error) {
          // NOT_FOUND is a key already ended, or expired: nothing is left.
          if (!(error instanceof MemcoNotFoundError)) {
            if (key !== undefined) {
              key.ending = undefined
              key.endFailed = true
            }
            failure = error
            return
          }
        }
        this.live.delete(keyId)
      })
    } catch {
      // Refused because the client is closing, whose close ends what is left.
      if (key !== undefined) {
        key.ending = undefined
      }
      return
    }
    if (failure !== undefined) {
      // The key id revokes nothing, so it is safe to log; the value never is.
      log.warning(
        'could not end impersonation key %s; it is tried again before the ' +
          'next session for its user, and when the client closes: %s',
        keyId,
        failure
      )
    }
  }

  /**
   * End the keys of sessions garbage-collected without being closed.
   *
   * Taken off the queue at once, so a call this sets off does not set it off
   * again.
   */
  private async endDropped(): Promise<void> {
    if (this.dropped.length === 0) {
      return
    }
    const owners = new Set(this.dropped.splice(0))
    await Promise.all(
      [...this.live]
        .filter(([, key]) => owners.has(key.owner))
        .map(([keyId, key]) => this.endKey(key.externalId, keyId))
    )
  }

  /**
   * End the keys still live as the client closes. Never throws.
   *
   * Stops at the first failure: one refusal says the service cannot end them
   * now, and asking again for each would only make closing slower. The keys
   * left expire on their own.
   *
   * @param live The keys to end, by key id.
   */
  private async endLive(live: [string, LiveKey][]): Promise<void> {
    for (const [index, [keyId, key]] of live.entries()) {
      try {
        // Sent directly: `call` refuses everything once the client is closed,
        // and nothing else can be in flight by now.
        await this.credential.lease(bearer =>
          this.send(
            this.transport.admin.endImpersonation.bind(this.transport.admin),
            requests.endImpersonationRequest(key.externalId, keyId),
            this.deadlineAt(),
            'EndImpersonation',
            bearer
          )
        )
      } catch (error) {
        if (error instanceof MemcoNotFoundError) {
          continue
        }
        log.warning(
          'could not end impersonation keys %s; they expire on their own: %s',
          live
            .slice(index)
            .map(([left]) => left)
            .join(', '),
          error
        )
        return
      }
    }
  }

  /**
   * Invoke one RPC under a leased credential, translating any failure into a
   * typed error.
   *
   * The lease is taken inside the in-flight count, so a token renewal or a key
   * renewal it sets off is waited for by {@link Memco.close}, and its failure
   * reaches the caller typed like any other. The deadline is taken when the
   * call is made, so time spent waiting for a credential counts against it.
   *
   * An arrow property rather than a method, so the operations can hold it
   * without binding it.
   */
  private readonly call: Caller = (invoke, request, timeout, rpc, credential) =>
    this.counted(() => {
      const until = this.deadlineAt(timeout)
      if (this.dropped.length > 0) {
        // Not waited for: the keys of sessions dropped elsewhere are no
        // business of this call's. Counted all the same, so a close waits.
        void this.endDropped()
      }
      return (credential ?? this.credential).lease(
        bearer => this.send(invoke, request, until, rpc, bearer),
        until
      )
    })

  /**
   * Send one RPC carrying one credential, translating any failure.
   *
   * @param invoke The generated client's method.
   * @param request The request message.
   * @param until The call's epoch deadline in milliseconds.
   * @param rpc The method's name, for the log record.
   * @param bearer The credential to send as the call's only one, or
   *   `undefined` for a call that takes none.
   * @returns The response.
   * @throws MemcoAPIError If the service returned an error status.
   */
  private async send<Request, Response>(
    invoke: Invoke<Request, Response>,
    request: Request,
    until: number,
    rpc: string,
    bearer: Minted | undefined
  ): Promise<Response> {
    // Built fresh, never spread from anything a caller supplied.
    const options = { deadline: until }
    const started = performance.now()
    try {
      const response = await unary(invoke, request, options, bearer)
      log.debug('%s ok in %sms', rpc, elapsed(started))
      return response
    } catch (error) {
      const name = error instanceof Error ? error.name : typeof error
      log.debug('%s failed in %sms: %s', rpc, elapsed(started), name)
      throw error
    }
  }
}

/**
 * Await one unary call.
 *
 * @param invoke The generated client's method.
 * @param request The request message.
 * @param options Call options carrying the deadline.
 * @param bearer The credential to send, or `undefined` to send none. Read only
 *   here, as the metadata is built, so no other frame holds its value.
 * @returns The response.
 * @throws MemcoAPIError Translated once, here, so the caller never sees a raw
 *   transport error and the log record names what they will catch.
 */
function unary<Request, Response>(
  invoke: Invoke<Request, Response>,
  request: Request,
  options: Partial<CallOptions>,
  bearer: Minted | undefined
): Promise<Response> {
  return new Promise<Response>((settle, fail) => {
    const sent = bearer === undefined ? new Metadata() : metadata(bearer.value)
    invoke(request, sent, options, (error, value) => {
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
