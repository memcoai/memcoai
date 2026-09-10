/**
 * The exception hierarchy.
 *
 * Every failure a caller can see is one of these. No raw transport error ever
 * reaches them: {@link fromServiceError} translates whatever gRPC raised into
 * the class that says what happened, once, at the boundary.
 *
 * Two properties are worth knowing about.
 *
 * `name` carries the class name and is the discriminator to prefer over
 * `instanceof`. This package publishes an ESM build and a CommonJS build, and a
 * consumer that somehow loads both holds two distinct copies of every class, so
 * `error instanceof MemcoNotFoundError` can be false for an error that is one.
 * `error.name === 'MemcoNotFoundError'` cannot.
 *
 * `message` is formatted `"<CODE_NAME>: <details>"`, so the code and the
 * service's own wording both read straight out of a log line.
 */

import { status, type ServiceError } from '@grpc/grpc-js'

import { errorDetails, STATUS_DETAILS_KEY } from './internal/wire.js'

/**
 * Base of every error this SDK raises.
 *
 * Catch this to catch everything the SDK can produce, and nothing it cannot.
 */
export class MemcoError extends Error {
  constructor(message: string) {
    super(message)
    this.name = new.target.name
    Error.captureStackTrace?.(this, new.target)
  }
}

/**
 * The client was asked for something it cannot do before reaching the service.
 *
 * A missing credential, an unparseable host, a non-positive timeout, a log
 * level that is not one, a call on a closed client. Nothing was sent.
 */
export class MemcoConfigError extends MemcoError {}

/**
 * Base of every failure the service reported.
 *
 * @see {@link fromServiceError} for how a gRPC status becomes one of these.
 */
export class MemcoAPIError extends MemcoError {
  /** The gRPC status code, as `@grpc/grpc-js`'s numeric `status` enum. */
  readonly code: status

  /** The status code's name, such as `UNAUTHENTICATED`. */
  readonly codeName: string

  /** The service's own description of the failure, without the code prefix. */
  readonly detail: string

  /** gRPC's internal diagnostic string, when the call carried one. */
  readonly debugErrorString: string | undefined

  constructor(code: status, detail: string, debugErrorString?: string) {
    const codeName = status[code] ?? `UNRECOGNIZED(${code})`
    super(`${codeName}: ${detail}`)
    this.code = code
    this.codeName = codeName
    this.detail = detail
    this.debugErrorString = debugErrorString
  }
}

/**
 * The credential was missing, malformed or rejected.
 *
 * Never worth retrying: the same credential will be refused again. Check that
 * `MEMCO_API_TOKEN`, or the `token` given to {@link MemcoOptions}, is present
 * and still current. {@link Memco.connect} lists domains, and that call carries
 * the credential, so a stale one fails there rather than on the first search.
 *
 * Prefer `error.name === 'MemcoAuthenticationError'` to `instanceof`, for the
 * reason this module's header gives.
 */
export class MemcoAuthenticationError extends MemcoAPIError {}

/**
 * The credential is valid but does not reach what was asked for.
 *
 * The request is well formed and the credential is genuine, so neither a retry
 * nor a different argument changes the answer: the credential itself has to be
 * granted what it was refused. Distinct from
 * {@link MemcoAuthenticationError}, which says it was not accepted at all.
 */
export class MemcoPermissionError extends MemcoAPIError {}

/**
 * The request was refused as malformed.
 *
 * Raised by the service, and raised locally by the SDK's own structural checks
 * — deliberately indistinguishable, because the remedy is the same.
 *
 * A local refusal usually means nothing was sent. The exception is
 * {@link MemoryOperations.importMemories}, which validates a batch one group at
 * a time and sends each before taking the next, so a bad entry late in a long
 * batch is refused after the groups before it have already been written.
 *
 * Never worth retrying: the same request will be refused again. `detail` says
 * what was wrong with it, and fixing the call is the only way past this.
 *
 * Prefer `error.name === 'MemcoInvalidRequestError'` to `instanceof`, for the
 * reason this module's header gives.
 */
export class MemcoInvalidRequestError extends MemcoAPIError {}

/**
 * Nothing answers to the handle that was given.
 *
 * Sending the same handle again will not find it. An idx or an operation id
 * comes back on a response and cannot be constructed by hand, so one that was
 * invented or edited lands here.
 *
 * Not every absence arrives as an error:
 * {@link MemoryOperations.revertMemory} reports an operation id that answers to
 * nothing as a successful call carrying {@link RevertOutcome.NOT_FOUND}.
 *
 * Prefer `error.name === 'MemcoNotFoundError'` to `instanceof`, for the reason
 * this module's header gives.
 */
export class MemcoNotFoundError extends MemcoAPIError {}

/**
 * The service refused because the caller's state does not allow the call.
 *
 * Billing, account standing, terms — something outside the request has to
 * change, so neither a retry nor a different argument gets past it. `detail` is
 * the service's own account of what that is.
 *
 * {@link MemcoSunsetError} extends this class, so an `instanceof` chain testing
 * this one first never reaches it, and comparing `name` against
 * `'MemcoPreconditionFailedError'` never matches it at all. Test for the sunset
 * explicitly, either way.
 */
