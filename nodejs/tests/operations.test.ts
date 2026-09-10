/**
 * The operations that reach the wire, and the scope's delegation of them.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { Memco } from '../src/client.js'
import {
  MemcoInternalError,
  MemcoInvalidRequestError,
  MemcoNotFoundError,
  MemcoTimeoutError
} from '../src/errors.js'
import * as pb from '../src/internal/gen.js'
import { NEW_MEMORY } from '../src/internal/validate.js'
import { RevertOutcome } from '../src/types.js'
import { withHarness, type Harness } from './fakeServer.js'

function client(harness: Harness): Memco {
  return new Memco({ token: 'test-token', host: harness.address, tls: false })
}

test('fetching a memory returns what the service sent for that handle', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      const memory = await memco.memory.getMemory('memory-6oiv6b-1')
      assert.equal(memory.idx, 'memory-6oiv6b-1')
      assert.equal(
        (harness.memory.requests.get('getMemory') as pb.GetMemoryRequest).idx,
        'memory-6oiv6b-1'
      )
    } finally {
      await memco.close()
    }
  })
})

test('a reply carrying no memory at all is a not-found, not an empty memory', async () => {
  await withHarness(async harness => {
    // Field presence, not truthiness: an unset singular message decodes to a
    // default instance that is indistinguishable from a real empty memory.
    harness.memory.responses.set(
      'getMemory',
      pb.GetMemoryResponse.fromPartial({})
    )
    const memco = client(harness)
    try {
      await memco.connect()
      await assert.rejects(
        memco.memory.getMemory('memory-missing-1'),
        (error: unknown) => {
          assert.ok(error instanceof MemcoNotFoundError)
          assert.match(
            error.detail,
            /no memory was returned for "memory-missing-1"/
          )
          return true
        }
      )
    } finally {
      await memco.close()
    }
  })
})

test('enriching names the memory it extends, and the sentinel opens a new one', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      await memco.memory.enrichMemory({
        memoryIdx: NEW_MEMORY,
        sessionId: 'session-a',
        title: 'a title',
        content: 'some content'
      })
      const request = harness.memory.requests.get(
        'enrichMemory'
      ) as pb.EnrichMemoryRequest
      assert.equal(request.memoryIdx, 'new')
      assert.equal(request.sessionId, 'session-a')
    } finally {
      await memco.close()
    }
  })
})

test('a write that mints no operation id reports that it cannot be undone', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'createMemory',
      pb.CreateMemoryResponse.fromPartial({})
    )
    const memco = client(harness)
    try {
      await memco.connect()
      const written = await memco.memory.createMemory({
        query: 'a query',
        title: 'a title',
        content: 'some content',
        domain: 'coding'
      })
      // Empty on the wire becomes null, because absence is the meaningful
      // thing here: there is no handle to hand revertMemory.
      assert.equal(written.operationId, null)
    } finally {
      await memco.close()
    }
  })
})

test('a scope delegates every operation with its session already bound', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'startSession',
      pb.StartSessionResponse.fromPartial({
        sessionId: 'session-scoped',
        instructions: { content: 'search before you work' }
      })
    )
    const memco = client(harness)
    try {
      await memco.connect()
      const session = await memco.memory.withSession('coding')
      assert.equal(session.instructions.content, 'search before you work')

      const memory = await session.getMemory('memory-a-1')
      assert.equal(memory.idx, 'memory-a-1')

      await session.enrichMemory({
        memoryIdx: 'memory-a-1',
        title: 'a title',
        content: 'some content'
      })
      assert.equal(
        (harness.memory.requests.get('enrichMemory') as pb.EnrichMemoryRequest)
          .sessionId,
        'session-scoped'
      )

      const reverted = await session.revertMemory('create-a')
      assert.equal(reverted.outcome, RevertOutcome.MERGED)

      const imported = await session.importMemories([
        {
          queries: ['a query'],
          insights: [{ title: 'a title', content: 'content' }]
        }
      ])
      assert.equal(
        (
          harness.memory.requests.get(
            'importMemories'
          ) as pb.ImportMemoriesRequest
        ).sessionId,
        'session-scoped'
      )
      assert.equal(imported.results.length, 1)
    } finally {
      await memco.close()
    }
  })
})

test('a per-call timeout is honoured over the client default', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      // The server is made to answer far later than the per-call deadline
      // allows, so the deadline is the only thing that can end this call. An
      // in-process server answers in microseconds, so a short deadline raced
      // against an undelayed one decides nothing.
      harness.memory.delays.set('listDomains', 5_000)
      await assert.rejects(
        memco.memory.listDomains({ timeout: 0.05 }),
        MemcoTimeoutError
      )
    } finally {
      await memco.close()
    }
  })
})

// -- Memory.feedback() -------------------------------------------------------

/** A search response carrying one memory, for feedback tests. */
function searchResponse(): pb.SearchResponse {
  return pb.SearchResponse.fromPartial({
    sessionId: 'session-a',
    memories: [{ idx: 'memory-a-1' }]
  })
}

