/**
 * The session-scoped view: what it binds, and when it opens.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { status } from '@grpc/grpc-js'

import { Memco } from '../src/client.js'
import {
  MemcoAPIError,
  MemcoInvalidRequestError,
  MemcoNotFoundError,
  MemcoUnavailableError
} from '../src/errors.js'
import { Session } from '../src/operations.js'
import * as pb from '../src/internal/gen.js'
import { Refusal, withHarness, type Harness } from './fakeServer.js'

function client(harness: Harness): Memco {
  return new Memco({ token: 'test-token', host: harness.address, tls: false })
}

test('startSession returns a session with every bound method already on it', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'startSession',
      pb.StartSessionResponse.fromPartial({ sessionId: 'session-direct' })
    )
    const memco = client(harness)
    try {
      await memco.connect()
      const session = await memco.memory.startSession('coding')
      assert.equal(session.id, 'session-direct')

      await session.search('anything')
      assert.equal(
        (harness.memory.requests.get('search') as pb.SearchRequest).sessionId,
        'session-direct'
      )
    } finally {
      await memco.close()
    }
  })
})

test('withSession opens nothing until it is awaited', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()
      memco.memory.withSession('coding')
      // Proven by absence: an opener that is created and dropped costs nothing,
      // which is what makes it safe to build one speculatively.
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('awaiting an opener twice opens one session', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()
      const opener = memco.memory.withSession('coding')
      const first = await opener
      const second = await opener
      assert.equal(first, second)
      // The domains are listed first; the rest sorted rather than in an exact
      // order: startSession runs the two calls concurrently, so which one the
      // fake server sees first is not settled.
      assert.equal(harness.memory.calls[0], 'listDomains')
      assert.deepEqual(harness.memory.calls.slice(1).sort(), [
        'listTools',
        'startSession'
      ])
    } finally {
      await memco.close()
    }
  })
})

test('a scope binds its session onto every call that takes one', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'startSession',
      pb.StartSessionResponse.fromPartial({ sessionId: 'session-bound' })
    )
    const memco = client(harness)
    try {
      await memco.connect()
      const session = await memco.memory.withSession('coding')
      assert.equal(session.id, 'session-bound')

      await session.search('anything')
      assert.equal(
        (harness.memory.requests.get('search') as pb.SearchRequest).sessionId,
        'session-bound'
      )

      await session.createMemory({ query: 'q', title: 't', content: 'c' })
      assert.equal(
        (harness.memory.requests.get('createMemory') as pb.CreateMemoryRequest)
          .sessionId,
        'session-bound'
      )

      await session.shareFeedback({
        feedback: [{ idx: 'memory-a-1', relevant: true, correct: true }]
      })
      assert.equal(
        (
          harness.memory.requests.get(
            'shareFeedback'
          ) as pb.ShareFeedbackRequest
        ).sessionId,
        'session-bound'
      )
    } finally {
      await memco.close()
    }
  })
})

test('a scope carries the session id but never a domain', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      const session = await memco.memory.withSession('coding')
      await session.search('anything')
      // A scope that let a domain through would let a caller search one domain
      // while recording it against a session opened in another.
      assert.equal(
        (harness.memory.requests.get('search') as pb.SearchRequest).domain,
        ''
      )
    } finally {
      await memco.close()
    }
  })
})

test('disposing a scope releases nothing and sends nothing', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      let held: Session
      {
        await using session = await memco.memory.withSession('coding')
        held = session
        harness.forget()
      }
      // The contract has no operation that ends a session, so leaving the block
      // is a claim about the reader's attention, not about a resource — and the
      // scope keeps working afterwards.
      assert.deepEqual(harness.memory.calls, [])
      await held.search('still usable')
      assert.deepEqual(harness.memory.calls, ['search'])
    } finally {
      await memco.close()
    }
  })
})

test('an import batch is split by the reported cap and renumbered for the caller', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'listDomains',
      pb.ListDomainsResponse.fromPartial({
        limits: { maxImportMemories: 2 },
        domains: [{ slug: 'coding', maxTagsPerQuery: 0 }]
      })
    )
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()
      const batch = Array.from({ length: 5 }, (_, index) => ({
        queries: [`query ${index}`],
        insights: [{ title: `title ${index}`, content: `content ${index}` }]
      }))
      const result = await memco.memory.importMemories(batch, {
        domain: 'coding'
      })
      // Three calls of 2, 2 and 1 — but the caller sees one flat run of indices
      // into the array they passed, whatever the service's batch limit is.
      assert.deepEqual(harness.memory.calls, [
        'importMemories',
        'importMemories',
        'importMemories'
      ])
      assert.deepEqual(
        result.results.map(outcome => outcome.index),
        [0, 1, 2, 3, 4]
      )
    } finally {
      await memco.close()
    }
  })
})

test('reverting names the operation id on the wire field the contract calls op_id', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      const reverted = await memco.memory.revertMemory('create-a')
      assert.equal(
        (harness.memory.requests.get('revertMemory') as pb.RevertMemoryRequest)
          .opId,
        'create-a'
      )
      assert.equal(reverted.operationId, 'create-a')
    } finally {
      await memco.close()
    }
  })
})

test('an opener that failed to open can be awaited again', async () => {
  await withHarness(async harness => {
    // StartSession is deliberately outside the retry policy, so nothing absorbs
    // a blip here. Caching the rejection would poison the opener for good.
    harness.memory.transientErrors.set('startSession', [
      { code: status.UNAVAILABLE, details: 'starting up' }
    ])
    const memco = client(harness)
    try {
      await memco.connect()
      const opener = memco.memory.withSession('coding')
      // Wrapped: assert.rejects takes a Promise or a thunk, and an opener is a
      // PromiseLike rather than a Promise.
      await assert.rejects(async () => {
        await opener
      }, MemcoUnavailableError)
      const session = await opener
      assert.equal(session.id, 'session-a')
      assert.equal(
        harness.memory.calls.filter(name => name === 'startSession').length,
        2
      )
    } finally {
      await memco.close()
    }
  })
})

test('a batch taken from a generator is split across calls without losing any of it', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'listDomains',
      pb.ListDomainsResponse.fromPartial({
        limits: { maxImportMemories: 2 },
        domains: [{ slug: 'coding', maxTagsPerQuery: 0 }]
      })
    )
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()

      const walk = function* <T>(items: readonly T[]): Generator<T> {
        yield* items
      }
      const insights = [{ title: 'a title', content: 'some content' }]
      // Lazy at every level, which is the shape a caller reading from a file or
      // a cursor would naturally build.
      const batch = walk(
        Array.from({ length: 5 }, (_, n) => ({
          queries: walk([`query ${n}`]),
          insights: walk(insights),
          tags: walk([{ type: 'language', value: 'typescript' }])
        }))
      )

      const result = await memco.memory.importMemories(batch, {
        domain: 'coding'
      })

      // Three calls of 2, 2 and 1. Taking the groups with `for...of` would call
      // return() on the generator at the first break, finishing it — one call
      // would go out and the rest of the batch would vanish without an error.
      assert.equal(
        harness.memory.calls.filter(name => name === 'importMemories').length,
        3
      )
      assert.deepEqual(
        result.results.map(outcome => outcome.index),
        [0, 1, 2, 3, 4]
      )
      const last = harness.memory.requests.get(
        'importMemories'
      ) as pb.ImportMemoriesRequest
      assert.equal(last.memories.length, 1)
      assert.equal(last.memories[0]?.insights.length, 1)
      assert.equal(last.memories[0]?.tags.length, 1)
    } finally {
      await memco.close()
    }
  })
})

test('a blank domain is refused before anything is sent, the catalog fetch included', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()
      await assert.rejects(memco.memory.startSession(' '), {
        name: 'MemcoInvalidRequestError'
      })
      // Long enough for a call sent anyway to have reached the server.
      await new Promise(settle => setTimeout(settle, 50))
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

// -- resuming a session ---------------------------------------------------

/** A domain listing carrying one entry for `coding`. */
function codingListed(): pb.ListDomainsResponse {
  return pb.ListDomainsResponse.fromPartial({
    domains: [{ slug: 'coding', title: 'Software Development' }]
  })
}

