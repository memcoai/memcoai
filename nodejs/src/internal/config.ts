/**
 * Resolution of the client's credential, endpoint and call defaults.
 *
 * Arguments always win over the environment, which always wins over the
 * built-in defaults. Everything a caller can get wrong is refused here, before
 * a socket is opened, and named in the message: a misparsed host dials an
 * address nobody asked for and comes back as a name-resolution failure, which
 * is a much longer way round to the same mistake.
 */

import { inspect } from 'node:util'

import { MemcoConfigError } from '../errors.js'
import { ROOT, getLogger } from './logging.js'

const log = getLogger(`${ROOT}.config`)

/** Endpoint used when neither an argument nor `MEMCO_API_HOST` supplies one. */
export const DEFAULT_HOST = 'grpc.spark.memco.ai'

/** Port used when the host does not carry one of its own. */
export const DEFAULT_PORT = 443

/**
 * Per-call deadline in seconds, used when a method is given no timeout.
 *
 * Seconds, which is the unit a caller thinks in. `@grpc/grpc-js` takes
 * deadlines in milliseconds, so the conversion belongs at the call site that
 * builds `CallOptions`, not here.
 */
export const DEFAULT_TIMEOUT = 30

/** Environment variable holding the credential. */
export const TOKEN_ENV = 'MEMCO_API_TOKEN'

/** Deprecated predecessor of {@link TOKEN_ENV}, still honoured with a warning. */
export const LEGACY_TOKEN_ENV = 'MEMCO_API_KEY'

/** Environment variable overriding {@link DEFAULT_HOST}. */
export const HOST_ENV = 'MEMCO_API_HOST'

/**
 * An environment to read settings from.
 *
 * Shaped like `process.env`, so a caller can pass that, a filtered copy of it,
 * or a literal in a test.
 */
export type Environment = Readonly<Record<string, string | undefined>>

/** The settings of {@link ClientConfig} that may safely be rendered. */
export interface ConfigSummary {
  /** Hostname of the service, without a port. */
  host: string
  /** TCP port of the service. */
  port: number
  /** Whether the channel dials over TLS. */
  tls: boolean
  /** Default per-call deadline in seconds. */
  timeout: number
}

/**
 * Fully resolved settings for one client.
 *
 * The credential is deliberately hard to render. It is stored as a
 * non-enumerable property and omitted from both {@link toJSON} and the
 * `util.inspect` hook, so it survives none of `console.log`, `console.dir`,
 * `JSON.stringify`, a spread, a `structuredClone`, or a `util.inspect` with
 * `showHidden`. Error trackers such as Sentry capture local variables by
 * default, and this object is live while the channel is being dialled, which is
 * precisely when a crash report is most likely to be taken.
 *
 * What it does not survive is deliberate reflection — reading
 * `Object.getOwnPropertyDescriptors`, or inspecting with `customInspect: false`
 * as well as `showHidden`. That is where the boundary has to sit: the SDK
 * itself has to read the token back out to send it.
 */
export class ClientConfig {
  /**
   * The credential sent as `authorization: Bearer <token>`.
   *
   * Either a Memco API key or a session token issued for the account; the
   * service accepts both in the same header. Declared rather than defined, so
   * the constructor's `Object.defineProperty` is what creates it — a normal
   * field declaration would emit an enumerable one and undo the hiding.
   */
  declare readonly token: string

  /** Hostname of the service, without a port. */
  readonly host: string

  /** TCP port of the service. */
  readonly port: number

  /** Whether to dial over TLS using the system trust store. */
  readonly tls: boolean

  /** Default per-call deadline in seconds. */
  readonly timeout: number

  /**
   * @param token The credential, hidden from every rendering of this object.
   * @param host Hostname without a port, and without IPv6 brackets.
   * @param port TCP port.
   * @param tls Whether to dial over TLS.
   * @param timeout Default per-call deadline in seconds.
   */
  constructor(
    token: string,
    host: string,
    port: number,
    tls: boolean,
    timeout: number
  ) {
    this.host = host
    this.port = port
    this.tls = tls
    this.timeout = timeout
    Object.defineProperty(this, 'token', {
      value: token,
      enumerable: false,
      writable: false,
      configurable: false
    })
  }