export class MemcoPreconditionFailedError extends MemcoAPIError {}

/** Which version reached its end of life. */
export const SunsetKind = {
  /** This SDK build is too old for the service. Upgrade the package. */
  CLIENT_VERSION: 'client_version',
  /** The API version this SDK speaks has been withdrawn. */
  API_VERSION: 'api_version'
} as const

/** One of {@link SunsetKind}'s values. */
export type SunsetKind = (typeof SunsetKind)[keyof typeof SunsetKind]

/**
 * The call was refused because a version it depends on has been withdrawn.
 *
 * Nothing to retry and nothing to reconfigure. Read {@link kind} for the
 * remedy: {@link SunsetKind.CLIENT_VERSION} means upgrade this package, while
 * {@link SunsetKind.API_VERSION} means the API version this build speaks is no
 * longer served, which reinstalling the same version will not fix.
 *
 * It should never be the first warning. While a version still works, the
 * service reports the coming sunset on every `listDomains`, and the SDK
 * surfaces that through `process.emitWarning` under
 * {@link DEPRECATION_WARNING_NAME} — once per message, not once per call.
 *
 * A subclass of {@link MemcoPreconditionFailedError}, so ordering matters when
 * catching both: test for this one first.
 */
export class MemcoSunsetError extends MemcoPreconditionFailedError {
  /** Whether the SDK build or the API version is the one that expired. */
  readonly kind: SunsetKind

  constructor(
    code: status,
    detail: string,
    debugErrorString: string | undefined,
    kind: SunsetKind
  ) {
    super(code, detail, debugErrorString)
    this.kind = kind
  }
}

/** Which allowance ran out. */
export const ResourceExhaustedKind = {
  /** A short-window limit. Backing off and retrying is the remedy. */
  RATE_LIMIT: 'rate_limit',
  /** A longer-window allowance. Retrying will not help. */
  QUOTA: 'quota',
  /** The message did not say which. Treat it as a rate limit. */
  UNKNOWN: 'unknown'
} as const

/** One of {@link ResourceExhaustedKind}'s values. */
export type ResourceExhaustedKind =
  (typeof ResourceExhaustedKind)[keyof typeof ResourceExhaustedKind]

// Checked before the rate-limit markers, so "daily rate limit" classifies as a
// quota: the longer window is the one that decides whether retrying can help.
const QUOTA_MARKERS = ['daily', 'weekly', 'monthly', 'quota']
const RATE_LIMIT_MARKERS = [
  'rate limit',
  'per-minute',
  'per minute',
  'per-second',
  'too many requests'
]

function classifyExhaustion(detail: string): ResourceExhaustedKind {
  const lowered = detail.toLowerCase()
  if (QUOTA_MARKERS.some(marker => lowered.includes(marker))) {
    return ResourceExhaustedKind.QUOTA
  }
  if (RATE_LIMIT_MARKERS.some(marker => lowered.includes(marker))) {
    return ResourceExhaustedKind.RATE_LIMIT
  }
  return ResourceExhaustedKind.UNKNOWN
}

/**
 * An allowance ran out.
 *
 * Read {@link kind} before retrying: a rate limit clears on a backoff, a quota
 * will not refill on one, and {@link ResourceExhaustedKind.UNKNOWN} means the
 * message did not say — treat it as a rate limit.
 *
 * The transport does not retry this on the caller's behalf, precisely because
 * which of the two it is decides whether retrying is worth anything, and only
 * the caller can act on that.
 */
export class MemcoResourceExhaustedError extends MemcoAPIError {
  /** Whether a rate limit or a longer-window quota was reached. */
  readonly kind: ResourceExhaustedKind

  constructor(code: status, detail: string, debugErrorString?: string) {
    super(code, detail, debugErrorString)
    this.kind = classifyExhaustion(detail)
  }
}

/**
 * The service could not be reached, or was not ready to answer.
 *
 * The one failure here a backoff is the right answer to: nothing about the
 * request is wrong. The transport already replays it — three attempts in all —
 * on {@link MemoryOperations.listDomains}, {@link MemoryOperations.getMemory}
 * and the health probe, so seeing it from one of those means all three failed.
 * Every other method is left alone, because replaying a write can record it
 * twice, so retrying one of those is the caller's decision and carries that
 * risk.
 *
 * {@link MemcoUnhealthyError} extends this class, so an `instanceof` chain
 * testing this one first never reaches it, and comparing `name` against
 * `'MemcoUnavailableError'` never matches it at all. Test for the unhealthy
 * case explicitly, either way.
 */
export class MemcoUnavailableError extends MemcoAPIError {}

