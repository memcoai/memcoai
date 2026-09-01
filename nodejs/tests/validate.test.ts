/**
 * The structural checks that run before anything is sent.
 *
 * Two things are load-bearing here and are pinned as such: no numeric limit is
 * compiled in, and a "character" is a code point rather than a UTF-16 unit.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { status } from '@grpc/grpc-js'

import { MemcoInvalidRequestError } from '../src/errors.js'
import { DEFAULT_LEVEL, setLevel } from '../src/internal/logging.js'
import * as validate from '../src/internal/validate.js'
import type { ImportedMemory } from '../src/types.js'

function imported(overrides: Partial<ImportedMemory> = {}): ImportedMemory {
  return {
    queries: ['how does X work'],
    insights: [{ title: 'T', content: 'C' }],
    ...overrides
  }
}

/** Run `body` at DEBUG and return everything the SDK wrote to stderr. */
function logged(body: () => void): string {
  const written: string[] = []
  const write = process.stderr.write.bind(process.stderr)
  process.stderr.write = (chunk: unknown): boolean => {
    written.push(String(chunk))
    return true
  }
  setLevel('debug')
  try {
    body()
  } finally {
    process.stderr.write = write
    setLevel(DEFAULT_LEVEL)
  }
  return written.join('')
}

test('a blank value is refused by the name the caller wrote', () => {
  const checks: [(value: string) => void, string][] = [
    [validate.checkQuery, 'query'],
    [validate.checkTitle, 'title'],
    [value => validate.checkIdx(value), 'idx'],
    [validate.checkSessionId, 'session_id'],
    [validate.checkDomain, 'domain'],
    [validate.checkOperationId, 'operation_id'],
    [validate.checkContent, 'content']
  ]
  for (const [check, field] of checks) {
    assert.throws(
      () => {
        check('   ')
      },
      { detail: `${field} must not be empty` },
      `${field} should be refused when blank`
    )
  }
})

test('a value the caller never supplied is refused rather than crashing', () => {
  // An un-revertible write reports its operation id as null, and the documented
  // flow feeds that straight back in.
  for (const missing of [null, undefined]) {
    assert.throws(
      () => {
        validate.checkOperationId(missing as unknown as string)
      },
      { detail: 'operation_id must not be empty' }
    )
  }
})

test('a local rejection is indistinguishable from a server-side one', () => {
  assert.throws(
    () => {
      validate.checkQuery('')
    },
    (error: unknown) => {
      assert.ok(error instanceof MemcoInvalidRequestError)
      assert.equal(error.code, status.INVALID_ARGUMENT)
      assert.equal(error.name, 'MemcoInvalidRequestError')
      return true
    }
  )
})

test('no length is checked without the service having reported one', () => {
  // A number compiled in here would go stale the moment the service changed
  // it, and this SDK would refuse requests the service had started accepting.
  validate.checkQuery('q'.repeat(100_000))
  validate.checkTitle('t'.repeat(100_000))
  validate.checkContent('c'.repeat(100_000))
  validate.checkIdx('i'.repeat(100_000))
})

test('no batch size is checked without the service having reported one', () => {
  validate.checkSources(Array.from({ length: 1000 }, (_, at) => `src-${at}`))
  validate.checkFeedback(
    Array.from({ length: 1000 }, () => ({
      idx: 'i',
      relevant: true,
      correct: true
    }))
  )
  validate.checkImportMemories(Array.from({ length: 1000 }, () => imported()))
})

test('the module exports no cap of its own', () => {
  // A constant with a number in it is the failure mode the whole design is
  // arranged against: only the service knows what its caps are.
  assert.deepEqual(
    Object.keys(validate).filter(name => name.startsWith('MAX_')),
    []
  )
})

test('a cap of zero means the service reported none and nothing is checked', () => {
  validate.checkWithin('x'.repeat(1000), 'query', 0)
  validate.checkCount(1000, 'feedback', 0)
  assert.deepEqual(validate.trim(['a', 'b', 'c'], 0, 'tags'), ['a', 'b', 'c'])
})

test('a value over a reported cap is refused and counted', () => {
  assert.throws(
    () => {
      validate.checkWithin('xxxxx', 'query', 4)
    },
    { detail: 'query is 5 characters, which exceeds the limit of 4' }
  )
  validate.checkWithin('xxxx', 'query', 4)
})

test('a batch over a reported cap is refused and counted', () => {
  assert.throws(
    () => {
      validate.checkCount(5, 'feedback', 4)
    },
    { detail: 'feedback has 5 entries, which exceeds the limit of 4' }
  )
  validate.checkCount(4, 'feedback', 4)
})

