/**
 * Building the request messages, and the caps applied on the way.
 *
 * Two behaviours matter more than the field mapping: a cap the service has not
 * reported is not applied at all, and the two caps that trim server-side trim
 * here rather than refusing a call the service would have accepted.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { status } from '@grpc/grpc-js'

import { MemcoNotFoundError } from '../src/errors.js'
import * as pb from '../src/internal/gen.js'
import { Known } from '../src/internal/limits.js'
import {
  createMemoryRequest,
  enrichMemoryRequest,
  getMemoryRequest,
  importMemoriesRequests,
  listDomainsRequest,
  requireMemory,
  revertMemoryRequest,
  searchRequest,
  shareFeedbackRequest,
  startSessionRequest
} from '../src/internal/requests.js'
import {
  DataSource,
  type ImportedMemory,
  type Limits,
  type Tag
} from '../src/types.js'

/** A {@link Known} taught exactly the caps a test cares about. */
function taught(overrides: Partial<Limits> = {}, tagsPerDomain = 0): Known {
  const known = new Known()
  known.update(
    {
      maxQueryCharacters: 0,
      maxTextCharacters: 0,
      maxIdxCharacters: 0,
      maxSources: 0,
      maxFeedbackEntries: 0,
      maxImportMemories: 0,
      maxImportQueriesPerMemory: 0,
      maxImportInsightsPerMemory: 0,
      maxImportTagsPerMemory: 0,
      ...overrides
    },
    [
      {
        slug: 'coding',
        title: '',
        summary: '',
        whenToSearch: '',
        whenToSave: '',
        whatNotToSave: '',
        tagsDescription: '',
        filterTagTypes: [],
        versionTagTypes: [],
        maxTagsPerQuery: tagsPerDomain
      }
    ]
  )
  return known
}

function imported(overrides: Partial<ImportedMemory> = {}): ImportedMemory {
  return {
    queries: ['how does X work'],
    insights: [{ title: 'T', content: 'C' }],
    ...overrides
  }
}

test('listing domains asks nothing, the call being the answer to "which?"', () => {
  assert.deepEqual(listDomainsRequest(), {})
})

test('a revert addresses the write by the wire name of the field', () => {
  // The SDK's argument is operationId; the contract's field is op_id. Getting
  // this wrong reverts nothing and reports success.
  assert.equal(revertMemoryRequest('op-1').opId, 'op-1')
})

test('a session is opened on a named domain', () => {
  assert.equal(startSessionRequest('coding').domain, 'coding')
  assert.throws(() => startSessionRequest(' '), {
    detail: 'domain must not be empty'
  })
})

test('a search carries the query and whichever scope was given', () => {
  const request = searchRequest('how does X work', {
    domain: 'coding',
    sessionId: null
  })
  assert.equal(request.query, 'how does X work')
  assert.equal(request.domain, 'coding')
  // An absent scope half is the empty string on the wire, never undefined.
  assert.equal(request.sessionId, '')
  assert.deepEqual(request.tags, [])
})

test('a call naming neither a domain nor a session is refused before it is built', () => {
  assert.throws(() => searchRequest('q', { domain: null, sessionId: null }), {
    detail:
      'pass a domain or a session_id: a request needs one of them to name a domain'
  })
})

test('a tag without a version leaves the wire field unset', () => {
  // Set to an empty string it would be a version, and the service drops or
  // matches on one differently from an absent one.
  const request = searchRequest('q', {
    domain: 'coding',
    sessionId: null,
    tags: [
      { type: 'language', value: 'typescript' },
      { type: 'framework', value: 'express', version: '4' }
    ]
  })
  assert.equal(request.tags[0].version, undefined)
  assert.equal(request.tags[1].version, '4')
})

test('a create carries every field and the source it was given', () => {
  const request = createMemoryRequest({
    query: 'how does X work',
    title: 'T',
    content: 'C',
    domain: 'coding',
    sessionId: null,
    tags: null,
    source: DataSource.USER
  })
  assert.equal(request.query, 'how does X work')
  assert.equal(request.title, 'T')
  assert.equal(request.content, 'C')
  assert.equal(request.domain, 'coding')
  assert.equal(request.sessionId, '')
  assert.equal(request.source, pb.DataSource.DATA_SOURCE_USER)
})

