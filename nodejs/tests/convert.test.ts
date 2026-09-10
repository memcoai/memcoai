/**
 * Turning generated messages into the SDK's public types.
 *
 * The conventions under test are the two the module documents: an empty string
 * becomes `null` where absence is meaningful, and a date the service sends is
 * kept as an ISO string only when it is genuinely one.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { MemcoInternalError } from '../src/errors.js'
import {
  feedbackSubmitter,
  toDomainList,
  toFeedbackResult,
  toImportResult,
  toMemory,
  toRevertResult,
  toSearchResult,
  toSessionFields,
  toWriteResult,
  type FeedbackCaller
} from '../src/internal/convert.js'
import * as pb from '../src/internal/gen.js'
import {
  ImportStatus,
  RevertOutcome,
  type FeedbackRating
} from '../src/types.js'

/** A shareFeedback stub that fails the test if it is ever actually called. */
const NO_SHARE: FeedbackCaller = () => {
  throw new Error('shareFeedback was not expected to be called in this test')
}

/** A submitter that fails the test if `feedback()` is ever actually called. */
const NO_FEEDBACK = feedbackSubmitter('unused', NO_SHARE)

test('an instructions part with nothing to say stays an empty string', () => {
  // The contract documents an empty part as a real state, so it is not folded
  // to null the way an absent notice or operation id is.
  const session = toSessionFields(
    pb.StartSessionResponse.fromPartial({ sessionId: 's' })
  )
  assert.equal(session.instructions.content, '')
  assert.equal(session.instructions.policy, '')
})

test('a response carrying no instructions message reads as nothing to say', () => {
  // ts-proto leaves an unset nested message undefined rather than handing back
  // a default instance, so the conversion is what has to turn it into the empty
  // instructions a caller can read without a guard.
  const session = toSessionFields(
    pb.StartSessionResponse.fromPartial({ sessionId: 's' })
  )
  assert.deepEqual(session.instructions, {
    content: '',
    policy: '',
    adding: '',
    rating: '',
    next: ''
  })
})

test('instructions are read part for part', () => {
  const session = toSessionFields(
    pb.StartSessionResponse.fromPartial({
      sessionId: 's',
      instructions: {
        content: 'c',
        policy: 'p',
        adding: 'a',
        rating: 'r',
        next: 'n'
      }
    })
  )
  assert.deepEqual(session.instructions, {
    content: 'c',
    policy: 'p',
    adding: 'a',
    rating: 'r',
    next: 'n'
  })
})

test('repeated fields become plain arrays', () => {
  const result = toDomainList(
    pb.ListDomainsResponse.fromPartial({
      domains: [{ slug: 'coding', filterTagTypes: ['language'] }]
    })
  )
  assert.ok(Array.isArray(result.domains))
  assert.deepEqual(result.domains[0].filterTagTypes, ['language'])
})

test('a domain entry is read field for field', () => {
  const result = toDomainList(
    pb.ListDomainsResponse.fromPartial({
      domains: [
        {
          slug: 'coding',
          title: 'Coding',
          summary: 'How this team builds software',
          whenToSearch: 'before writing code',
          whenToSave: 'after learning something',
          whatNotToSave: 'secrets',
          tagsDescription: 'language, framework',
          filterTagTypes: ['language'],
          versionTagTypes: ['framework'],
          maxTagsPerQuery: 4
        }
      ]
    })
  )
  assert.deepEqual(result.domains[0], {
    slug: 'coding',
    title: 'Coding',
    summary: 'How this team builds software',
    whenToSearch: 'before writing code',
    whenToSave: 'after learning something',
    whatNotToSave: 'secrets',
    tagsDescription: 'language, framework',
    filterTagTypes: ['language'],
    versionTagTypes: ['framework'],
    maxTagsPerQuery: 4
  })
})

