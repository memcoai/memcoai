/**
 * Surfacing the deprecation notice the service returns.
 *
 * The service reports on every `listDomains` whether what the caller is using
 * has been superseded — either the API version or this SDK build — and supplies
 * the remedy as text. Three rules govern how that is shown, and each exists
 * because breaking it makes the notice useless:
 *
 * - **Once per process, not per call.** A busy client emitting this on every
 *   request becomes unusable and the notice gets filtered out wholesale, which
 *   is the opposite of the intent.
 * - **Verbatim.** The flag deliberately does not say which of the two causes
 *   applies; only the message does. Pattern-matching it or substituting text of
 *   our own would eventually tell someone to upgrade the SDK when their actual
 *   remedy is migrating to a new API version.
 * - **Never fatal.** This is advice. It must not refuse to build a client,
 *   throw, or block a call — the service keeps serving, and the caller decides
 *   when to act. Enforcing the sunset date here would mean a skewed clock
 *   bricking a working client, and this SDK refusing work the service would
 *   have accepted. The cutoff is the service's to make: once it stops serving a
 *   build it refuses the call outright, which reaches the caller as a
 *   `MemcoSunsetError`.
 *
 * The third rule reaches only as far as Node lets it. `process.emitWarning`
 * defers the event to the next tick, so a `process.on('warning')` listener that
 * throws does so outside this call and cannot be caught here.
 */

/**
 * The `type` every notice is emitted under, and the `name` it arrives with.
 *
 * Node prints it ahead of the text and passes it as the warning's `name`, which
 * is what lets a caller filter these out with a `process.on('warning')` handler
 * without also silencing Node's own.
 *
 * Exported because a caller who cannot name this has to hard-code the string to
 * filter on it.
 */
export const DEPRECATION_WARNING_NAME = 'MemcoDeprecationWarning'

/**
 * Messages already surfaced, so a repeat call stays quiet.
 *
 * Keyed on the text rather than on the flag, so that a message the service
 * changes is shown again.
 */
const seen = new Set<string>()

/**
 * Surface a deprecation notice, at most once per message per process.
 *
 * @param message The service-authored remedy, relayed unchanged. Nothing is
 *   inferred from it and nothing is added beyond the sunset date. An empty
 *   message is the service reporting no notice, and says nothing.
 * @param sunset When what the caller uses stops working, as an ISO `YYYY-MM-DD`
 *   string, or `null` when the service named no date.
 */
export function warnOnce(message: string, sunset: string | null = null): void {
  if (!message || seen.has(message)) {
    return
  }
  seen.add(message)
  const text = sunset ? `${message} (stops working on ${sunset})` : message
  try {
    process.emitWarning(text, DEPRECATION_WARNING_NAME)
  } catch {
    // A caller who has made warnings fatal — or replaced the emitter — would
    // otherwise have a working call broken by a notice about a future change.
    // Losing the notice is the lesser harm: deprecation is advice, and must
    // never fail a call.
  }
}

/**
 * Forget which notices have been surfaced.
 *
 * Only for tests: the once-per-process rule would otherwise let one test
 * suppress another's notice.
 */
export function resetWarnings(): void {
  seen.clear()
}