test('an enrichment names its memory, its session and its sources', () => {
  const request = enrichMemoryRequest({
    memoryIdx: 'new',
    sessionId: 'session-a',
    title: 'T',
    content: 'C',
    tags: null,
    sources: ['memory-a-1', 'memory-a-2'],
    source: DataSource.AGENT
  })
  assert.equal(request.memoryIdx, 'new')
  assert.equal(request.sessionId, 'session-a')
  assert.deepEqual(request.sources, ['memory-a-1', 'memory-a-2'])
  assert.equal(request.source, pb.DataSource.DATA_SOURCE_AGENT)
})

test('a rating without a comment leaves the wire field unset', () => {
  const request = shareFeedbackRequest({
    sessionId: 'session-a',
    feedback: [
      { idx: 'memory-a-1', relevant: true, correct: false },
      { idx: 'memory-a-2', relevant: false, correct: true, comment: 'why' }
    ]
  })
  assert.equal(request.sessionId, 'session-a')
  assert.equal(request.feedback[0].comment, undefined)
  assert.equal(request.feedback[0].relevant, true)
  assert.equal(request.feedback[0].correct, false)
  assert.equal(request.feedback[1].comment, 'why')
})

// --- nothing is checked until the service has reported a number -----------

test('no cap is applied before the service has reported one', () => {
  // The whole design: an SDK carrying its own numbers refuses work the service
  // would have taken as soon as the service moves one of them.
  const request = createMemoryRequest({
    query: 'q'.repeat(5000),
    title: 't'.repeat(5000),
    content: 'c'.repeat(5000),
    domain: 'coding',
    sessionId: null,
    tags: Array.from({ length: 50 }, (_, at) => ({
      type: 'language',
      value: `v${at}`
    })),
    source: DataSource.AGENT
  })
  assert.equal(request.tags.length, 50)
})

test('an over-long query is refused once the cap is known', () => {
  assert.throws(
    () =>
      searchRequest('x'.repeat(11), {
        domain: 'coding',
        sessionId: null,
        known: taught({ maxQueryCharacters: 10 })
      }),
    { detail: 'query is 11 characters, which exceeds the limit of 10' }
  )
})

test('title and content are bounded together rather than each', () => {
  const known = taught({ maxTextCharacters: 100 })
  assert.throws(
    () =>
      createMemoryRequest({
        query: 'q',
        title: 't'.repeat(60),
        content: 'c'.repeat(60),
        domain: 'coding',
        sessionId: null,
        tags: null,
        source: DataSource.AGENT,
        known
      }),
    {
      detail:
        'title and content are 120 characters together, which exceeds the combined limit of 100'
    }
  )
  // Neither alone exceeds the cap, so a check on each would have let it through.
  createMemoryRequest({
    query: 'q',
    title: 't'.repeat(40),
    content: 'c'.repeat(40),
    domain: 'coding',
    sessionId: null,
    tags: null,
    source: DataSource.AGENT,
    known
  })
})

test('an over-long handle is refused once the cap is known', () => {
  const known = taught({ maxIdxCharacters: 8 })
  assert.throws(() => getMemoryRequest('m'.repeat(9), known), {
    detail: 'idx is 9 characters, which exceeds the limit of 8'
  })
  assert.throws(() => revertMemoryRequest('o'.repeat(9), known), {
    detail: 'operation_id is 9 characters, which exceeds the limit of 8'
  })
  assert.throws(
    () =>
      enrichMemoryRequest({
        memoryIdx: 'm',
        sessionId: 'session-a',
        title: 'T',
        content: 'C',
        tags: null,
        sources: ['s'.repeat(9)],
        source: DataSource.AGENT,
        known
      }),
    { detail: 'sources entry is 9 characters, which exceeds the limit of 8' }
  )
})

test('too many ratings are refused once the cap is known', () => {
  assert.throws(
    () =>
      shareFeedbackRequest({
        sessionId: 'session-a',
        feedback: Array.from({ length: 3 }, (_, at) => ({
          idx: `i${at}`,
          relevant: true,
          correct: true
        })),
        known: taught({ maxFeedbackEntries: 2 })
      }),
    { detail: 'feedback has 3 entries, which exceeds the limit of 2' }
  )
})

// --- the two caps that trim ----------------------------------------------

test('excess sources are trimmed rather than refused', () => {
  // The service keeps the first max_sources and drops the rest, so raising
  // would refuse a call it would have accepted.
  const request = enrichMemoryRequest({
    memoryIdx: 'new',
    sessionId: 'session-a',
    title: 'T',
    content: 'C',
    tags: null,
    sources: ['a', 'b', 'c', 'd'],
    source: DataSource.AGENT,
    known: taught({ maxSources: 2 })
  })
  assert.deepEqual(request.sources, ['a', 'b'])
})

