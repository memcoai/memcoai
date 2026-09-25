/**
 * The credential each call carries, and the renewing holder it is leased from.
 *
 * The credential travels as a gRPC metadata header, passed explicitly with each
 * call rather than attached by a channel-level interceptor. A client holds more
 * than one credential at once — its own token, and a key per impersonated
 * session — so which one a call carries has to be an argument of that call, not
 * state on the channel every call shares. Passing it explicitly also fails
 * closed: a call made without it carries no credential at all, which is
 * exactly what the health probe and the token exchange need.
 *
 * Call credentials are not used because gRPC refuses them on an insecure
 * channel, and the SDK must also support plaintext endpoints for local
 * development and tests.
 */

import { inspect } from 'node:util'

import { Metadata, status } from '@grpc/grpc-js'

import { MemcoConfigError, MemcoTimeoutError } from '../errors.js'
import { ROOT, getLogger } from './logging.js'

const log = getLogger(`${ROOT}.auth`)

/** Metadata key carrying the credential. */
export const AUTH_HEADER = 'authorization'

/**
 * Scheme prefix placed before the credential.
 *
 * The service matches this with a case-sensitive prefix check, so the capital
 * `B` and the trailing space are both load-bearing. A lowercase `bearer` is
 * rejected as an invalid credential.
 */
export const AUTH_SCHEME = 'Bearer '

/**
 * The fraction of a minted credential's lifetime after which it is renewed.
 *
 * Early enough that a call leased just before the renewal point still has a
 * fifth of the lifetime to finish in, and late enough that a credential is not
 * re-minted more often than it needs to be.
 */
export const RENEW_AFTER = 0.8

/**
 * The clocks renewal is decided by, both in milliseconds.
 *
 * Read through this object rather than called directly, so the suite can move
 * both by replacing them here without touching the deadlines grpc-js computes.
 */
export const clock = {
  /** A monotonic reading, which every renewal point is measured against. */
  monotonic: (): number => performance.now(),
  /** Unix time, for reading an absolute expiry the service sends. */
  now: (): number => Date.now()
}

const CLOSED = 'this session is closed; open a new one to make more calls'

/** The longest delay a Node timer takes; past it, one fires at once. */
const LONGEST_TIMER_MS = 2 ** 31 - 1

/**
 * Build the metadata one call sends its credential in.
 *
 * Fresh per call, so nothing another call added can ride along with it.
 *
 * @param token The bearer to send: an API key, an issued token or an
 *   impersonation key.
 * @returns The metadata, holding exactly one credential entry.
 * @throws MemcoConfigError If the credential cannot be sent as a header.
 */
export function metadata(token: string): Metadata {
  const sent = new Metadata()
  try {
    sent.set(AUTH_HEADER, `${AUTH_SCHEME}${token}`)
  } catch {
    // `set` rejects a value outside printable ASCII by throwing an Error that
    // quotes it, which would put the whole credential into whatever the
    // application logs. `resolve` refuses such a token long before this, so
    // reaching here means a malformed issued token or key — but this is the
    // last line before the value leaves the SDK, and it is the one that must
    // not repeat what grpc-js would have said.
    throw new MemcoConfigError(
      'the token cannot be sent as an HTTP header: it must be printable ASCII'
    )
  }
  return sent
}

/**
 * One credential a {@link Renewing} holds, and the calls still using it.
 *
 * Mutable, and compared by identity: the use count and the retired flag are
 * the bookkeeping that decides when the credential may be ended.
 *
 * The value is stored as a non-enumerable property and left out of both
 * {@link Minted.toJSON} and the `util.inspect` hook, as the client's own
 * configuration hides its token, so no rendering of a client, a session or an
 * error that reaches one can print it.
 */
export class Minted {
  /** The bearer itself. Hidden from every rendering of this object. */
  declare readonly value: string

  /**
   * The {@link clock} monotonic reading, in milliseconds, at which to replace
   * it. `Infinity` for a credential that is never renewed.
   */
  readonly renewAt: number

  /** The id the service names the credential by, if it has one. Safe to log. */
  readonly keyId: string

  /**
   * The {@link clock} monotonic reading, in milliseconds, at which it stops
   * working. Until then a failed renewal falls back to it.
   */
  readonly expiresAt: number

  /** How many leases of it are open. */
  users = 0

  /** Whether it has been replaced or closed, so that its last lease ends it. */
  retired = false