test('characters are counted as code points, not as UTF-16 units', () => {
  // An astral character is one character to the service and two to
  // JavaScript's String.length. Counting the wrong one refuses text the service
  // would have accepted — and does it only for non-Latin callers.
  const five = '\u{1F44D}'.repeat(5)
  assert.equal(five.length, 10, 'the fixture must actually differ')
  assert.equal(Array.from(five).length, 5)
  validate.checkWithin(five, 'title', 5)
  assert.throws(
    () => {
      validate.checkWithin(five, 'title', 4)
    },
    { detail: 'title is 5 characters, which exceeds the limit of 4' }
  )
})

test('a list over a trimming cap keeps its first entries', () => {
  // The service keeps the first N and drops the rest, so raising here would
  // refuse a call it would have accepted.
  assert.deepEqual(validate.trim(['a', 'b', 'c', 'd'], 2, 'sources'), [
    'a',
    'b'
  ])
})

test('a list within a trimming cap is left exactly as it was', () => {
  const values = ['a', 'b']
  assert.equal(validate.trim(values, 2, 'sources'), values)
})

test('a trim is reported on the logger, being the only sign it happened', () => {
  // Three tags go in, two are sent, and nothing in the result says so. A debug
  // record is the caller's only way to find out their data was dropped.
  const records = logged(() => {
    validate.trim(['a', 'b', 'c'], 2, 'tags')
  })
  assert.match(records, /tags trimmed from 3 to 2 by the service's cap/)
})

test('nothing is reported when no trim happens', () => {
  const records = logged(() => {
    validate.trim(['a'], 2, 'tags')
  })
  assert.doesNotMatch(records, /trimmed/)
})

test('a scope names a domain, a session, or both', () => {
  validate.checkScope({ domain: 'coding', sessionId: null })
  validate.checkScope({ domain: null, sessionId: 'session-a' })
  // Both is allowed and left for the service to resolve.
  validate.checkScope({ domain: 'coding', sessionId: 'session-a' })
  assert.throws(
    () => {
      validate.checkScope({ domain: null, sessionId: null })
    },
    {
      detail:
        'pass a domain or a session_id: a request needs one of them to name a domain'
    }
  )
})

test('a scope that was given but left blank is still refused', () => {
  assert.throws(
    () => {
      validate.checkScope({ domain: ' ', sessionId: null })
    },
    { detail: 'domain must not be empty' }
  )
  assert.throws(
    () => {
      validate.checkScope({ domain: null, sessionId: ' ' })
    },
    { detail: 'session_id must not be empty' }
  )
})

test('the sentinel opening a new memory is exactly "new"', () => {
  assert.equal(validate.NEW_MEMORY, 'new')
  validate.checkMemoryIdx(validate.NEW_MEMORY)
  validate.checkMemoryIdx('memory-a-1')
  // "New" is not the sentinel; it is an ordinary handle, refused only if blank.
  validate.checkMemoryIdx('New')
  assert.notEqual('New', validate.NEW_MEMORY)
  assert.throws(
    () => {
      validate.checkMemoryIdx('')
    },
    { detail: 'memory_idx must not be empty' }
  )
})

test('a handle is refused under the argument name the caller wrote', () => {
  assert.throws(
    () => {
      validate.checkIdx('', 'memory_idx')
    },
    { detail: 'memory_idx must not be empty' }
  )
})

test('feedback must carry at least one rating', () => {
  assert.throws(
    () => {
      validate.checkFeedback([])
    },
    { detail: 'feedback must contain at least one rating' }
  )
})

test('a rating with a blank handle is refused', () => {
  assert.throws(
    () => {
      validate.checkFeedback([{ idx: '', relevant: true, correct: true }])
    },
    { detail: 'feedback idx must not be empty' }
  )
})

test('a blank tag is refused, since a filtering tag narrows to nothing', () => {
  assert.throws(
    () => {
      validate.checkTags([{ type: '', value: 'go' }])
    },
    { detail: 'tag type must not be empty' }
  )
  assert.throws(
    () => {
      validate.checkTags([{ type: 'language', value: ' ' }])
    },
    { detail: 'tag value must not be empty' }
  )
})

test('tags are materialised, and no tags is an empty list', () => {
  assert.deepEqual(validate.checkTags(null), [])
  assert.deepEqual(validate.checkTags(undefined), [])
  assert.deepEqual(validate.checkTags([{ type: 'language', value: 'go' }]), [
    { type: 'language', value: 'go' }
  ])
})

test('a call carrying more than one set of tags names which set is at fault', () => {
  assert.throws(
    () => {
      validate.checkTags([{ type: '', value: 'go' }], 'memories[0] tag')
    },
    { detail: 'memories[0] tag type must not be empty' }
  )
})

test('a single string is not a sequence of source handles', () => {
  // A JavaScript caller has no compiler to catch this, and iterating a string
  // would cite one memory per character.
  assert.throws(
    () => {
      validate.checkSources('memory-a-1' as unknown as string[])
    },
    { detail: 'sources must be a sequence of handles, not a single string' }
  )
})

test('a blank source handle is refused', () => {
  assert.throws(
    () => {
      validate.checkSources(['memory-a-1', ' '])
    },
    { detail: 'sources entry must not be empty' }
  )
})

test('sources are materialised, and no sources is an empty list', () => {
  assert.deepEqual(validate.checkSources(null), [])
  assert.deepEqual(validate.checkSources(['a']), ['a'])
})

test('an import must carry at least one memory', () => {
  assert.throws(
    () => {
      validate.checkImportMemories([])
    },
    { detail: 'memories must contain at least one memory' }
  )
})

test('an imported memory needs a query and an insight', () => {
  assert.throws(
    () => {
      validate.checkImportMemories([imported({ queries: [] })])
    },
    { detail: 'memories[0] must contain at least one query' }
  )
  assert.throws(
    () => {
      validate.checkImportMemories([imported({ insights: [] })])
    },
    { detail: 'memories[0] must contain at least one insight' }
  )
})

test('a single string is not a sequence of queries', () => {
  assert.throws(
    () => {
      validate.checkImportMemories([
        imported({ queries: 'how does X work' as unknown as string[] })
      ])
    },
    {
      detail:
        'memories[0] queries must be a sequence of queries, not a single string'
    }
  )
})

test('a blank field names the entry of the batch it is in', () => {
  // A batch gives the caller no handle to address one memory by, so the
  // position is the only way to say which of five hundred entries is at fault.
  assert.throws(
    () => {
      validate.checkImportMemories([imported(), imported({ queries: ['  '] })])
    },
    { detail: 'memories[1] queries[0] must not be empty' }
  )
  assert.throws(
    () => {
      validate.checkImportMemories([
        imported(),
        imported({ insights: [{ title: ' ', content: 'C' }] })
      ])
    },
    { detail: 'memories[1] insights[0] title must not be empty' }
  )
  assert.throws(
    () => {
      validate.checkImportMemories([
        imported(),
        imported({ insights: [{ title: 'T', content: '' }] })
      ])
    },
    { detail: 'memories[1] insights[0] content must not be empty' }
  )
  assert.throws(
    () => {
      validate.checkImportMemories([
        imported(),
        imported({ tags: [{ type: 'language', value: '' }] })
      ])
    },
    { detail: 'memories[1] tag value must not be empty' }
  )
})

test('a one-shot iterable survives rather than being silently dropped', () => {
  // Validation used to walk the caller's own object, and the request builder
  // then walked it again to size and build. A generator was empty by the second
  // pass, so the call sent nothing at all and reported success — and an import
  // mints no handle, so `results` coming back empty was the caller's only sign.
  // Materialising here, and building from what comes back, is what makes a lazy
  // source safe rather than refused.
  const walk = function* <T>(items: readonly T[]): Generator<T> {
    yield* items
  }
  const insights = [
    { title: 't0', content: 'c0' },
    { title: 't1', content: 'c1' }
  ]
  const tags = [
    { type: 'language', value: 'v0' },
    { type: 'framework', value: 'v1' }
  ]

  const batch = validate.checkImportMemories(
    walk([{ queries: ['a query'], insights }])
  )
  assert.equal(batch.length, 1)

  const [byQueries] = validate.checkImportMemories([
    { queries: walk(['q0', 'q1']), insights }
  ])
  assert.deepEqual(byQueries?.queries, ['q0', 'q1'])

  const [byInsights] = validate.checkImportMemories([
    { queries: ['a query'], insights: walk(insights) }
  ])
  assert.deepEqual(
    byInsights?.insights.map(insight => insight.title),
    ['t0', 't1']
  )

  const [byTags] = validate.checkImportMemories([
    { queries: ['a query'], insights, tags: walk(tags) }
  ])
  assert.deepEqual(
    byTags?.tags.map(tag => tag.value),
    ['v0', 'v1']
  )

  const rated = validate.checkFeedback(
    walk([
      { idx: 'memory-a-0', relevant: true, correct: true },
      { idx: 'memory-a-1', relevant: true, correct: true }
    ])
  )
  assert.deepEqual(
    rated.map(rating => rating.idx),
    ['memory-a-0', 'memory-a-1']
  )
})

test('an entry taken from the middle of a batch names its absolute position', () => {
  // A group is validated as it is reached, so without the offset every message
  // would say memories[0] whatever the caller actually sent.
  assert.throws(
    () =>
      validate.checkImportMemories(
        [{ queries: [], insights: [{ title: 't', content: 'c' }] }],
        7
      ),
    /memories\[7\] must contain at least one query/
  )
})