test('excess tags are trimmed against the cap of the domain named', () => {
  const request = searchRequest('q', {
    domain: 'coding',
    sessionId: null,
    tags: [
      { type: 'language', value: 'python' },
      { type: 'language', value: 'go' },
      { type: 'language', value: 'rust' }
    ],
    known: taught({}, 2)
  })
  assert.equal(request.tags.length, 2)
})

test('a domain tag cap of zero means the domain sets no cap', () => {
  const request = searchRequest('q', {
    domain: 'coding',
    sessionId: null,
    tags: [
      { type: 'language', value: 'python' },
      { type: 'language', value: 'go' },
      { type: 'language', value: 'rust' }
    ],
    known: taught({}, 0)
  })
  assert.equal(request.tags.length, 3)
})

test('a search scoped by a session trims no tags, its domain being invisible', () => {
  const request = searchRequest('q', {
    domain: null,
    sessionId: 'session-a',
    tags: [
      { type: 'language', value: 'python' },
      { type: 'language', value: 'go' },
      { type: 'language', value: 'rust' }
    ],
    known: taught({}, 2)
  })
  assert.equal(request.tags.length, 3)
})

test('an enrichment trims no tags, since a session hides its domain', () => {
  // Deliberate: enrich is always scoped by a session, so there is no domain to
  // look a cap up under and the service does the trimming.
  const request = enrichMemoryRequest({
    memoryIdx: 'new',
    sessionId: 'session-a',
    title: 'T',
    content: 'C',
    tags: [
      { type: 'language', value: 'python' },
      { type: 'language', value: 'go' },
      { type: 'language', value: 'rust' }
    ],
    sources: null,
    source: DataSource.AGENT,
    known: taught({}, 2)
  })
  assert.equal(request.tags.length, 3)
})

// --- imports: split by the call cap, refused by the per-entry ones --------

test('a batch over the reported cap is split into calls rather than refused', () => {
  // The cap bounds one call, not one batch. A caller should not have to learn
  // the number and chunk against it.
  const built = [
    ...importMemoriesRequests(
      Array.from({ length: 5 }, () => imported()),
      {
        domain: 'coding',
        sessionId: null,
        known: taught({ maxImportMemories: 2 })
      }
    )
  ]
  assert.deepEqual(
    built.map(([offset]) => offset),
    [0, 2, 4]
  )
  assert.deepEqual(
    built.map(([, request]) => request.memories.length),
    [2, 2, 1]
  )
  assert.equal(built[0][1].domain, 'coding')
  assert.equal(built[0][1].sessionId, '')
})

test('an unreported call cap sends the whole batch in one call', () => {
  // No number reported means the service rules, here as everywhere: the SDK
  // does not invent a group size of its own to split against.
  const built = [
    ...importMemoriesRequests(
      Array.from({ length: 5 }, () => imported()),
      { domain: 'coding', sessionId: null }
    )
  ]
  assert.equal(built.length, 1)
  assert.equal(built[0][0], 0)
  assert.equal(built[0][1].memories.length, 5)
})

test('an imported memory reaches the wire with its queries and insights', () => {
  const built = [
    ...importMemoriesRequests(
      [
        imported({
          queries: ['a', 'b'],
          insights: [{ title: 'T', content: 'C' }],
          tags: [{ type: 'language', value: 'go' }]
        })
      ],
      { domain: null, sessionId: 'session-a' }
    )
  ]
  const memory = built[0][1].memories[0]
  assert.deepEqual(memory.queries, ['a', 'b'])
  assert.deepEqual(memory.insights, [{ title: 'T', content: 'C' }])
  assert.deepEqual(memory.tags, [{ type: 'language', value: 'go' }])
  assert.equal(built[0][1].sessionId, 'session-a')
  assert.equal(built[0][1].domain, '')
})