/**
 * The endpoint answered its health probe with something other than serving.
 *
 * Raised only by the connection check, never by {@link fromServiceError}.
 *
 * The endpoint and TLS are right — something answered — and the backend is not
 * taking traffic. Nothing to reconfigure: wait, then call
 * {@link Memco.connect} again. It leaves the channel open on failure, so a
 * second attempt costs no fresh handshake. The credential is untested at this
 * point, because the probe runs before the call that carries it.
 *
 * A subclass of {@link MemcoUnavailableError}, so ordering matters when
 * catching both: test for this one first.
 */
export class MemcoUnhealthyError extends MemcoUnavailableError {}

/**
 * The deadline passed before the service answered.
 *
 * The deadline is the caller's own — the `timeout` on the call, else the one
 * the client was built with, else {@link DEFAULT_TIMEOUT} seconds — which is
 * why the transport does not retry this: a second attempt against the same
 * deadline has no more time than the first. Raise the deadline, or ask for
 * less.
 *
 * It says nothing about whether the service acted. A write that timed out may
 * still have been recorded, so sending it again can write it twice.
 */
export class MemcoTimeoutError extends MemcoAPIError {}

/**
 * The service failed in a way it does not describe to callers.
 *
 * Also where any status this SDK maps to no class of its own lands. Either way
 * it is not the caller's bug, and there is nothing in the request to fix.
 * {@link MemcoAPIError.codeName} says which status arrived — a code
 * `@grpc/grpc-js` does not recognise reads as `UNKNOWN` — and is what to log
 * alongside `detail`.
 *
 * Nothing here says the call would have succeeded a moment later, so this is
 * not the one to put a backoff loop around.
 */
export class MemcoInternalError extends MemcoAPIError {}

type APIErrorClass = new (
  code: status,
  detail: string,
  debugErrorString?: string
) => MemcoAPIError

const STATUS_TO_ERROR = new Map<status, APIErrorClass>([
  [status.UNAUTHENTICATED, MemcoAuthenticationError],
  [status.PERMISSION_DENIED, MemcoPermissionError],
  [status.INVALID_ARGUMENT, MemcoInvalidRequestError],
  [status.NOT_FOUND, MemcoNotFoundError],
  [status.FAILED_PRECONDITION, MemcoPreconditionFailedError],
  [status.RESOURCE_EXHAUSTED, MemcoResourceExhaustedError],
  [status.UNAVAILABLE, MemcoUnavailableError],
  [status.DEADLINE_EXCEEDED, MemcoTimeoutError]
])

/** The error domain Memco's own `google.rpc.ErrorInfo` details are scoped to. */
const ERROR_DOMAIN = 'memco.ai'

const SUNSET_REASONS = new Map<string, SunsetKind>([
  ['CLIENT_VERSION_SUNSET', SunsetKind.CLIENT_VERSION],
  ['API_VERSION_SUNSET', SunsetKind.API_VERSION]
])

/**
 * Read the sunset discriminator out of a call's trailing metadata.
 *
 * Returns `undefined` for anything that is not a Memco-scoped sunset, and for
 * any trailer that cannot be read. Losing the discriminator costs the caller
 * the `MemcoSunsetError` they would have had, leaving them a
 * `MemcoPreconditionFailedError`; a throw here would cost them their error
 * entirely.
 */
function sunsetKind(error: Partial<ServiceError>): SunsetKind | undefined {
  const raw = error.metadata?.get(STATUS_DETAILS_KEY)?.[0]
  if (!(raw instanceof Buffer)) {
    return undefined
  }
  for (const info of errorDetails(raw)) {
    if (info.domain === ERROR_DOMAIN) {
      const kind = SUNSET_REASONS.get(info.reason)
      if (kind !== undefined) {
        return kind
      }
    }
  }
  return undefined
}

/**
 * Translate a gRPC failure into the error that says what happened.
 *
 * Returns rather than throws, so a caller can add context before raising.
 *
 * The mapping is by status code, with `FAILED_PRECONDITION` splitting further:
 * when the call carries a Memco-scoped `google.rpc.ErrorInfo` naming a sunset,
 * the result is a {@link MemcoSunsetError}. Any code with no entry becomes a
 * {@link MemcoInternalError}, which is where an unrecognised status belongs —
 * it is not something the caller did.
 *
 * @param error The rejection `@grpc/grpc-js` produced.
 * @returns The typed error to raise.
 */
export function fromServiceError(error: Partial<ServiceError>): MemcoAPIError {
  const code =
    typeof error.code === 'number' && status[error.code] !== undefined
      ? error.code
      : status.UNKNOWN
  const detail = error.details ?? ''
  // grpc-js puts its diagnostic in the message; there is no separate accessor
  // for it, and it is often the same string as the details.
  const debug = error.message
  if (code === status.FAILED_PRECONDITION) {
    const kind = sunsetKind(error)
    if (kind !== undefined) {
      return new MemcoSunsetError(code, detail, debug, kind)
    }
  }
  const cls = STATUS_TO_ERROR.get(code) ?? MemcoInternalError
  return new cls(code, detail, debug)
}