test('resuming a session reuses its id and starts none', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()
      const session = await memco.memory.resumeSession('session-stored')
      assert.equal(session.id, 'session-stored')
      // The domains are listed, then the session looked up and the catalog
      // fetched together, so tools() is shaped by the caller's role.
      assert.equal(harness.memory.calls[0], 'listDomains')
      assert.deepEqual(harness.memory.calls.slice(1).sort(), [
        'getSession',
        'listTools'
      ])
      assert.equal(
        (harness.memory.requests.get('getSession') as pb.GetSessionRequest)
          .sessionId,
        'session-stored'
      )

      await session.search('anything')
      assert.equal(
        (harness.memory.requests.get('search') as pb.SearchRequest).sessionId,
        'session-stored'
      )
    } finally {
      await memco.close()
    }
  })
})

test('a resumed session carries no opening instructions', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      const session = await memco.memory.resumeSession('session-stored')
      assert.deepEqual(session.instructions, {
        content: '',
        policy: '',
        adding: '',
        rating: '',
        next: ''
      })
    } finally {
      await memco.close()
    }
  })
})

test('a blank session id is refused before anything is sent', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()
      await assert.rejects(
        memco.memory.resumeSession('  '),
        MemcoInvalidRequestError
      )
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('a session knows the entry for its domain', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set('listDomains', codingListed())
    const memco = client(harness)
    try {
      await memco.connect()
      const started = await memco.memory.startSession('coding')
      assert.equal(started.domain?.slug, 'coding')
      assert.equal(started.domain?.title, 'Software Development')
    } finally {
      await memco.close()
    }
  })
})

test('a session in a domain the listing did not describe has no entry', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set('listDomains', codingListed())
    const memco = client(harness)
    try {
      await memco.connect()
      const session = await memco.memory.startSession('knowledge')
      assert.equal(session.domain, null)
    } finally {
      await memco.close()
    }
  })
})