test('an insight update is kept as the ISO day the service sent', () => {
  // Not a Date: reading a date-only value as a timestamp attaches the reader's
  // time zone to it, landing the day early for anyone west of UTC.
  const memory = toMemory(
    pb.MemoryResult.fromPartial({
      idx: 'memory-a-1',
      insights: [{ updated: '2026-08-26' }]
    }),
    NO_FEEDBACK
  )
  assert.equal(memory.insights[0].updated, '2026-08-26')
})

for (const raw of [
  '',
  'not-a-date',
  '2026-13-99',
  '2026-02-30',
  '2025-02-29',
  '2026-8-26',
  '20260826',
  '2026-08-26T00:00:00Z',
  '2026-08-26 ',
  '2026-08-26\n',
  '+2026-08-26',
  '0000-01-01',
  '0000-02-29'
]) {
  test(`an update the service sent as ${JSON.stringify(raw)} becomes null`, () => {
    // A malformed date is never fatal: the rest of the insight is still useful.
    // Every value here is one that `new Date(raw)` would have accepted or
    // silently shifted, which is why the parse is hand-rolled.
    const memory = toMemory(
      pb.MemoryResult.fromPartial({ idx: 'm', insights: [{ updated: raw }] }),
      NO_FEEDBACK
    )
    assert.equal(memory.insights[0].updated, null)
  })
}

test('a leap day is a real date in a leap year', () => {
  const memory = toMemory(
    pb.MemoryResult.fromPartial({
      idx: 'm',
      insights: [{ updated: '2024-02-29' }]
    }),
    NO_FEEDBACK
  )
  assert.equal(memory.insights[0].updated, '2024-02-29')
})

test('empty optional strings become null', () => {
  const search = toSearchResult(
    pb.SearchResponse.fromPartial({ sessionId: 's', notice: '' }),
    NO_SHARE
  )
  assert.equal(search.notice, null)
  assert.equal(
    toMemory(pb.MemoryResult.fromPartial({ idx: 'm' }), NO_FEEDBACK).reference,
    null
  )
  const feedback = toFeedbackResult(
    pb.ShareFeedbackResponse.fromPartial({
      sessionId: 's',
      entries: [{ idx: 'i', advice: '' }]
    })
  )
  assert.equal(feedback.entries[0].advice, null)
})

test('populated optional strings are kept', () => {
  const search = toSearchResult(
    pb.SearchResponse.fromPartial({ sessionId: 's', notice: 'heads up' }),
    NO_SHARE
  )
  assert.equal(search.notice, 'heads up')
  const memory = toMemory(
    pb.MemoryResult.fromPartial({ idx: 'm', reference: 'memory-a-1' }),
    NO_FEEDBACK
  )
  assert.equal(memory.reference, 'memory-a-1')
})

test('an empty operation id marks a write that cannot be undone', () => {
  assert.equal(
    toWriteResult(pb.CreateMemoryResponse.fromPartial({ operationId: '' }))
      .operationId,
    null
  )
  assert.equal(
    toWriteResult(pb.CreateMemoryResponse.fromPartial({ operationId: 'op-1' }))
      .operationId,
    'op-1'
  )
})

test('a create response and an enrich response convert identically', () => {
  // The two messages are structurally the same, which is why one converter
  // serves both. If they ever diverge this is what says so.
  const created = toWriteResult(
    pb.CreateMemoryResponse.fromPartial({
      operationId: 'op-1',
      instructions: { next: 'revert with op-1' }
    })
  )
  const enriched = toWriteResult(
    pb.EnrichMemoryResponse.fromPartial({
      operationId: 'op-1',
      instructions: { next: 'revert with op-1' }
    })
  )
  assert.deepEqual(created, enriched)
})

test('an absent limits message means validate nothing, not validate zero', () => {
  // Reading an unset message as zeros would reject every call before it was
  // sent, locally, with no way for the caller to tell why.
  const result = toDomainList(
    pb.ListDomainsResponse.fromPartial({ domains: [{ slug: 'coding' }] })
  )
  assert.equal(result.limits, null)
})