/** A shareFeedback response recording one entry, for feedback tests. */
function feedbackResponse(): pb.ShareFeedbackResponse {
  return pb.ShareFeedbackResponse.fromPartial({
    sessionId: 'session-a',
    entries: [{ idx: 'memory-a-1', relevant: true, correct: true }]
  })
}

test('feedback() on a memory from a plain search sends the right session and idx', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set('search', searchResponse())
    harness.memory.responses.set('shareFeedback', feedbackResponse())
    const memco = client(harness)
    try {
      await memco.connect()
      const result = await memco.memory.search('q', { domain: 'coding' })
      const entry = await result.memories[0]!.feedback({
        relevant: true,
        correct: true
      })
      const request = harness.memory.requests.get(
        'shareFeedback'
      ) as pb.ShareFeedbackRequest
      assert.equal(request.sessionId, 'session-a')
      assert.deepEqual(
        request.feedback.map(one => one.idx),
        ['memory-a-1']
      )
      assert.equal(entry.idx, 'memory-a-1')
    } finally {
      await memco.close()
    }
  })
})

test('feedback() on a memory from a session-bound search also works', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'startSession',
      pb.StartSessionResponse.fromPartial({ sessionId: 'session-a' })
    )
    harness.memory.responses.set('search', searchResponse())
    harness.memory.responses.set('shareFeedback', feedbackResponse())
    const memco = client(harness)
    try {
      await memco.connect()
      const session = await memco.memory.startSession('coding')
      const result = await session.search('q')
      await result.memories[0]!.feedback({ relevant: true, correct: true })
      assert.equal(
        (
          harness.memory.requests.get(
            'shareFeedback'
          ) as pb.ShareFeedbackRequest
        ).sessionId,
        'session-a'
      )
    } finally {
      await memco.close()
    }
  })
})

test('feedback() on a memory from getMemory is refused locally', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      const memory = await memco.memory.getMemory('memory-a-1')
      harness.forget()
      await assert.rejects(
        memory.feedback({ relevant: true, correct: true }),
        (error: unknown) => {
          assert.ok(error instanceof MemcoInvalidRequestError)
          assert.match(error.detail, /session_id/)
          return true
        }
      )
      // Refused before anything was sent, exactly as an explicit blank
      // sessionId passed to shareFeedback would be.
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('feedback() on a memory fetched through a session sends that session id', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'startSession',
      pb.StartSessionResponse.fromPartial({ sessionId: 'session-a' })
    )
    harness.memory.responses.set('shareFeedback', feedbackResponse())
    const memco = client(harness)
    try {
      await memco.connect()
      const session = await memco.memory.startSession('coding')
      const memory = await session.getMemory('memory-a-1')
      await memory.feedback({ relevant: true, correct: true })
      assert.equal(
        (
          harness.memory.requests.get(
            'shareFeedback'
          ) as pb.ShareFeedbackRequest
        ).sessionId,
        'session-a'
      )
    } finally {
      await memco.close()
    }
  })
})

test('feedback() throws a typed error when the service records no entry for the rating', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set('search', searchResponse())
    harness.memory.responses.set(
      'shareFeedback',
      pb.ShareFeedbackResponse.fromPartial({ sessionId: 'session-a' })
    )
    const memco = client(harness)
    try {
      await memco.connect()
      const result = await memco.memory.search('q', { domain: 'coding' })
      await assert.rejects(
        result.memories[0]!.feedback({ relevant: true, correct: true }),
        (error: unknown) => {
          assert.ok(error instanceof MemcoInternalError)
          assert.match(error.detail, /no feedback entry.*"memory-a-1"/)
          return true
        }
      )
    } finally {
      await memco.close()
    }
  })
})