for (const [what, entry, expected] of [
  [
    'queries',
    { queries: ['a', 'b', 'c'] },
    'memories[1] queries has 3 entries, which exceeds the limit of 2'
  ],
  [
    'insights',
    {
      insights: [
        { title: 'T', content: 'C' },
        { title: 'T', content: 'C' },
        { title: 'T', content: 'C' }
      ]
    },
    'memories[1] insights has 3 entries, which exceeds the limit of 2'
  ],
  [
    'tags',
    {
      tags: [
        { type: 'language', value: 'a' },
        { type: 'language', value: 'b' },
        { type: 'language', value: 'c' }
      ]
    },
    'memories[1] tags has 3 entries, which exceeds the limit of 2'
  ]
] as [string, Partial<ImportedMemory>, string][]) {
  test(`too many ${what} on one entry is refused and names the entry`, () => {
    // A batch gives the caller no handle to address one memory by, so the
    // position is the only thing that says which of them has to be cut down.
    const caps: Partial<Limits> = {
      maxImportQueriesPerMemory: 2,
      maxImportInsightsPerMemory: 2,
      maxImportTagsPerMemory: 2
    }
    assert.throws(
      () => [
        ...importMemoriesRequests([imported(), imported(entry)], {
          domain: 'coding',
          sessionId: null,
          known: taught(caps)
        })
      ],
      { detail: expected }
    )
  })
}

test('an imported insight is bounded by the joint text cap and named', () => {
  const known = taught({ maxTextCharacters: 100 })
  assert.throws(
    () => [
      ...importMemoriesRequests(
        [
          imported({
            insights: [{ title: 't'.repeat(60), content: 'c'.repeat(60) }]
          })
        ],
        { domain: 'coding', sessionId: null, known }
      )
    ],
    {
      detail:
        'memories[0] insights[0] title and content are 120 characters together, which exceeds the combined limit of 100'
    }
  )
  importMemoriesRequests(
    [
      imported({
        insights: [{ title: 't'.repeat(40), content: 'c'.repeat(40) }]
      })
    ],
    { domain: 'coding', sessionId: null, known }
  )
})

test('the refusing import tag cap is checked before the trimming domain one', () => {
  // Two caps meet on the same field and disagree about what to do. Checking the
  // refusing one after the trim would make it unreachable.
  const known = taught({ maxImportTagsPerMemory: 2 }, 1)
  const tags = [
    { type: 'language', value: 'a' },
    { type: 'language', value: 'b' },
    { type: 'language', value: 'c' }
  ]
  assert.throws(
    () => [
      ...importMemoriesRequests([imported({ tags })], {
        domain: 'coding',
        sessionId: null,
        known
      })
    ],
    { detail: 'memories[0] tags has 3 entries, which exceeds the limit of 2' }
  )
  // Within the refusing cap, the trimming one still trims rather than raising.
  const built = [
    ...importMemoriesRequests([imported({ tags: tags.slice(0, 2) })], {
      domain: 'coding',
      sessionId: null,
      known
    })
  ]
  assert.equal(built[0][1].memories[0].tags.length, 1)
})

test('an empty batch is refused before any call is built', () => {
  assert.throws(
    () => [
      ...importMemoriesRequests([], { domain: 'coding', sessionId: null })
    ],
    { detail: 'memories must contain at least one memory' }
  )
})

// --- responses -----------------------------------------------------------

test('a memory the service did not return is reported as missing', () => {
  assert.throws(
    () => requireMemory(pb.GetMemoryResponse.fromPartial({}), 'memory-a-1'),
    (error: unknown) => {
      assert.ok(error instanceof MemcoNotFoundError)
      assert.equal(error.code, status.NOT_FOUND)
      assert.equal(error.detail, 'no memory was returned for "memory-a-1"')
      return true
    }
  )
})

test('a memory whose every field is empty is still a memory', () => {
  // Presence, not truthiness: an all-default message is what the service sends
  // for a memory with nothing in it, and converting it away would report a
  // handle as unresolved when it resolved.
  const response = pb.GetMemoryResponse.fromPartial({ memory: {} })
  assert.equal(requireMemory(response, 'memory-a-1').idx, '')
})

// --- text a JavaScript string can hold but protobuf cannot send ----------

test('an unpaired surrogate never escapes as an untyped failure', () => {
  // A lone surrogate arrives routinely from a mis-decoded filename or scraped
  // JSON. Whatever happens to it, the caller must not see a raw encoder error.
  const lone = 'before\uD800after'
  const request = createMemoryRequest({
    query: lone,
    title: lone,
    content: lone,
    domain: 'coding',
    sessionId: null,
    tags: null,
    source: DataSource.AGENT
  })
  assert.equal(request.query, lone)
})

