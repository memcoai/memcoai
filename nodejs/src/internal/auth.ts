/**
 * Credential injection for outgoing calls.
 *
 * The credential travels as a gRPC metadata header on every request. It is
 * attached by a client interceptor rather than by call credentials, because
 * gRPC refuses call credentials on an insecure channel and the SDK must also
 * support plaintext endpoints for local development and tests.
 */

import { InterceptingCall, Metadata, type Interceptor } from '@grpc/grpc-js'

import { MemcoConfigError } from '../errors.js'

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
 * Methods the credential is deliberately withheld from.
 *
 * The health endpoint takes no credential, so sending one there would achieve
 * nothing and would widen its exposure: health endpoints are routinely reached
 * by infrastructure that logs request headers more liberally than an
 * application path does, and the probe fires on every client construction
 * before the caller has made a request.
 */
export const UNAUTHENTICATED_PREFIX = '/grpc.health.v1.'

/**
 * Combine caller metadata with the credential.
 *
 * Any credential already present is dropped rather than kept alongside the real
 * one. gRPC permits repeated keys, and servers disagree about which occurrence
 * wins, so appending would let caller-supplied metadata decide which credential
 * the server reads.
 *
 * @param metadata The metadata already on the call. It is cloned, not mutated:
 *   a caller who reuses one `Metadata` object across calls should not find the
 *   credential accumulating in it.
 * @param token The credential to add.
 * @returns The combined metadata, carrying exactly one credential entry.
 */
export function merged(metadata: Metadata, token: string): Metadata {
  const combined = metadata.clone()
  try {
    // `Metadata` normalises every key to lower case and `set` replaces all
    // values stored under one, so a caller-supplied `Authorization` is
    // displaced by this rather than sent beside it.
    combined.set(AUTH_HEADER, `${AUTH_SCHEME}${token}`)
  } catch {
    // `set` rejects a value outside printable ASCII by throwing an Error that
    // quotes it, which would put the whole credential into whatever the
    // application logs. `resolve` refuses such a token long before this, so
    // reaching here means a config built some other way — but this is the last
    // line before the value leaves the SDK, and it is the one that must not
    // repeat what grpc-js would have said.
    throw new MemcoConfigError(
      'the token cannot be sent as an HTTP header: it must be printable ASCII'
    )
  }
  return combined
}

/**
 * Build the interceptor that adds the credential to every authenticated call.
 *
 * @param token The API key or session token to send.
 * @returns An interceptor to pass as `interceptors` when constructing a client.
 */
export function authInterceptor(token: string): Interceptor {
  return (options, nextCall) => {
    if (options.method_definition.path.startsWith(UNAUTHENTICATED_PREFIX)) {
      // No requester at all, so the call's metadata reaches the wire untouched.
      return new InterceptingCall(nextCall(options))
    }
    return new InterceptingCall(nextCall(options), {
      start(metadata, listener, next) {
        next(merged(metadata, token), listener)
      }
    })
  }
}