test('a limits message is read field for field', () => {
  const result = toDomainList(
    pb.ListDomainsResponse.fromPartial({
      limits: {
        maxQueryCharacters: 100,
        maxTextCharacters: 200,
        maxIdxCharacters: 32,
        maxSources: 3,
        maxFeedbackEntries: 5,
        maxImportMemories: 50,
        maxImportQueriesPerMemory: 6,
        maxImportInsightsPerMemory: 7,
        maxImportTagsPerMemory: 8
      }
    })
  )
  assert.deepEqual(result.limits, {
    maxQueryCharacters: 100,
    maxTextCharacters: 200,
    maxIdxCharacters: 32,
    maxSources: 3,
    maxFeedbackEntries: 5,
    maxImportMemories: 50,
    maxImportQueriesPerMemory: 6,
    maxImportInsightsPerMemory: 7,
    maxImportTagsPerMemory: 8
  })
})

test('the deprecation fields reach the caller', () => {
  const result = toDomainList(
    pb.ListDomainsResponse.fromPartial({
      deprecated: true,
      deprecationMessage: 'Memory API v1 is superseded; migrate to v2.',
      sunsetDate: '2027-01-01'
    })
  )
  assert.equal(result.deprecated, true)
  assert.equal(
    result.deprecationMessage,
    'Memory API v1 is superseded; migrate to v2.'
  )
  assert.equal(result.sunsetDate, '2027-01-01')
})

test('an older service reporting no deprecation says nothing', () => {
  const result = toDomainList(pb.ListDomainsResponse.fromPartial({}))
  assert.equal(result.deprecated, false)
  assert.equal(result.deprecationMessage, '')
  assert.equal(result.sunsetDate, null)
})

test('the answering build reaches the caller', () => {
  // It names the build that answered, which is what a bug report quotes. It is
  // not provenance().serverCommit, which is the commit this package was cut at.
  assert.equal(
    toDomainList(
      pb.ListDomainsResponse.fromPartial({ serverCommit: '8317b7b' })
    ).serverCommit,
    '8317b7b'
  )
  assert.equal(
    toDomainList(pb.ListDomainsResponse.fromPartial({})).serverCommit,
    ''
  )
})

test('a revert carries the outcome as a typed value', () => {
  const result = toRevertResult(
    pb.RevertMemoryResponse.fromPartial({
      operationId: 'op-1',
      outcome: pb.RevertOutcome.REVERT_OUTCOME_MEMORY_REMOVED
    })
  )
  assert.equal(result.outcome, RevertOutcome.MEMORY_REMOVED)
  assert.equal(result.operationId, 'op-1')
})

test('a revert outcome this SDK predates folds to unspecified', () => {
  const result = toRevertResult(
    pb.RevertMemoryResponse.fromPartial({ outcome: 999 as pb.RevertOutcome })
  )
  assert.equal(result.outcome, RevertOutcome.UNSPECIFIED)
  assert.equal(result.operationId, null)
})

test('an import carries one typed outcome per entry', () => {
  // The indices are deliberately not their own positions: reading them off the
  // array index instead of off the field would pass an in-order fixture.
  const result = toImportResult([
    [
      0,
      pb.ImportMemoriesResponse.fromPartial({
        results: [
          { index: 2, status: pb.ImportStatus.IMPORT_STATUS_QUEUED },
          { index: 0, status: pb.ImportStatus.IMPORT_STATUS_DUPLICATE },
          {
            index: 1,
            status: pb.ImportStatus.IMPORT_STATUS_REJECTED,
            errors: ['queries must not be empty']
          }
        ]
      })
    ]
  ])
  assert.deepEqual(
    result.results.map(outcome => outcome.index),
    [2, 0, 1]
  )
  assert.equal(result.results[0].status, ImportStatus.QUEUED)
  assert.equal(result.results[1].status, ImportStatus.DUPLICATE)
  assert.deepEqual(result.results[2].errors, ['queries must not be empty'])
  // An entry that was queued carries an empty list, not a null.
  assert.deepEqual(result.results[0].errors, [])
})