test('a blank tag is named as blank, not as text that cannot be sent', () => {
  // Tags are validated inside the guard that turns an encoder refusal into a
  // typed error, because building one is where such a refusal would happen.
  // The guard must let a rejection that is already typed through unchanged, or
  // every blank tag is reported as an encoding failure.
  assert.throws(
    () =>
      searchRequest('q', {
        domain: 'coding',
        sessionId: null,
        tags: [{ type: '', value: 'go' }]
      }),
    { detail: 'tag type must not be empty' }
  )
})

test('a rating handle longer than the reported cap is refused', () => {
  assert.throws(
    () =>
      shareFeedbackRequest({
        sessionId: 'session-a',
        feedback: [{ idx: 'i'.repeat(9), relevant: true, correct: true }],
        known: taught({ maxIdxCharacters: 8 })
      }),
    { detail: 'feedback idx is 9 characters, which exceeds the limit of 8' }
  )
  // A handle within the cap goes through, so the check refuses rather than
  // rejecting every rating once a cap is known.
  shareFeedbackRequest({
    sessionId: 'session-a',
    feedback: [{ idx: 'i'.repeat(8), relevant: true, correct: true }],
    known: taught({ maxIdxCharacters: 8 })
  })
})

test('anything that goes wrong while building reaches the caller typed', () => {
  // The guarantee the guard exists for. ts-proto's messages are plain objects
  // and refuse nothing, so the realistic trigger is a caller object that
  // misbehaves when it is walked. The caller must not see a raw JavaScript
  // throw from a call that never left the process.
  // A real array — checkTags now refuses a non-array outright, so an object
  // pretending to be iterable no longer reaches `built()`. The throw has to
  // come from walking an entry instead, which is the same guarantee.
  const hostile = [
    {
      get type(): string {
        throw new TypeError('nope')
      },
      value: 'typescript'
    }
  ] as unknown as Tag[]
  assert.throws(
    () =>
      searchRequest('q', {
        domain: 'coding',
        sessionId: null,
        tags: hostile
      }),
    {
      name: 'MemcoInvalidRequestError',
      detail: 'a field value cannot be sent: TypeError: nope'
    }
  )
})

test('a null version or comment leaves its wire field unset', () => {
  // A JavaScript caller says "no value" with null as readily as by omitting the
  // field. Copied through, it would reach the encoder as a string field holding
  // a null.
  const search = searchRequest('q', {
    domain: 'coding',
    sessionId: null,
    tags: [
      { type: 'language', value: 'go', version: null as unknown as undefined }
    ]
  })
  assert.equal(search.tags[0].version, undefined)
  const feedback = shareFeedbackRequest({
    sessionId: 'session-a',
    feedback: [
      {
        idx: 'memory-a-1',
        relevant: true,
        correct: true,
        comment: null as unknown as undefined
      }
    ]
  })
  assert.equal(feedback.feedback[0].comment, undefined)
})

test('a fetch addresses the memory by the wire name of the field', () => {
  // The mirror of the opId test: getting a wire field name wrong fetches
  // nothing and reports success rather than failing.
  assert.equal(getMemoryRequest('memory-a-1').idx, 'memory-a-1')
})

test('a create scoped by a session carries the session and no domain', () => {
  const request = createMemoryRequest({
    query: 'q',
    title: 'T',
    content: 'C',
    domain: null,
    sessionId: 'session-a',
    tags: null,
    source: DataSource.AGENT
  })
  assert.equal(request.sessionId, 'session-a')
  assert.equal(request.domain, '')
})

test('an over-long memory handle is refused on an enrichment', () => {
  assert.throws(
    () =>
      enrichMemoryRequest({
        memoryIdx: 'm'.repeat(9),
        sessionId: 'session-a',
        title: 'T',
        content: 'C',
        tags: null,
        sources: null,
        source: DataSource.AGENT,
        known: taught({ maxIdxCharacters: 8 })
      }),
    { detail: 'memory_idx is 9 characters, which exceeds the limit of 8' }
  )
})

test("a trimmed enrichment leaves the caller's own array alone", () => {
  // The caller keeps whatever they passed in; the trim belongs to the request.
  const sources = ['a', 'b', 'c', 'd']
  enrichMemoryRequest({
    memoryIdx: 'new',
    sessionId: 'session-a',
    title: 'T',
    content: 'C',
    tags: null,
    sources,
    source: DataSource.AGENT,
    known: taught({ maxSources: 2 })
  })
  assert.deepEqual(sources, ['a', 'b', 'c', 'd'])
})
