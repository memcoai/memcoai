/**
 * The guarantee that every failure this SDK produces is a `MemcoError`.
 *
 * `errors.ts` states it and `CONTRIBUTING.md` makes it a review condition, but
 * TypeScript only enforces it for callers who have a compiler. npm's default
 * consumer language does not, and a `?? null` in the caller's own code is
 * enough to reach every entry point below with a value its signature forbids.
 * A bare `TypeError` escaping any of them breaks the promise for exactly the
 * callers least able to see it coming.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { MemcoError } from '../src/errors.js'
import { resolve } from '../src/internal/config.js'
import { setLevel } from '../src/internal/logging.js'
import * as requests from '../src/internal/requests.js'
import * as validate from '../src/internal/validate.js'

// Each entry reaches a public or near-public entry point with something its
// TypeScript signature rules out. The assertion is only ever "typed", never a
// particular message: what is being pinned is the class, not the wording.
const hostile: [string, () => unknown][] = [
  ['checkQuery(number)', () => validate.checkQuery(123 as never)],
  ['checkIdx(number)', () => validate.checkIdx(123 as never)],
  ['checkTitle(null)', () => validate.checkTitle(null as never)],
  ['checkTags(number)', () => validate.checkTags(123 as never)],
  ['checkTags(object)', () => validate.checkTags({ a: 1 } as never)],
  ['checkTags([null])', () => validate.checkTags([null] as never)],
  ['checkFeedback(number)', () => validate.checkFeedback(123 as never)],
  ['checkFeedback([null])', () => validate.checkFeedback([null] as never)],
  ['checkScope(null)', () => validate.checkScope(null as never)],
  [
    'checkImportMemories([null])',
    () => validate.checkImportMemories([null] as never)
  ],
  [
    'checkImportMemories(insights: [null])',
    () =>
      validate.checkImportMemories([
        { queries: ['a query'], insights: [null] }
      ] as never)
  ],
  [
    'revertMemoryRequest(number)',
    () => requests.revertMemoryRequest(123 as never)
  ],
  [
    'searchRequest(number)',
    () =>
      requests.searchRequest(123 as never, {
        domain: 'coding',
        sessionId: null
      })
  ],
  [
    'createMemoryRequest(content: number)',
    () =>
      requests.createMemoryRequest({
        query: 'a query',
        title: 'a title',
        content: 123 as never,
        domain: 'coding',
        sessionId: null,
        source: 2
      })
  ],
  ['resolve(token: number)', () => resolve({ token: 123 as never })],
  ['resolve(host: number)', () => resolve({ token: 't', host: 123 as never })],
  ['resolve(timeout: NaN)', () => resolve({ token: 't', timeout: Number.NaN })],
  [
    'resolve(timeout: Infinity)',
    () => resolve({ token: 't', timeout: Number.POSITIVE_INFINITY })
  ],
  ['setLevel(null)', () => setLevel(null as never)],
  ['setLevel(number)', () => setLevel(10 as never)],
  ['setLevel(boolean)', () => setLevel(true as never)]
]

for (const [label, call] of hostile) {
  test(`${label} fails as a MemcoError, not a bare TypeError`, () => {
    assert.throws(call, (error: unknown) => {
      assert.ok(
        error instanceof MemcoError,
        `${label} threw ${(error as Error)?.constructor?.name}: ${String(error)}`
      )
      return true
    })
  })
}

test('a null token falls back to the environment', () => {
  // Not an error: a caller writing `token: config.token ?? null` means "use
  // the environment", so this has to resolve rather than refuse.
  const config = resolve({
    token: null as never,
    env: { MEMCO_API_TOKEN: 'from-the-environment' }
  })
  assert.equal(config.target, 'grpc.spark.memco.ai:443')
})