  /**
   * The `host:port` string to dial.
   *
   * An IPv6 literal is re-bracketed. {@link host} holds the bare address, so
   * joining it to the port with a colon would produce something no resolver can
   * parse, and the failure would be reported as a name-resolution error rather
   * than as the malformed address it is.
   */
  get target(): string {
    const host = this.host.includes(':') ? `[${this.host}]` : this.host
    return `${host}:${this.port}`
  }

  /**
   * The settings without the credential, for `JSON.stringify`.
   *
   * @returns Everything but the token.
   */
  toJSON(): ConfigSummary {
    return {
      host: this.host,
      port: this.port,
      tls: this.tls,
      timeout: this.timeout
    }
  }

  /**
   * The settings without the credential, for `util.inspect` and `console.log`.
   *
   * @returns A rendering that omits the token even under `showHidden`.
   */
  [inspect.custom](): string {
    return `ClientConfig ${inspect(this.toJSON())}`
  }
}

// Node's `process.emitWarning` does not deduplicate, so the once-per-process
// rule is kept here. Once is what a caller can act on: the remedy is renaming
// one environment variable, and repeating the notice per client construction is
// noise they have no way to switch off.
let legacyWarned = false

/**
 * Refuse a timeout that is not a finite number of seconds.
 *
 * Kept apart from the positive check because NaN and Infinity are not
 * usefully described as "not positive", and because what each of them does is
 * specific to grpc-js. A NaN deadline makes it throw from a promise
 * continuation this SDK does not own, so it joins no caller's chain, reaches no
 * `catch`, and takes the process down. An infinite one makes it omit the
 * deadline header altogether, so the call never settles and `close()` waits for
 * it for ever.
 *
 * @param timeout The seconds the caller asked for.
 * @throws MemcoConfigError If it is NaN or infinite.
 */
function checkFinite(timeout: number): void {
  if (!Number.isFinite(timeout)) {
    throw new MemcoConfigError(
      `timeout must be a finite number of seconds, got ${timeout}`
    )
  }
}

function resolveToken(token: string | undefined, env: Environment): string {
  // `!= null` rather than `!== undefined`: a JavaScript caller has no compiler
  // to stop them passing null, and a null means "use the environment" rather
  // than a blank argument. Reading .trim() off it would throw a bare TypeError
  // and break the guarantee that every failure here is a MemcoError.
  if (token != null && typeof token !== 'string') {
    throw new MemcoConfigError(
      `the token passed to the client must be a string, got ${typeof token}`
    )
  }
  if (token != null && token.trim() !== '') {
    log.debug('credential taken from the token argument')
    return token.trim()
  }
  if (token != null) {
    // A blank argument is a caller bug, not a request for the environment.
    // Falling through would authenticate as somebody they never named.
    throw new MemcoConfigError(
      `the token passed to the client is blank: pass a real token or set ${TOKEN_ENV}`
    )
  }

  const fromEnv = (env[TOKEN_ENV] ?? '').trim()
  if (fromEnv !== '') {
    log.debug('credential taken from %s', TOKEN_ENV)
    return fromEnv
  }

  const legacy = (env[LEGACY_TOKEN_ENV] ?? '').trim()
  if (legacy !== '') {
    if (!legacyWarned) {
      legacyWarned = true
      process.emitWarning(
        `${LEGACY_TOKEN_ENV} is deprecated and will be removed in a future release; ` +
          `rename it to ${TOKEN_ENV}.`,
        'DeprecationWarning'
      )
    }
    log.debug('credential taken from %s', LEGACY_TOKEN_ENV)
    return legacy
  }

  throw new MemcoConfigError(`no API token: pass token=... or set ${TOKEN_ENV}`)
}