test('an import status this SDK predates folds to unspecified', () => {
  const result = toImportResult([
    [
      0,
      pb.ImportMemoriesResponse.fromPartial({
        results: [{ index: 0, status: 999 as pb.ImportStatus }]
      })
    ]
  ])
  assert.equal(result.results[0].status, ImportStatus.UNSPECIFIED)
})

test('split groups are renumbered against the whole batch', () => {
  // Each call numbers its own results from zero. Merging them without the
  // offset would report index 0 once per group and identify nothing.
  const result = toImportResult([
    [
      0,
      pb.ImportMemoriesResponse.fromPartial({
        results: [
          { index: 0, status: pb.ImportStatus.IMPORT_STATUS_QUEUED },
          { index: 1, status: pb.ImportStatus.IMPORT_STATUS_QUEUED }
        ],
        instructions: { content: 'first' }
      })
    ],
    [
      2,
      pb.ImportMemoriesResponse.fromPartial({
        results: [
          { index: 0, status: pb.ImportStatus.IMPORT_STATUS_DUPLICATE },
          { index: 1, status: pb.ImportStatus.IMPORT_STATUS_QUEUED }
        ],
        instructions: { content: 'second' }
      })
    ]
  ])
  assert.deepEqual(
    result.results.map(outcome => outcome.index),
    [0, 1, 2, 3]
  )
  assert.equal(result.results[2].status, ImportStatus.DUPLICATE)
  // The calls are one operation in one domain, so one set of guidance is right.
  assert.equal(result.instructions.content, 'first')
})

test('a search result nests its memories and their insights', () => {
  const result = toSearchResult(
    pb.SearchResponse.fromPartial({
      sessionId: 'session-a',
      memories: [
        {
          idx: 'memory-a-1',
          kind: 'insight',
          timesServed: 3,
          intents: ['why does X happen'],
          insights: [
            {
              idx: 'memory-a-1-insight-1',
              title: 'T',
              content: 'C',
              updated: '2026-08-26',
              timesServed: 2,
              endorsed: 1,
              disputed: 0
            }
          ]
        }
      ]
    }),
    NO_SHARE
  )
  assert.equal(result.sessionId, 'session-a')
  const memory = result.memories[0]
  assert.equal(memory.idx, 'memory-a-1')
  assert.equal(memory.kind, 'insight')
  assert.equal(memory.timesServed, 3)
  assert.deepEqual(memory.intents, ['why does X happen'])
  assert.deepEqual(memory.insights[0], {
    idx: 'memory-a-1-insight-1',
    title: 'T',
    content: 'C',
    updated: '2026-08-26',
    timesServed: 2,
    endorsed: 1,
    disputed: 0
  })
})

test('a feedback result carries what the service recorded per rating', () => {
  const result = toFeedbackResult(
    pb.ShareFeedbackResponse.fromPartial({
      sessionId: 'session-a',
      entries: [
        {
          idx: 'memory-a-1',
          relevant: true,
          correct: false,
          advice: 'check it'
        }
      ]
    })
  )
  assert.equal(result.sessionId, 'session-a')
  assert.deepEqual(result.entries[0], {
    idx: 'memory-a-1',
    relevant: true,
    correct: false,
    advice: 'check it'
  })
})

test('a repeated field is copied, not aliased to the message', () => {
  // A caller holding the result must not see it change when the message it
  // came from is reused or recycled.
  const message = pb.ListDomainsResponse.fromPartial({
    domains: [{ slug: 'coding', filterTagTypes: ['language'] }]
  })
  const result = toDomainList(message)
  message.domains[0].filterTagTypes.push('framework')
  assert.deepEqual(result.domains[0].filterTagTypes, ['language'])
})