  /**
   * @param value The bearer, hidden from every rendering.
   * @param renewAt When to replace it, on the monotonic clock.
   * @param keyId The id the service names it by, if any.
   * @param expiresAt When it stops working, on the monotonic clock. Omitted,
   *   the renewal point, which leaves nothing to fall back on.
   */
  constructor(value: string, renewAt: number, keyId = '', expiresAt = renewAt) {
    this.renewAt = renewAt
    this.keyId = keyId
    this.expiresAt = expiresAt
    Object.defineProperty(this, 'value', {
      value,
      enumerable: false,
      writable: false,
      configurable: false
    })
  }

  /**
   * Everything but the value, for `JSON.stringify`.
   *
   * @returns The bookkeeping, and the key id.
   */
  toJSON(): {
    keyId: string
    renewAt: number
    users: number
    retired: boolean
  } {
    return {
      keyId: this.keyId,
      renewAt: this.renewAt,
      users: this.users,
      retired: this.retired
    }
  }

  /**
   * Everything but the value, for `util.inspect` and `console.log`.
   *
   * @returns A rendering that omits the value even under `showHidden`.
   */
  [inspect.custom](): string {
    return `Minted ${inspect(this.toJSON())}`
  }
}

/**
 * Mark a replaced credential retired, returning it if it is to be ended now.
 *
 * Exactly one party ends each credential: the retirer when nothing is using
 * it, otherwise its last lease.
 *
 * @param minted The credential being replaced, if one was held.
 * @returns The credential when nothing is using it, else `undefined`.
 */
function retire(minted: Minted | undefined): Minted | undefined {
  if (minted === undefined) {
    return undefined
  }
  minted.retired = true
  return minted.users === 0 ? minted : undefined
}

/**
 * Wait for a promise, but no later than a deadline.
 *
 * The promise itself goes on: only this caller stops waiting for it.
 *
 * @param pending What to wait for.
 * @param until The epoch deadline in milliseconds, or `Infinity` for none. A
 *   deadline further off than a timer can count to is treated as none, since
 *   Node would fire such a timer at once.
 * @returns What the promise settled with, if it settled in time.
 * @throws MemcoTimeoutError If the deadline passed first.
 */
function within<T>(pending: Promise<T>, until: number): Promise<T> {
  if (until - Date.now() > LONGEST_TIMER_MS) {
    return pending
  }
  return new Promise<T>((settle, fail) => {
    const timer = setTimeout(
      () => {
        fail(
          new MemcoTimeoutError(
            status.DEADLINE_EXCEEDED,
            'the deadline passed while waiting for a credential to be issued'
          )
        )
      },
      Math.max(0, until - Date.now())
    )
    pending.then(
      value => {
        clearTimeout(timer)
        settle(value)
      },
      (error: unknown) => {
        clearTimeout(timer)
        fail(error)
      }
    )
  })
}

/**
 * A credential minted on first use and renewed at its renewal point.
 *
 * A client's own token and each impersonated session's key are held by one of
 * these. At most one mint is in flight at a time, shared by every lease that
 * needs it.
 *
 * Past the renewal point, while the current credential has not yet expired, a
 * lease starts the renewal — or joins the one running — without waiting for
 * it, and goes on under the current credential: due is not expired, and a slow
 * token or admin service must not fail calls the current credential serves.
 * The renewal serves the leases after it lands. If it fails, the failure is
 * logged once at `WARNING` and the next lease past the renewal point tries
 * again.
 *
 * Only a lease with no credential to use — none minted yet, or the current one
 * expired — waits on the mint, and no longer than its own deadline; the mint
 * itself goes on, and what it produces serves the next call. Leases waiting on
 * one mint share its failure, so a burst of calls meeting a refused mint is
 * refused once rather than minting again in turn, each queued behind the last.
 * A credential it has replaced is
 * ended only once the last call carrying it has finished, so a renewal never
 * revokes a key under a call still in flight, yet no replaced key is left live
 * once its calls are done.
 *
 * Nothing here can be cancelled part-way. A mint the service is already
 * answering finishes whatever its caller does next, and what it produces is
 * held, and ended, like any other.
 */
export class Renewing {
  /** The credential calls lease now, once one has been minted. */
  private current: Minted | undefined

  /** The mint in flight, which every lease arriving meanwhile shares. */
  private minting: Promise<Minted> | undefined

  private closed = false

  /** What close() started, which every later close() waits on too. */
  private closing: Promise<void> | undefined

  /**
   * Hold nothing yet; the first lease mints.
   *
   * @param mint Mints a fresh credential. Rejects with a typed error on
   *   failure, which every lease waiting on it rejects with in turn.
   * @param end Ends a credential that has been replaced or closed. Must not
   *   reject. Omitted when there is nothing to end a credential with.
   */
  constructor(
    private readonly mint: () => Promise<Minted>,
    private readonly end?: (minted: Minted) => Promise<void>
  ) {}