function checkSendable(token: string): void {
  // `@grpc/grpc-js` rejects a metadata value outside printable ASCII by
  // throwing an Error that quotes the value — so a token carrying a stray
  // newline, a zero-width space or a smart quote from a copy-paste would put
  // the whole credential into a message the application then logs. Checking
  // first is what keeps that error from ever being raised.
  //
  // The position is named and the character is not: an index is diagnostic
  // enough to find the stray byte and reveals nothing about the credential.
  const offending = [...token].findIndex(
    character => !/^[\x20-\x7e]$/.test(character)
  )
  if (offending !== -1) {
    throw new MemcoConfigError(
      'the token cannot be sent as an HTTP header: it must be printable ' +
        `ASCII, and the character at index ${offending} is not`
    )
  }
}

// Every host below is rendered with JSON.stringify, which double-quotes it and
// escapes anything in it that would otherwise garble the message.
function parsePort(text: string, host: string): number {
  // Checked as a shape first: Number() would happily read "5e3", "0x10", " "
  // and "" as numbers, and a port that arrived as one of those is a mistake
  // worth naming rather than a value worth honouring. The pattern refuses
  // "50_051" and non-ASCII digits along with them, which is the safer
  // direction for a value that decides where the credential is sent.
  const trimmed = text.trim()
  if (!/^[+-]?\d+$/.test(trimmed)) {
    throw new MemcoConfigError(
      `host ${JSON.stringify(host)} has an unparseable port: ` +
        `${JSON.stringify(text)} is not an integer`
    )
  }
  // BigInt rather than Number: the message names the port back to the caller,
  // and Number rounds anything past 2^53, so "99999999999999999999" would be
  // reported as the different number 100000000000000000000.
  const port = BigInt(trimmed)
  if (port < 1n || port > 65535n) {
    throw new MemcoConfigError(
      `host ${JSON.stringify(host)} has a port outside the range 1-65535: ${port}`
    )
  }
  return Number(port)
}

/**
 * Split an optional `host:port` string into its parts.
 *
 * Handles IPv6 in both forms. A bracketed literal is the RFC 3986 spelling and
 * is the only one that can carry a port; a bare literal is accepted too, since
 * splitting it on its last colon would otherwise yield a nonsense host and port
 * rather than an error.
 *
 * @param host A hostname or IP address, optionally carrying an explicit port.
 * @returns The host without brackets, and the port, defaulting to
 *   {@link DEFAULT_PORT}.
 * @throws MemcoConfigError If the brackets are unbalanced, if text follows the
 *   closing bracket without a port, or if a port is present but is not an
 *   integer in 1-65535.
 */
function splitHostPort(host: string): [string, number] {
  if (host.startsWith('[')) {
    const closing = host.indexOf(']')
    if (closing === -1) {
      throw new MemcoConfigError(
        `host ${JSON.stringify(host)} opens a bracket that is never closed`
      )
    }
    const name = host.slice(1, closing)
    const rest = host.slice(closing + 1)
    if (name === '') {
      throw new MemcoConfigError(
        `host ${JSON.stringify(host)} has no address inside its brackets`
      )
    }
    if (rest === '') {
      return [name, DEFAULT_PORT]
    }
    if (!rest.startsWith(':')) {
      throw new MemcoConfigError(
        `host ${JSON.stringify(host)} has unexpected text after the closing bracket`
      )
    }
    return [name, parsePort(rest.slice(1), host)]
  }

  // More than one colon and no brackets: a bare IPv6 literal, which cannot
  // carry a port. Splitting it would silently produce a wrong host and port.
  if (host.split(':').length - 1 > 1) {
    return [host, DEFAULT_PORT]
  }

  const separator = host.lastIndexOf(':')
  if (separator === -1) {
    return [host, DEFAULT_PORT]
  }
  const name = host.slice(0, separator)
  if (name.trim() === '') {
    // ":50051" is what `${process.env.MY_HOST ?? ''}:${port}` produces. Left
    // alone it dials an empty host and surfaces as a retryable transport
    // failure rather than the configuration error it is.
    throw new MemcoConfigError(
      `host ${JSON.stringify(host)} has a port but no hostname`
    )
  }
  return [name, parsePort(host.slice(separator + 1), host)]
}