test('a sunset date the service malformed becomes null, not a throw', () => {
  // The notice is still worth showing without its date.
  const result = toDomainList(
    pb.ListDomainsResponse.fromPartial({
      deprecated: true,
      deprecationMessage: 'migrate to v2',
      sunsetDate: 'whenever'
    })
  )
  assert.equal(result.sunsetDate, null)
  assert.equal(result.deprecationMessage, 'migrate to v2')
})

// -- feedback wiring --------------------------------------------------------

test("a memory's feedback() calls the submitter with its own idx", async () => {
  const calls: [string, unknown][] = []
  const memory = toMemory(
    pb.MemoryResult.fromPartial({ idx: 'memory-a-1' }),
    async (idx, rating) => {
      calls.push([idx, rating])
      return {
        idx,
        relevant: rating.relevant,
        correct: rating.correct,
        advice: null
      }
    }
  )
  const entry = await memory.feedback({ relevant: true, correct: false })
  assert.deepEqual(calls, [['memory-a-1', { relevant: true, correct: false }]])
  assert.deepEqual(entry, {
    idx: 'memory-a-1',
    relevant: true,
    correct: false,
    advice: null
  })
})

test('a submitter forwards a comment to the caller unchanged', async () => {
  const received: unknown[] = []
  const submit = feedbackSubmitter('session-a', async options => {
    received.push([...options.feedback])
    return {
      sessionId: 'session-a',
      entries: [
        { idx: 'memory-a-1', relevant: true, correct: true, advice: null }
      ],
      instructions: {
        content: '',
        policy: '',
        adding: '',
        rating: '',
        next: ''
      }
    }
  })
  await submit('memory-a-1', { relevant: true, correct: true, comment: 'why' })
  assert.deepEqual(received, [
    [{ idx: 'memory-a-1', relevant: true, correct: true, comment: 'why' }]
  ])
})

test('a submitter throws a typed error rather than returning undefined when entries is empty', async () => {
  const submit = feedbackSubmitter('session-a', async () => ({
    sessionId: 'session-a',
    entries: [],
    instructions: {
      content: '',
      policy: '',
      adding: '',
      rating: '',
      next: ''
    }
  }))
  await assert.rejects(
    submit('memory-a-1', { relevant: true, correct: true }),
    (error: unknown) => {
      assert.ok(error instanceof MemcoInternalError)
      assert.match(error.detail, /no feedback entry.*"memory-a-1"/)
      return true
    }
  )
})

test('toSearchResult builds its submitter from the response own session id', async () => {
  const seen: Array<{
    sessionId: string
    rating: Pick<FeedbackRating, 'idx' | 'relevant' | 'correct'>
  }> = []
  const result = toSearchResult(
    pb.SearchResponse.fromPartial({
      sessionId: 'session-from-response',
      memories: [{ idx: 'memory-a-1' }, { idx: 'memory-a-2' }]
    }),
    async options => {
      const [rating] = [...options.feedback]
      seen.push({
        sessionId: options.sessionId,
        rating: {
          idx: rating!.idx,
          relevant: rating!.relevant,
          correct: rating!.correct
        }
      })
      return {
        sessionId: options.sessionId,
        entries: [{ idx: 'x', relevant: true, correct: true, advice: null }],
        instructions: {
          content: '',
          policy: '',
          adding: '',
          rating: '',
          next: ''
        }
      }
    }
  )
  await result.memories[0]!.feedback({ relevant: true, correct: true })
  await result.memories[1]!.feedback({ relevant: false, correct: false })
  // Every memory in the result shares the one submitter, built from the
  // response's own session id rather than one supplied by the caller — and
  // each call still carries its own memory's idx and its own rating, so a
  // regression where every memory closed over the same idx would fail here.
  assert.deepEqual(seen, [
    {
      sessionId: 'session-from-response',
      rating: { idx: 'memory-a-1', relevant: true, correct: true }
    },
    {
      sessionId: 'session-from-response',
      rating: { idx: 'memory-a-2', relevant: false, correct: false }
    }
  ])
})
