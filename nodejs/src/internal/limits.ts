/**
 * Applying the input limits the service reports.
 *
 * The service owns these numbers and delivers them on `listDomains`. An SDK
 * carrying its own copy would go stale the moment the service changed one: an
 * older client would keep rejecting requests the service had started accepting,
 * locally, with no way for the caller to tell why. So nothing here has a
 * default — a client that has not been told a limit does not check it, and the
 * service rules.
 *
 * Two of the caps **trim** rather than refuse. The service keeps the first N
 * and drops the rest, so raising would reject a call it would have accepted.
 */

import type { DomainEntry, Limits } from '../types.js'

/**
 * What the service has told this client about its own limits.
 *
 * Empty until a `listDomains` response supplies it, and refreshed by every
 * subsequent one.
 */
export class Known {
  /**
   * The service-wide caps, or `null` if none have been reported.
   *
   * Written only by {@link Known.update}, which is what keeps every request
   * validated against one consistent set of numbers.
   */
  limits: Limits | null = null

  /**
   * The per-domain tag cap, by domain slug.
   *
   * An absent entry and a zero both mean that domain sets no cap, which is
   * what {@link Known.maxTags} collapses them to.
   */
  readonly tagsPerDomain = new Map<string, number>()

  /**
   * Record what a `listDomains` response reported.
   *
   * @param limits The service-wide caps the response carried, or `null` when it
   *   carried none. A `null` does not clear what is already known: silence is
   *   an older service saying nothing, not a retraction.
   * @param domains The domains it described, each carrying its own tag cap.
   */
  update(limits: Limits | null, domains: readonly DomainEntry[]): void {
    // Truthiness rather than a test against null, so a JavaScript caller who
    // passed nothing at all is treated as having reported nothing. A Limits is
    // always an object and so always truthy, which is what makes the two the
    // same test for every value the contract can produce.
    if (limits) {
      // Copied, not kept by reference. TypeScript's `readonly` is
      // compile-time only, so holding the caller's own object would let a
      // JavaScript caller who mutated the limits they were handed change what
      // every later request is validated against.
      this.limits = { ...limits }
    }
    for (const domain of domains) {
      this.tagsPerDomain.set(domain.slug, domain.maxTagsPerQuery)
    }
  }

  /**
   * Return the tag cap for a domain.
   *
   * @param domain The domain being named, or `null` when the call is scoped by
   *   a session instead.
   * @returns The cap, or `0` for no cap — which is also the answer when the
   *   domain is unknown or the call names a session, since the session's domain
   *   is not visible to the client.
   */
  maxTags(domain: string | null): number {
    return this.tagsPerDomain.get(domain ?? '') ?? 0
  }
}