/** What {@link resolve} accepts. Every field falls back to the environment. */
export interface ResolveOptions {
  /**
   * Credential to authenticate with. When omitted, `MEMCO_API_TOKEN` is used,
   * falling back to the deprecated `MEMCO_API_KEY` with a warning. A blank
   * value is refused rather than treated as absent.
   */
  token?: string
  /**
   * Service endpoint, optionally including a port such as `localhost:50051` or
   * `[2001:db8::1]:50051`. When omitted, `MEMCO_API_HOST` is used, falling back
   * to {@link DEFAULT_HOST}. A host without a port gets {@link DEFAULT_PORT}. A
   * blank value is refused rather than treated as absent.
   */
  host?: string
  /**
   * Whether to dial over TLS with the system trust store. Set to `false` only
   * for a plaintext endpoint, such as a local server.
   */
  tls?: boolean
  /** Default per-call deadline in seconds. */
  timeout?: number
  /**
   * Environment to read from. Defaults to `process.env`; supplying one is
   * mainly useful in tests.
   */
  env?: Environment
}

/**
 * Resolve client settings from arguments and the environment.
 *
 * @param options The caller's settings, each falling back to the environment
 *   and then to the built-in default.
 * @returns The resolved configuration.
 * @throws MemcoConfigError If no credential is available, if the host is blank
 *   or carries an invalid port, or if the timeout is not positive.
 *
 * @example
 * ```ts
 * resolve({ token: 'sk-...', host: 'localhost:50051', tls: false }).target
 * // 'localhost:50051'
 * ```
 */
export function resolve(options: ResolveOptions = {}): ClientConfig {
  const {
    token,
    host,
    tls = true,
    timeout = DEFAULT_TIMEOUT,
    env = process.env
  } = options

  // Before the credential, deliberately: a caller who passed a bad timeout and
  // no token should be told about the timeout, which is the thing they got
  // wrong rather than the thing they left to the environment.
  checkFinite(timeout)
  if (timeout <= 0) {
    throw new MemcoConfigError(`timeout must be positive, got ${timeout}`)
  }

  const resolvedToken = resolveToken(token, env)
  checkSendable(resolvedToken)

  // `!= null` for the same reason as the token above, and a type check for the
  // same one again: coercing would be worse than refusing here, since
  // String(12345) is a perfectly plausible-looking hostname.
  if (host != null && typeof host !== 'string') {
    throw new MemcoConfigError(
      `the host passed to the client must be a string, got ${typeof host}`
    )
  }
  if (host != null && host.trim() === '') {
    throw new MemcoConfigError(
      `the host passed to the client is blank: pass a real host or set ${HOST_ENV}`
    )
  }
  const fromEnv = (env[HOST_ENV] ?? '').trim()
  const resolvedHost = (
    host ?? (fromEnv !== '' ? fromEnv : DEFAULT_HOST)
  ).trim()
  const [name, port] = splitHostPort(resolvedHost)

  const config = new ClientConfig(resolvedToken, name, port, tls, timeout)
  log.debug(
    'endpoint %s tls=%s (host from %s)',
    config.target,
    tls,
    host !== undefined
      ? 'the host argument'
      : fromEnv !== ''
        ? HOST_ENV
        : 'the default'
  )
  return config
}

/**
 * Resolve the deadline for one call.
 *
 * @param timeout The caller's per-call deadline in seconds, or `undefined` to
 *   use the client's default.
 * @param fallback The client's default deadline in seconds.
 * @returns The deadline in seconds.
 * @throws MemcoConfigError If a deadline was given but is not positive. A zero
 *   or negative deadline is silently useless — the call expires before it is
 *   sent — so it is refused rather than substituted.
 */
export function deadline(
  timeout: number | undefined,
  fallback: number
): number {
  if (timeout === undefined) {
    return fallback
  }
  checkFinite(timeout)
  if (timeout <= 0) {
    throw new MemcoConfigError(`timeout must be positive, got ${timeout}`)
  }
  return timeout
}