test('a session takes its entry from its own listing, not an earlier one', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set('listDomains', codingListed())
    const memco = client(harness)
    try {
      await memco.connect()
      harness.memory.responses.set(
        'listDomains',
        pb.ListDomainsResponse.fromPartial({})
      )
      const session = await memco.memory.startSession('coding')
      assert.equal(session.domain, null)
    } finally {
      await memco.close()
    }
  })
})

test('a resumed session knows the domain the service says it runs in', async () => {
  // The domain belongs to the session: the service reports it, and supplies it
  // on every call the session makes, so a caller cannot name another one.
  await withHarness(async harness => {
    harness.memory.responses.set(
      'listDomains',
      pb.ListDomainsResponse.fromPartial({
        domains: [
          { slug: 'coding', title: 'Software Development' },
          { slug: 'knowledge', title: 'Company Knowledge' }
        ]
      })
    )
    harness.memory.responses.set(
      'getSession',
      pb.GetSessionResponse.fromPartial({
        sessionId: 'session-stored',
        domain: 'knowledge'
      })
    )
    const memco = client(harness)
    try {
      const session = await memco.memory.resumeSession('session-stored')
      assert.equal(session.domain?.slug, 'knowledge')
      await session.search('anything')
      assert.equal(
        (harness.memory.requests.get('search') as pb.SearchRequest).domain,
        ''
      )
    } finally {
      await memco.close()
    }
  })
})

test('a session does not open when its domains cannot be listed', async () => {
  // Strict, unlike the catalog: a session's calls need its domain, so one
  // opened without the listing would only fail later, further from the cause.
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      harness.memory.responses.set('listDomains', () => {
        throw new Refusal({ code: status.INTERNAL, details: 'listing broke' })
      })
      harness.forget()
      await assert.rejects(memco.memory.startSession('coding'), MemcoAPIError)
      await assert.rejects(
        memco.memory.resumeSession('session-stored'),
        MemcoAPIError
      )
      assert.deepEqual(harness.memory.calls, ['listDomains', 'listDomains'])
    } finally {
      await memco.close()
    }
  })
})

test('an invalid key lifetime is refused without an externalId too', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()
      await assert.rejects(
        memco.memory.startSession('coding', { keyTtlMinutes: -5 }),
        MemcoInvalidRequestError
      )
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('resuming a session the service does not know is refused', async () => {
  await withHarness(async harness => {
    harness.memory.transientErrors.set('getSession', [
      { code: status.NOT_FOUND, details: 'no such session' }
    ])
    const memco = client(harness)
    try {
      await assert.rejects(
        memco.memory.resumeSession('session-mistyped'),
        MemcoNotFoundError
      )
    } finally {
      await memco.close()
    }
  })
})

test('getSession reports what a session runs in and what happened under it', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'getSession',
      pb.GetSessionResponse.fromPartial({
        sessionId: 'session-stored',
        domain: 'coding',
        createdAt: 1_700_000_000,
        source: 'cli',
        searches: 7,
        feedbacks: 3,
        assists: 2,
        memoriesCreated: 1,
        enrichments: 4
      })
    )
    const memco = client(harness)
    try {
      assert.deepEqual(await memco.memory.getSession('session-stored'), {
        id: 'session-stored',
        domain: 'coding',
        createdAt: new Date(1_700_000_000_000),
        source: 'cli',
        searches: 7,
        feedbacks: 3,
        assists: 2,
        memoriesCreated: 1,
        enrichments: 4
      })
    } finally {
      await memco.close()
    }
  })
})

test('a session looks itself up by its own id', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      const session = await memco.memory.startSession('coding')
      const info = await session.getSession()
      assert.equal(info.id, 'session-a')
      assert.equal(
        (harness.memory.requests.get('getSession') as pb.GetSessionRequest)
          .sessionId,
        'session-a'
      )
    } finally {
      await memco.close()
    }
  })
})

test('getSession refuses a blank id before anything is sent', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()
      await assert.rejects(
        memco.memory.getSession(' '),
        MemcoInvalidRequestError
      )
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('resuming survives one blip on the session lookup', async () => {
  // A pure read, retried like the domain listing and the catalog beside it, so
  // a dropped connection does not fail the resume (or end an impersonation key).
  await withHarness(async harness => {
    harness.memory.transientErrors.set('getSession', [
      { code: status.UNAVAILABLE, details: 'blip' }
    ])
    const memco = client(harness)
    try {
      const session = await memco.memory.resumeSession('session-stored')
      assert.equal(session.id, 'session-stored')
    } finally {
      await memco.close()
    }
  })
})

test('a resumed session keeps the id it was resumed by', async () => {
  // Not the id the lookup echoes: one left empty would blank every call after.
  await withHarness(async harness => {
    harness.memory.responses.set(
      'getSession',
      pb.GetSessionResponse.fromPartial({ domain: 'coding' })
    )
    const memco = client(harness)
    try {
      const session = await memco.memory.resumeSession('session-stored')
      assert.equal(session.id, 'session-stored')
    } finally {
      await memco.close()
    }
  })
})
