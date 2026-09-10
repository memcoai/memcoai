/**
 * How the service's deprecation notice reaches the caller.
 *
 * Three rules, each of which makes the notice useless when broken: once per
 * process, verbatim, and never fatal.
 */

import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'

import { resetWarnings, warnOnce } from '../src/internal/deprecation.js'

/** One emitted warning, reduced to what a caller can actually observe. */
interface Emitted {
  name: string
  message: string
}

/**
 * Run `body` and collect the process warnings it emits.
 *
 * `process.emitWarning` defers the event to the next tick, so the collection
 * has to outlive the call that caused it.
 */
async function captured(body: () => void): Promise<Emitted[]> {
  const seen: Emitted[] = []
  const listener = (warning: Error): void => {
    seen.push({ name: warning.name, message: warning.message })
  }
  process.on('warning', listener)
  try {
    body()
    await new Promise(resolve => setImmediate(resolve))
  } finally {
    process.off('warning', listener)
  }
  return seen
}

beforeEach(() => {
  resetWarnings()
})

afterEach(() => {
  resetWarnings()
})

test('a notice is emitted once however many times it is reported', async () => {
  // Per-call warnings make a busy client unusable and get filtered wholesale,
  // which defeats the point of warning at all.
  const seen = await captured(() => {
    for (let attempt = 0; attempt < 5; attempt += 1) {
      warnOnce('Memory API v1 is superseded; migrate to v2.')
    }
  })
  assert.equal(seen.length, 1)
})

test('a message the service changed is surfaced again', async () => {
  // Keyed on the text, not on a flag, so a new remedy reaches the caller.
  const seen = await captured(() => {
    warnOnce('Memory API v1 is superseded; migrate to v2.')
    warnOnce('A different remedy entirely.')
  })
  assert.deepEqual(
    seen.map(warning => warning.message),
    [
      'Memory API v1 is superseded; migrate to v2.',
      'A different remedy entirely.'
    ]
  )
})

test("the service's wording is relayed unchanged", async () => {
  // Only the service knows whether the remedy is an upgrade or a migration, so
  // nothing may be inferred from the text or added to it.
  const seen = await captured(() => {
    warnOnce('Memory API v1 is superseded; migrate to v2.')
  })
  assert.equal(seen[0].message, 'Memory API v1 is superseded; migrate to v2.')
})

test('a sunset date is appended, not substituted', async () => {
  const seen = await captured(() => {
    warnOnce('Memory API v1 is superseded; migrate to v2.', '2027-01-01')
  })
  assert.equal(
    seen[0].message,
    'Memory API v1 is superseded; migrate to v2. (stops working on 2027-01-01)'
  )
})

test('the notice carries its own name so a caller can filter it', async () => {
  const seen = await captured(() => {
    warnOnce('Memory API v1 is superseded; migrate to v2.')
  })
  assert.equal(seen[0].name, 'MemcoDeprecationWarning')
})

test('a service reporting no notice says nothing', async () => {
  const seen = await captured(() => {
    warnOnce('')
  })
  assert.deepEqual(seen, [])
})

test('a notice that cannot be emitted never fails the call', () => {
  // Deprecation is advice about a future change. Losing the notice is the
  // lesser harm; breaking a call the service would have served is not.
  const emitWarning = process.emitWarning
  process.emitWarning = (): never => {
    throw new Error('warnings are escalated to errors here')
  }
  try {
    assert.doesNotThrow(() => {
      warnOnce('Memory API v1 is superseded; migrate to v2.')
    })
  } finally {
    process.emitWarning = emitWarning
  }
})

test('forgetting a surfaced notice lets it be surfaced again', async () => {
  // The reset exists so one test cannot suppress another's notice.
  const first = await captured(() => {
    warnOnce('Memory API v1 is superseded; migrate to v2.')
  })
  resetWarnings()
  const second = await captured(() => {
    warnOnce('Memory API v1 is superseded; migrate to v2.')
  })
  assert.equal(first.length, 1)
  assert.equal(second.length, 1)
})