  /**
   * Hold the current credential for the duration of one call.
   *
   * A credential that is missing or expired is minted first, and a failed mint
   * then rejects before anything is counted, so the next lease simply tries
   * again. One that is due but still valid is leased at once, while its
   * renewal runs in the background.
   *
   * @param use The call, given the credential to send. It receives the
   *   {@link Minted} itself rather than its bare value, since the object's
   *   renderings hide the value.
   * @param until The caller's own epoch deadline in milliseconds, which bounds
   *   how long it waits on a mint. Omitted, it waits for the mint to settle.
   * @returns What `use` returned. The lease that is the last to use a
   *   replaced or closed credential sets its end off without waiting for it:
   *   the end belongs to the holder, and the call has its answer already.
   * @throws MemcoConfigError If this credential has been closed.
   * @throws MemcoTimeoutError If the deadline passed while a mint was pending.
   * @throws MemcoError Whatever the mint, or `use`, threw.
   */
  async lease<T>(
    use: (bearer: Minted) => Promise<T>,
    until = Number.POSITIVE_INFINITY
  ): Promise<T> {
    if (this.closed) {
      throw new MemcoConfigError(CLOSED)
    }
    let held = this.current
    if (held !== undefined && clock.monotonic() >= held.renewAt) {
      if (clock.monotonic() < held.expiresAt) {
        // Due but valid: renew beside this lease rather than in front of it.
        // The renewal's failure is logged by the renewal, so nothing here is
        // left to report it.
        void (this.minting ??= this.renew()).catch(() => undefined)
      } else {
        held = undefined
      }
    }
    if (held === undefined) {
      // The fresh credential is used whatever its renewal point says: one that
      // arrives already due is still the newest there is, and minting again at
      // once would never finish.
      held = await within((this.minting ??= this.renew()), until)
      if (held.retired) {
        // Retired before this lease could count itself — by a close() made
        // in the moment between the mint settling and this lease resuming —
        // and so possibly ended already. Nothing else retires a credential
        // that fresh.
        throw new MemcoConfigError(CLOSED)
      }
    }
    held.users += 1
    try {
      return await use(held)
    } finally {
      held.users -= 1
      if (held.retired && held.users === 0) {
        // `end` never rejects.
        void this.end?.(held)
      }
    }
  }

  /**
   * Refuse further leases, and end the credential once nothing uses it.
   *
   * Safe to call more than once, and every call returns once the credential
   * idle at the first has been ended. Waits for a mint in flight, so the
   * credential it produces is ended too rather than left live.
   */
  async close(): Promise<void> {
    // Shared, so a second close() does not report the credential ended while
    // the first is still ending it.
    this.closing ??= this.shut()
    await this.closing
  }

  private async shut(): Promise<void> {
    this.closed = true
    if (this.minting !== undefined) {
      // A failed mint is its lease's to report, not this close's.
      await this.minting.catch(() => undefined)
    }
    // With nothing minting, the end starts in the same job as close() was
    // called in, so anything that looks for it afterwards finds it in flight.
    const idle = retire(this.current)
    this.current = undefined
    if (idle !== undefined) {
      await this.end?.(idle)
    }
  }

  /**
   * Mint a replacement, and retire what it replaces.
   *
   * @returns The fresh credential, the one it replaced being ended if nothing
   *   was using it — or, when the mint fails, the current credential if it has
   *   not yet expired.
   * @throws MemcoError What the mint threw, when there is no current
   *   credential to fall back on.
   */
  private async renew(): Promise<Minted> {
    try {
      let fresh: Minted
      try {
        fresh = await this.mint()
      } catch (error) {
        const current = this.current
        if (current === undefined || clock.monotonic() >= current.expiresAt) {
          throw error
        }
        // Logged here, once per attempt, rather than by each lease sharing it.
        // The key id revokes nothing, so it is safe to log; the value never is.
        log.warning(
          'could not renew %s; using it until it expires in %ss: %s',
          current.keyId === '' ? 'the client token' : `key ${current.keyId}`,
          ((current.expiresAt - clock.monotonic()) / 1000).toFixed(0),
          error
        )
        return current
      }
      const stale = retire(this.current)
      this.current = fresh
      if (stale !== undefined) {
        // Ended at once, one live key per session, since the service caps how
        // many a user may hold — but not waited for: the fresh credential is
        // current already, and the leases waiting on it have no stake in the
        // end. `end` never rejects.
        void this.end?.(stale)
      }
      return fresh
    } finally {
      this.minting = undefined
    }
  }
}
