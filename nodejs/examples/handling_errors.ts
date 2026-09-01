/**
 * Every failure mode the SDK raises, and what to do about each.
 *
 * No raw gRPC error ever reaches you: everything is a MemcoError subclass, so
 * you can be as coarse or as precise as you like.
 *
 * The branches below test with `instanceof`, which is exact here because this
 * file loads one copy of the SDK. An application that somehow loads both the
 * ESM and the CommonJS build holds two copies of every class, so `instanceof`
 * can be false for an error that really is one; comparing `error.name` against
 * the class name cannot. src/errors.ts says more about that.
 *
 * Run it with:
 *
 *     export MEMCO_API_TOKEN=...
 *     npm run build:test && node build/js/examples/handling_errors.js
 */

import { setTimeout as sleep } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

import {
  Memco,
  MemcoAPIError,
  MemcoAuthenticationError,
  MemcoConfigError,
  MemcoInvalidRequestError,
  MemcoNotFoundError,
  MemcoPermissionError,
  MemcoPreconditionFailedError,
  MemcoResourceExhaustedError,
  MemcoSunsetError,
  MemcoTimeoutError,
  MemcoUnavailableError,
  MemcoUnhealthyError,
  ResourceExhaustedKind,
  SunsetKind
} from '../src/index.js'

/**
 * Build a client, reporting anything that stops it connecting.
 *
 * @returns The connected client, or `null` if it could not be built.
 */
async function connect(): Promise<Memco | null> {
  try {
    return await new Memco().connect()
  } catch (error) {
    // The order of these branches is load-bearing and nothing enforces it:
    // MemcoSunsetError extends MemcoPreconditionFailedError, and
    // MemcoUnhealthyError extends MemcoUnavailableError, so testing the
    // general class first would make the specific one unreachable. Specific
    // before general, every time.
    if (error instanceof MemcoConfigError) {
      // Nothing was sent: no token, or an unusable host or port.
      console.log(`configuration problem: ${error.message}`)
    } else if (error instanceof MemcoAuthenticationError) {
      // connect() lists domains to learn the service's limits, and that call
      // carries the credential — so a stale token fails here rather than on
      // the first search. This is the likeliest way for connecting to fail.
      console.log('credential rejected; check MEMCO_API_TOKEN is current')
    } else if (error instanceof MemcoSunsetError) {
      // Past its sunset date and no longer served. Nothing to retry and
      // nothing to reconfigure. Long before this, the same service sends a
      // deprecation notice through process.emitWarning, on every version that
      // still works, carrying the date this happens.
      if (error.kind === SunsetKind.API_VERSION) {
        // Not "reinstall the package": the API version this build speaks is
        // the thing that stopped being served.
        console.log(`API version no longer served: ${error.detail}`)
      } else {
        console.log(`upgrade required: ${error.detail}`)
      }
    } else if (error instanceof MemcoPreconditionFailedError) {
      // Same status code, unrelated cause: billing, account state, terms.
      console.log(`precondition unmet: ${error.detail}`)
    } else if (error instanceof MemcoTimeoutError) {
      // Neither a MemcoUnavailableError nor a MemcoConfigError, so it needs
      // its own branch even in a connection helper.
      console.log(`timed out reaching the service: ${error.message}`)
    } else if (error instanceof MemcoUnhealthyError) {
      // The service answered and said it is not ready. The endpoint, TLS and
      // credential are all fine; the backend is not taking traffic.
      console.log(`service is not serving: ${error.message}`)
    } else if (error instanceof MemcoUnavailableError) {
      // Could not reach it at all.
      console.log(`unreachable: ${error.message}`)
    } else {
      // Not something this SDK models. A chain of branches swallows whatever
      // it does not match, so letting it propagate has to be said out loud.
      throw error
    }
    return null
  }
}

/**
 * Search, handling each failure the way that failure deserves.
 *
 * @param client A connected client.
 * @param query The search query.
 * @param domain The domain to search.
 */
async function searchWithRetry(
  client: Memco,
  query: string,
  domain: string
): Promise<void> {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const result = await client.memory.search(query, { domain, timeout: 10 })
      console.log(`${result.memories.length} memories`)
      return
    } catch (error) {
      // Ordered like the chain above, and for the same reason: every modelled
      // class is tested before MemcoAPIError, which is the base of all of them.
      if (error instanceof MemcoInvalidRequestError) {
        // Malformed request. Retrying is pointless — fix the call. Some of
        // these are raised locally, before anything is sent.
        console.log(`bad request: ${error.detail}`)
        return
      } else if (error instanceof MemcoAuthenticationError) {
        // Missing, expired or revoked credential. The service returns one
        // indistinguishable message for all of them by design.
        console.log('credential rejected; check MEMCO_API_TOKEN is current')
        return
      } else if (error instanceof MemcoPermissionError) {
        // Authenticated, but this credential lacks the scope or role.
        console.log('credential lacks permission for this operation')
        return
      } else if (error instanceof MemcoNotFoundError) {
        console.log('nothing there')
        return
      } else if (error instanceof MemcoResourceExhaustedError) {
        // Rate limit and usage quota share a status code, so the SDK infers
        // which one from the message. Only the rate limit is worth waiting
        // out; a spent quota will not clear on this timescale.
        if (error.kind === ResourceExhaustedKind.QUOTA) {
          console.log('usage quota exhausted; retrying will not help')
          return
        }
        const wait = 2 ** attempt
        console.log(`rate limited; retrying in ${wait}s`)
        await sleep(wait * 1000)
      } else if (error instanceof MemcoTimeoutError) {
        console.log('timed out; pass a larger timeout')
        return
      } else if (error instanceof MemcoUnavailableError) {
        const wait = 2 ** attempt
        console.log(`service unavailable; retrying in ${wait}s`)
        await sleep(wait * 1000)
      } else if (error instanceof MemcoAPIError) {
        // Anything the service reported that is not modelled above.
        console.log(`unexpected ${error.codeName}: ${error.detail}`)
        return
      } else {
        throw error
      }
    }
  }

  console.log('gave up after 3 attempts')
}

/** Run the example. */
async function main(): Promise<void> {
  // `await using` accepts null, so the failed-to-connect path needs no
  // separate shape: there is simply nothing to close.
  await using client = await connect()
  if (client === null) {
    return
  }

  await searchWithRetry(client, 'how does authentication work', 'coding')

  // Structural problems are caught before anything is sent. Field length
  // limits belong to the service, so an over-long value is reported by it.
  try {
    await client.memory.search('', { domain: 'coding' })
  } catch (error) {
    if (!(error instanceof MemcoInvalidRequestError)) {
      throw error
    }
    console.log(`caught locally, nothing sent: ${error.detail}`)
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
