/**
 * Sessions that act as one external user: impersonate, list domains, start,
 * end the key.
 *
 * Which credential each call carried is read off the wire, call by call. The
 * failures worth catching are a call sent with the wrong credential — the
 * client's token where the user's key belongs, or one user's key on another
 * user's call — and a session that reported the right key while sending
 * another would pass anything read off the SDK.
 */

import assert from 'node:assert/strict'
import { test, type TestContext } from 'node:test'
import { setTimeout as sleep } from 'node:timers/promises'
import { inspect } from 'node:util'
import { setFlagsFromString } from 'node:v8'
import { runInNewContext } from 'node:vm'

import { status } from '@grpc/grpc-js'

import { Memco } from '../src/client.js'
import {
  MemcoAuthenticationError,
  MemcoConfigError,
  MemcoInvalidRequestError,
  MemcoPermissionError,
  MemcoResourceExhaustedError,
  MemcoTimeoutError
} from '../src/errors.js'
import * as pb from '../src/internal/gen.js'
import { DEFAULT_LEVEL, setLevel } from '../src/internal/logging.js'
import type { Session } from '../src/operations.js'
import { FakeClock } from './fakeClock.js'
import { Hold, withHarness, type Harness } from './fakeServer.js'
import { stderrOf } from './stderr.js'

const XID = 'customer-42'
const KEY_1 = `Bearer impersonation-${XID}-1`
const KEY_2 = `Bearer impersonation-${XID}-2`
const CLIENT_TOKEN = 'Bearer client-token-1'

/**
 * The key lifetime the renewal tests stage, so a key falls due at 480s: well
 * inside the client token's own renewal point, so only the key renews.
 */
const KEY_LIFETIME = 600

function credentialed(harness: Harness): Memco {
  return new Memco({
    clientId: 'client-a',
    clientSecret: 'secret-a',
    host: harness.address,
    tls: false
  })
}

/** Run `body` against a connected API client, with the connect forgotten. */
async function withClient(
  body: (memco: Memco, harness: Harness) => Promise<void>
): Promise<void> {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await memco.connect()
      harness.forget()
      await body(memco, harness)
    } finally {
      await memco.close()
    }
  })
}

/** The external id and key id of every EndImpersonation the service received. */
function ends(harness: Harness): [string, string][] {
  return harness.admin.calls.flatMap((name, at) => {
    if (name !== 'endImpersonation') {
      return []
    }
    const sent = harness.admin.received[at] as pb.admin.EndImpersonationRequest
    return [[sent.externalId, sent.keyId] as [string, string]]
  })
}

/** The one bearer each memory call to a method carried, in order. */
function carried(harness: Harness, method: string): string[] {
  return harness.memory.calls.flatMap((name, at) =>
    name === method ? (harness.memory.authorization[at] ?? []) : []
  )
}

function faked(t: TestContext, harness: Harness): FakeClock {
  const fake = new FakeClock().install()
  harness.admin.clock = fake.now
  t.after(() => fake.restore())
  return fake
}

/** Wait for something a call left running will do, failing if it never does. */
async function eventually(
  done: () => boolean | Promise<boolean>
): Promise<void> {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (await done()) {
      return
    }
    await sleep(10)
  }
  assert.fail('timed out waiting')
}

function found(): pb.SearchResponse {
  return pb.SearchResponse.fromPartial({
    sessionId: 'session-a',
    memories: [{ idx: 'memory-a-1' }]
  })
}

function rated(): pb.ShareFeedbackResponse {
  return pb.ShareFeedbackResponse.fromPartial({
    sessionId: 'session-a',
    entries: [{ idx: 'memory-a-1', relevant: true, correct: true }]
  })
}

// --- opening -------------------------------------------------------------

test('opening impersonates, then lists domains, then starts, under the key', async () => {
  await withClient(async (memco, harness) => {
    await memco.memory.startSession('coding', { externalId: XID })
    // Minted with the client's own token, for the service's default lifetime.
    assert.deepEqual(harness.admin.received, [
      pb.admin.ImpersonateExternalUserRequest.fromPartial({ externalId: XID })
    ])
    assert.deepEqual(harness.admin.authorization, [[CLIENT_TOKEN]])
    // Everything after carries the key, starting with the ListDomains that
    // tells this session its limits and any deprecation. StartSession and
    // ListTools are sent together, so their order is not settled.
    assert.equal(harness.memory.calls[0], 'listDomains')
    assert.deepEqual(harness.memory.calls.slice(1).sort(), [
      'listTools',
      'startSession'
    ])
    assert.deepEqual(harness.memory.authorization, [[KEY_1], [KEY_1], [KEY_1]])
  })
})

test('withSession opens the same way, once awaited', async () => {
  await withClient(async (memco, harness) => {
    const opener = memco.memory.withSession('coding', { externalId: XID })
    assert.deepEqual(harness.admin.calls, [])
    await opener
    assert.deepEqual(harness.admin.calls, ['impersonateExternalUser'])
    assert.equal(harness.memory.calls[0], 'listDomains')
    assert.deepEqual(carried(harness, 'startSession'), [KEY_1])
  })
})

test('the session learns its limits under its own key', async () => {
  await withClient(async (memco, harness) => {
    harness.memory.responses.set(
      'listDomains',
      pb.ListDomainsResponse.fromPartial({ limits: { maxQueryCharacters: 10 } })
    )
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    harness.forget()
    await assert.rejects(
      session.search('a query well past the limit'),
      MemcoInvalidRequestError
    )
    assert.deepEqual(harness.memory.calls, [])
  })
})

test('every call through the session carries the key', async () => {
  await withClient(async (memco, harness) => {
    harness.memory.responses.set('search', found())
    harness.memory.responses.set('shareFeedback', rated())
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    harness.forget()

    const result = await session.search('how does X work')
    await session.getMemory('memory-a-1')
    await session.createMemory({ query: 'q', title: 't', content: 'c' })
    await session.enrichMemory({
      memoryIdx: 'memory-a-1',
      title: 't',
      content: 'c'
    })
    await session.shareFeedback({
      feedback: [{ idx: 'memory-a-1', relevant: true, correct: true }]
    })
    await session.revertMemory('create-a')
    await session.importMemories([
      { queries: ['q'], insights: [{ title: 't', content: 'c' }] }
    ])
    // And what the session hands out: a returned memory's feedback, and the
    // tools a model calls.
    await result.memories[0]?.feedback({ relevant: true, correct: true })
    await session.tools().call('memco_search', { query: 'how does Y work' })

    assert.deepEqual(harness.memory.calls, [
      'search',
      'getMemory',
      'createMemory',
      'enrichMemory',
      'shareFeedback',
      'revertMemory',
      'importMemories',
      'shareFeedback',
      'search'
    ])
    assert.deepEqual(
      harness.memory.authorization,
      Array(harness.memory.calls.length).fill([KEY_1])
    )
    assert.deepEqual(harness.admin.calls, [])
  })
})

test("calls on the client keep the client's own credential", async () => {
  await withClient(async (memco, harness) => {
    await memco.memory.startSession('coding', { externalId: XID })
    harness.forget()
    await memco.networks.list()
    await memco.memory.listDomains()
    assert.deepEqual(harness.admin.authorization, [[CLIENT_TOKEN]])
    assert.deepEqual(harness.memory.authorization, [[CLIENT_TOKEN]])
  })
})

test('a token client keeps its token beside an impersonated session', async () => {
  await withHarness(async harness => {
    const memco = new Memco({
      token: 'test-token',
      host: harness.address,
      tls: false
    })
    try {
      const session = await memco.memory.startSession('coding', {
        externalId: XID
      })
      harness.forget()
      await session.search('as the user')
      await memco.memory.search('as the client', { domain: 'coding' })
      assert.deepEqual(harness.memory.authorization, [
        [KEY_1],
        ['Bearer test-token']
      ])
    } finally {
      await memco.close()
    }
    assert.deepEqual(harness.admin.authorization.at(-1), ['Bearer test-token'])
  })
})

test('a key never reaches a log, an error or a rendering of the session', async () => {
  await withClient(async (memco, harness) => {
    let session: Session | undefined
    let refused: unknown
    const written = await stderrOf(async () => {
      setLevel('debug')
      try {
        session = await memco.memory.startSession('coding', {
          externalId: XID
        })
        harness.memory.error = {
          code: status.UNAUTHENTICATED,
          details: 'key rejected'
        }
        refused = await session
          .search('anything')
          .catch((error: unknown) => error)
        await session.close()
      } finally {
        setLevel(DEFAULT_LEVEL)
      }
    })
    assert.ok(refused instanceof MemcoAuthenticationError)
    assert.ok(written.includes('ImpersonateExternalUser ok in'), written)
    for (const rendered of [
      written,
      inspect(refused, { showHidden: true, depth: null }),
      String(refused),
      inspect(session, { showHidden: true, depth: 4 }),
      inspect(memco, { showHidden: true, depth: 4 })
    ]) {
      assert.ok(!rendered.includes(`impersonation-${XID}`), rendered)
      assert.ok(!rendered.includes('client-token'), rendered)
    }
  })
})

for (const [domain, externalId] of [
  ['coding', ''],
  ['coding', '   '],
  ['', XID],
  [' ', XID]
] as const) {
  test(`opening with domain ${JSON.stringify(domain)} and externalId ${JSON.stringify(externalId)} sends nothing`, async () => {
    await withClient(async (memco, harness) => {
      await assert.rejects(
        memco.memory.startSession(domain, { externalId }),
        MemcoInvalidRequestError
      )
      await assert.rejects(
        Promise.resolve(memco.memory.withSession(domain, { externalId })),
        MemcoInvalidRequestError
      )
      // Checked before the mint, so a blank value costs no key.
      assert.deepEqual(harness.admin.calls, [])
      assert.deepEqual(harness.memory.calls, [])
    })
  })
}

test('a refused impersonation opens nothing, and has no key to end', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.error = {
      code: status.PERMISSION_DENIED,
      details: 'the admin grant is required'
    }
    await assert.rejects(
      memco.memory.startSession('coding', { externalId: XID }),
      MemcoPermissionError
    )
    assert.deepEqual(harness.admin.calls, ['impersonateExternalUser'])
    assert.deepEqual(harness.memory.calls, [])
  })
})

for (const failing of ['listDomains', 'startSession'] as const) {
  test(`a ${failing} failure after the mint ends the key`, async () => {
    // The Node form of an open abandoned part-way: the key it minted must not
    // stay live against the user's cap until it expires.
    await withClient(async (memco, harness) => {
      harness.memory.transientErrors.set(failing, [
        { code: status.PERMISSION_DENIED, details: 'no network for the user' }
      ])
      await assert.rejects(
        memco.memory.startSession('coding', { externalId: XID }),
        MemcoPermissionError
      )
      assert.deepEqual(ends(harness), [[XID, 'key-1']])
      assert.deepEqual(harness.admin.authorization.at(-1), [CLIENT_TOKEN])
    })
  })
}

// --- closing -------------------------------------------------------------

test('leaving an await-using block ends the key, once', async () => {
  await withClient(async (memco, harness) => {
    {
      await using session = await memco.memory.withSession('coding', {
        externalId: XID
      })
      await session.search('anything')
      assert.deepEqual(ends(harness), [])
    }
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
  })
})

test('closing twice ends the key once', async () => {
  await withClient(async (memco, harness) => {
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    await session.close()
    await session.close()
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
  })
})

test('a closed session refuses every call locally', async () => {
  await withClient(async (memco, harness) => {
    harness.memory.responses.set('search', found())
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    const result = await session.search('before')
    await session.close()
    harness.forget()
    const refusals: Promise<unknown>[] = [
      session.search('after'),
      session.getMemory('memory-a-1'),
      session.createMemory({ query: 'q', title: 't', content: 'c' }),
      session.revertMemory('create-a'),
      session.tools().call('memco_search', { query: 'after' }),
      result.memories[0]?.feedback({ relevant: true, correct: true }) ??
        Promise.resolve()
    ]
    for (const refusal of refusals) {
      await assert.rejects(refusal, (error: unknown) => {
        assert.ok(error instanceof MemcoConfigError)
        assert.match(error.message, /this session is closed/)
        return true
      })
    }
    assert.deepEqual(harness.memory.calls, [])
    assert.deepEqual(harness.admin.calls, [])
  })
})

test('closing a plain session changes nothing, and it stays usable', async () => {
  await withClient(async (memco, harness) => {
    const session = await memco.memory.startSession('coding')
    await session.close()
    await session.search('still works')
    assert.deepEqual(harness.admin.calls, [])
    assert.deepEqual(carried(harness, 'search'), [CLIENT_TOKEN])
  })
})

test('two awaits of one opener share one key', async () => {
  await withClient(async (memco, harness) => {
    const opener = memco.memory.withSession('coding', { externalId: XID })
    const [first, second] = await Promise.all([opener, opener])
    assert.equal(first, second)
    assert.deepEqual(harness.admin.calls, ['impersonateExternalUser'])
    assert.equal(
      harness.memory.calls.filter(name => name === 'startSession').length,
      1
    )
  })
})

// --- renewal -------------------------------------------------------------

test('a due key is renewed, and the old one ended', async t => {
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    harness.admin.keyLifetime = KEY_LIFETIME
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    fake.advance(0.8 * KEY_LIFETIME - 1)
    await session.search('before')
    assert.deepEqual(harness.admin.calls, ['impersonateExternalUser'])
    fake.advance(2)
    // Due but valid: the search goes on under key-1, and the renewal runs
    // beside it; the searches after it carry key-2.
    await session.search('after')
    await eventually(() => ends(harness).length === 1)
    await session.search('renewed')
    assert.deepEqual(harness.admin.calls, [
      'impersonateExternalUser',
      'impersonateExternalUser',
      'endImpersonation'
    ])
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
    assert.deepEqual(carried(harness, 'search'), [KEY_1, KEY_1, KEY_2])
    await session.close()
    assert.deepEqual(ends(harness), [
      [XID, 'key-1'],
      [XID, 'key-2']
    ])
  })
})

test('renewal never ends a key under a call still using it', async t => {
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    harness.admin.keyLifetime = KEY_LIFETIME
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    const hold = new Hold()
    harness.memory.holds.set('search', hold)
    const held = session.search('held')
    await hold.arrived
    fake.advance(0.8 * KEY_LIFETIME + 1)
    await session.getMemory('memory-a-1')
    await eventually(
      () =>
        harness.admin.calls.filter(name => name === 'impersonateExternalUser')
          .length === 2
    )
    // Received by the service; give the client its answer.
    await sleep(20)
    await session.getMemory('memory-a-2')
    // The new key serves new calls, but the old one still carries the held
    // search, so ending it now would fail that search.
    assert.deepEqual(ends(harness), [])
    hold.release()
    await held
    await eventually(() => ends(harness).length === 1)
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
    assert.deepEqual(carried(harness, 'search'), [KEY_1])
    assert.deepEqual(carried(harness, 'getMemory'), [KEY_1, KEY_2])
  })
})

test('a key renewal refused at the cap keeps the key until it expires, then raises', async t => {
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    harness.admin.keyLifetime = KEY_LIFETIME
    // key-1 is the user's one live key, so minting a second is refused.
    harness.admin.keyCap = 1
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    fake.advance(0.85 * KEY_LIFETIME)
    setLevel(DEFAULT_LEVEL)
    const written = await stderrOf(async () => {
      await Promise.all([
        session.search('first'),
        session.search('second'),
        session.getMemory('memory-a-1')
      ])
      await eventually(() => harness.admin.calls.length === 2)
      await sleep(20)
    })
    assert.deepEqual(carried(harness, 'search'), [KEY_1, KEY_1])
    assert.deepEqual(carried(harness, 'getMemory'), [KEY_1])
    // One refused attempt, shared by all three calls, and one warning naming
    // the key by its id alone.
    assert.deepEqual(harness.admin.calls, [
      'impersonateExternalUser',
      'impersonateExternalUser'
    ])
    const warnings = written
      .split('\n')
      .filter(line => line.includes('WARNING'))
    assert.equal(warnings.length, 1, written)
    assert.match(warnings[0] ?? '', /key-1/)
    assert.ok(!written.includes(`impersonation-${XID}`), written)

    // At the end of its lifetime the key is gone, and the refusal is raised.
    fake.advance(0.15 * KEY_LIFETIME)
    harness.admin.transientErrors.set('impersonateExternalUser', [
      { code: status.RESOURCE_EXHAUSTED, details: 'too many live keys' }
    ])
    await assert.rejects(session.search('third'), MemcoResourceExhaustedError)
    assert.deepEqual(carried(harness, 'search'), [KEY_1, KEY_1])
  })
})

test('a slow key renewal does not hold calls while the key is still valid', async t => {
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    harness.admin.keyLifetime = KEY_LIFETIME
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    fake.advance(0.85 * KEY_LIFETIME)
    const hold = new Hold()
    harness.admin.holds.set('impersonateExternalUser', hold)
    const started = Date.now()
    await Promise.all([
      session.search('first', { timeout: 0.2 }),
      session.search('second', { timeout: 0.2 })
    ])
    assert.ok(Date.now() - started < 150, 'the calls waited on the renewal')
    assert.deepEqual(carried(harness, 'search'), [KEY_1, KEY_1])
    await hold.arrived
    hold.release()
    harness.admin.holds.delete('impersonateExternalUser')
    await eventually(() => ends(harness).length === 1)
    await session.search('third')
    assert.deepEqual(carried(harness, 'search'), [KEY_1, KEY_1, KEY_2])
  })
})

test('an open keeps to its own deadline while the mint is slow, and the key is still ended', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.delays.set('impersonateExternalUser', 600)
    const started = Date.now()
    await assert.rejects(
      memco.memory.startSession('coding', { externalId: XID, timeout: 0.1 }),
      MemcoTimeoutError
    )
    assert.ok(Date.now() - started < 400, 'the open outwaited its deadline')
    // The mint it gave up on lands, and what it produced is ended.
    await eventually(() => ends(harness).length === 1)
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
    assert.deepEqual(harness.memory.calls, [])
  })
})

test('a mint in flight when the client closes has its key ended by the close', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    await memco.connect()
    const hold = new Hold()
    harness.admin.holds.set('impersonateExternalUser', hold)
    const opening = memco.memory
      .startSession('coding', { externalId: XID })
      .catch((error: unknown) => error)
    await hold.arrived
    const closing = memco.close()
    await sleep(20)
    hold.release()
    await closing
    // The close waited for the mint, and ended what it produced; the open, cut
    // off by the close, reports it rather than a session.
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
    assert.ok((await opening) instanceof MemcoConfigError)
    assert.deepEqual(harness.admin.keysOf(XID).size, 0)
  })
})

test('a key without expiresIn that arrives already expired by the local clock is refused, naming the clock, and ended', async () => {
  // The service's clock two hours behind this machine's, and a service that
  // does not yet say how long a key has left: a one-hour key reads as expired
  // the moment it arrives. Used anyway, every call would mint a new key, and
  // closing the client would skip the last one as expired.
  await withClient(async (memco, harness) => {
    harness.admin.sendExpiresIn = false
    harness.admin.clock = () => Date.now() - 2 * 3600 * 1000
    await assert.rejects(
      memco.memory.startSession('coding', { externalId: XID }),
      (error: unknown) => {
        assert.ok(error instanceof MemcoConfigError)
        assert.match(error.message, /clock/)
        assert.match(error.message, /key-1/)
        assert.ok(
          !error.message.includes(`impersonation-${XID}`),
          error.message
        )
        return true
      }
    )
    // Minted once, not in a loop, and ended rather than left live.
    assert.deepEqual(harness.admin.calls, [
      'impersonateExternalUser',
      'endImpersonation'
    ])
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
    assert.equal(harness.admin.keysOf(XID).size, 0)
    assert.deepEqual(harness.memory.calls, [])
  })
})

test('a key the service says how long it has left is timed by that, whatever the local clock says', async t => {
  // Two hours of skew: expiresAt reads as long past, but expiresIn is the
  // service's own count, so the key is timed without the wall clock at all.
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    harness.admin.clock = () => fake.now() - 2 * 3600 * 1000
    harness.admin.keyLifetime = KEY_LIFETIME
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    fake.advance(0.8 * KEY_LIFETIME - 1)
    await session.search('before')
    assert.deepEqual(harness.admin.calls, ['impersonateExternalUser'])
    fake.advance(2)
    await session.search('due')
    await eventually(() => ends(harness).length === 1)
    await session.search('renewed')
    assert.deepEqual(carried(harness, 'search'), [KEY_1, KEY_1, KEY_2])
    await session.close()
    assert.deepEqual(ends(harness), [
      [XID, 'key-1'],
      [XID, 'key-2']
    ])
  })
})

test('a key is timed from before it was asked for, so a slow mint cannot make it late', async t => {
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    let minted = 0
    harness.admin.responses.set(
      'impersonateExternalUser',
      (request: pb.admin.ImpersonateExternalUserRequest) => {
        // The mint takes 100s of the key's 600s.
        fake.advance(100)
        minted += 1
        return pb.admin.ImpersonationKey.fromPartial({
          value: `impersonation-${request.externalId}-${minted}`,
          keyId: `key-${minted}`,
          expiresAt: Math.floor(fake.now() / 1000) + 500,
          expiresIn: 500
        })
      }
    )
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    // Due at 0.8 of 500s from before the request, not from after it.
    fake.advance(0.8 * 500 - 100 - 1)
    await session.search('before')
    assert.equal(minted, 1)
    fake.advance(2)
    await session.search('due')
    await eventually(() => minted === 2)
  })
})

test('without expiresIn a key is timed from expiresAt, as before', async t => {
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    harness.admin.sendExpiresIn = false
    harness.admin.keyLifetime = KEY_LIFETIME
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    fake.advance(0.8 * KEY_LIFETIME - 1)
    await session.search('before')
    assert.deepEqual(harness.admin.calls, ['impersonateExternalUser'])
    fake.advance(2)
    await session.search('due')
    await eventually(() => ends(harness).length === 1)
    await session.search('renewed')
    assert.deepEqual(carried(harness, 'search'), [KEY_1, KEY_1, KEY_2])
  })
})

// --- ending a key --------------------------------------------------------

test('a failed end is logged by key id, and tried again when the client closes', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    harness.admin.transientErrors.set('endImpersonation', [
      { code: status.UNAVAILABLE, details: 'admin service down' }
    ])
    setLevel(DEFAULT_LEVEL)
    const written = await stderrOf(() => session.close())
    // Named by its id, which revokes nothing, and never by its value.
    assert.ok(
      written.includes('WARNING could not end impersonation key key-1'),
      written
    )
    assert.ok(!written.includes(`impersonation-${XID}`), written)
    await memco.close()
    assert.deepEqual(ends(harness), [
      [XID, 'key-1'],
      [XID, 'key-1']
    ])
  })
})

test('an end answered NOT_FOUND counts as ended', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    harness.admin.transientErrors.set('endImpersonation', [
      { code: status.NOT_FOUND, details: 'no such key' }
    ])
    const written = await stderrOf(async () => {
      await session.close()
      await memco.close()
    })
    assert.ok(!written.includes('WARNING'), written)
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
  })
})

test('closing the client ends the keys of sessions left open', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    await memco.memory.startSession('coding', { externalId: 'u1' })
    await memco.memory.startSession('coding', { externalId: 'u2' })
    await memco.close()
    assert.deepEqual(ends(harness), [
      ['u1', 'key-1'],
      ['u2', 'key-2']
    ])
    // Under the client's own token: the keys themselves are what is ended.
    assert.deepEqual(harness.admin.authorization.slice(-2), [
      [CLIENT_TOKEN],
      [CLIENT_TOKEN]
    ])
  })
})

test('a session closed after its client is a quiet no-op', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    await memco.close()
    const written = await stderrOf(() => session.close())
    assert.equal(written, '')
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
  })
})

test('the close sweep counts NOT_FOUND as ended and goes on', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    await memco.memory.startSession('coding', { externalId: 'u1' })
    await memco.memory.startSession('coding', { externalId: 'u2' })
    harness.admin.transientErrors.set('endImpersonation', [
      { code: status.NOT_FOUND, details: 'expired' }
    ])
    const written = await stderrOf(() => memco.close())
    assert.ok(!written.includes('WARNING'), written)
    assert.deepEqual(ends(harness), [
      ['u1', 'key-1'],
      ['u2', 'key-2']
    ])
  })
})

test('the close sweep stops at the first failure, with one warning naming what is left', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    for (const user of ['u1', 'u2', 'u3']) {
      await memco.memory.startSession('coding', { externalId: user })
    }
    harness.admin.transientErrors.set('endImpersonation', [
      { code: status.UNAVAILABLE, details: 'admin service down' }
    ])
    setLevel(DEFAULT_LEVEL)
    const written = await stderrOf(() => memco.close())
    const warnings = written
      .split('\n')
      .filter(line => line.includes('WARNING'))
    assert.equal(warnings.length, 1, written)
    assert.match(
      warnings[0] ?? '',
      /could not end impersonation keys key-1, key-2, key-3; they expire on their own/
    )
    assert.deepEqual(ends(harness), [['u1', 'key-1']])
  })
})

test('closing the client waits for calls in flight before ending keys', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    const hold = new Hold()
    harness.memory.holds.set('search', hold)
    const held = session.search('held')
    await hold.arrived
    const closing = memco.close()
    await new Promise(settle => setTimeout(settle, 20))
    assert.deepEqual(ends(harness), [])
    hold.release()
    await Promise.all([held, closing])
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
  })
})

// --- keys whose end is not the caller's --------------------------------

test('ending a replaced key takes its own deadline, not the one of the call that replaced it', async t => {
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    harness.admin.keyLifetime = KEY_LIFETIME
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    fake.advance(0.8 * KEY_LIFETIME + 1)
    // Longer than the search below will wait: an end sent under that search's
    // deadline would fail, and leave key-1 live against the user's cap.
    harness.admin.delays.set('endImpersonation', 150)
    setLevel(DEFAULT_LEVEL)
    const written = await stderrOf(async () => {
      // Answered under key-1, which is still valid, while the renewal and the
      // end of key-1 go on without it.
      await session.search('in a hurry', { timeout: 0.05 })
      await eventually(() => ends(harness).length === 1)
      await sleep(200)
    })
    assert.ok(!written.includes('WARNING'), written)
    harness.admin.delays.delete('endImpersonation')
    await session.close()
    // Ended once each: key-1 by the renewal, key-2 by the close.
    assert.deepEqual(ends(harness), [
      [XID, 'key-1'],
      [XID, 'key-2']
    ])
  })
})

test('a key ended while the client closes is ended once, not again by the sweep', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    const hold = new Hold()
    harness.admin.holds.set('endImpersonation', hold)
    const closingSession = session.close()
    await hold.arrived
    const closingClient = memco.close()
    hold.release()
    await Promise.all([closingSession, closingClient])
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
  })
})

test('a key whose end failed is ended before the next key for its user is minted', async () => {
  // The service caps how many live keys a user may hold, and a key stranded
  // by a failed end counts against that cap until it expires.
  await withClient(async (memco, harness) => {
    const first = await memco.memory.startSession('coding', {
      externalId: XID
    })
    harness.admin.transientErrors.set('endImpersonation', [
      { code: status.UNAVAILABLE, details: 'admin service down' }
    ])
    await stderrOf(() => first.close())
    const second = await memco.memory.startSession('coding', {
      externalId: XID
    })
    assert.deepEqual(harness.admin.calls, [
      'impersonateExternalUser',
      'endImpersonation',
      'endImpersonation',
      'impersonateExternalUser'
    ])
    await second.close()
    await memco.close()
    // Nothing left for the sweep: key-1 is not ended a third time.
    assert.deepEqual(ends(harness), [
      [XID, 'key-1'],
      [XID, 'key-1'],
      [XID, 'key-2']
    ])
  })
})

test('opens made together for one user end a key that failed to end once', async () => {
  await withClient(async (memco, harness) => {
    const first = await memco.memory.startSession('coding', {
      externalId: XID
    })
    harness.admin.transientErrors.set('endImpersonation', [
      { code: status.UNAVAILABLE, details: 'admin service down' }
    ])
    await stderrOf(() => first.close())
    await Promise.all([
      memco.memory.startSession('coding', { externalId: XID }),
      memco.memory.startSession('coding', { externalId: XID })
    ])
    // The failed attempt, then one retry: not one per open.
    assert.deepEqual(ends(harness), [
      [XID, 'key-1'],
      [XID, 'key-1']
    ])
  })
})

test('a call is not held past its answer while the key it carried is ended', async t => {
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    harness.admin.keyLifetime = KEY_LIFETIME
    const session = await memco.memory.startSession('coding', {
      externalId: XID
    })
    const hold = new Hold()
    harness.memory.holds.set('search', hold)
    const held = session.search('held')
    await hold.arrived
    fake.advance(0.8 * KEY_LIFETIME + 1)
    await session.getMemory('memory-a-1')
    // The held search is key-1's last call; ending key-1 is slow.
    harness.admin.delays.set('endImpersonation', 1000)
    hold.release()
    const started = Date.now()
    await held
    assert.ok(Date.now() - started < 500, 'the search waited for the end')
    harness.admin.delays.delete('endImpersonation')
    await eventually(() => ends(harness).length === 1)
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
  })
})

test('a key another user failed to end is left to its own user', async () => {
  await withClient(async (memco, harness) => {
    const first = await memco.memory.startSession('coding', {
      externalId: 'u1'
    })
    harness.admin.transientErrors.set('endImpersonation', [
      { code: status.UNAVAILABLE, details: 'admin service down' }
    ])
    await stderrOf(() => first.close())
    await memco.memory.startSession('coding', { externalId: 'u2' })
    assert.deepEqual(harness.admin.calls, [
      'impersonateExternalUser',
      'endImpersonation',
      'impersonateExternalUser'
    ])
  })
})

test('an expired key is dropped when the next key is minted, and never ended again', async t => {
  await withClient(async (memco, harness) => {
    const fake = faked(t, harness)
    const first = await memco.memory.startSession('coding', {
      externalId: 'u1'
    })
    harness.admin.transientErrors.set('endImpersonation', [
      { code: status.UNAVAILABLE, details: 'admin service down' }
    ])
    await stderrOf(() => first.close())
    fake.advance(3601)
    await memco.memory.startSession('coding', { externalId: 'u1' })
    await memco.close()
    // One failed attempt for key-1 and nothing more: expired, it was dropped
    // rather than retried, and the sweep ended only key-2.
    assert.deepEqual(ends(harness), [
      ['u1', 'key-1'],
      ['u1', 'key-2']
    ])
  })
})

test('the close sweep sends nothing for a key that has already expired', async t => {
  await withHarness(async harness => {
    const fake = faked(t, harness)
    const memco = credentialed(harness)
    await memco.memory.startSession('coding', { externalId: XID })
    fake.advance(3601)
    await memco.close()
    assert.deepEqual(ends(harness), [])
  })
})

// --- sessions dropped without closing ------------------------------------

/**
 * The garbage collector, reached without `--expose-gc` on the command line: the
 * flag is read as a context is created, so a fresh context carries `gc`.
 */
function collector(): () => void {
  setFlagsFromString('--expose-gc')
  return runInNewContext('gc') as () => void
}

/**
 * Open a session for a user, use it, and let go of it without closing it.
 *
 * A function of its own, so nothing in the caller's frame keeps it reachable.
 */
async function openAndDrop(memco: Memco, user: string): Promise<void> {
  const session = await memco.memory.startSession('coding', {
    externalId: user
  })
  await session.search('and then forgotten')
}

/** How many dropped sessions the client has queued, read off its internals. */
function queued(memco: Memco): number {
  return (memco as unknown as { dropped: object[] }).dropped.length
}

test("a new key for a user waits for the ends of that user's dropped keys", async () => {
  // Ends sent without waiting for them would race the mint to the service,
  // which checks its cap on live keys before they land.
  const gc = collector()
  await withClient(async (memco, harness) => {
    harness.admin.keyCap = 1
    await openAndDrop(memco, XID)
    for (let n = 0; n < 50 && queued(memco) === 0; n += 1) {
      gc()
      await sleep(10)
    }
    assert.equal(queued(memco), 1, 'the dropped session was not collected')
    harness.admin.delays.set('endImpersonation', 50)
    harness.forget()
    await memco.memory.startSession('coding', { externalId: XID })
    assert.deepEqual(harness.admin.calls, [
      'endImpersonation',
      'impersonateExternalUser'
    ])
    assert.equal(harness.admin.keysOf(XID).size, 1)
  })
})

test("a new key for a user waits for an end of that user's key already in flight", async () => {
  await withClient(async (memco, harness) => {
    harness.admin.keyCap = 1
    const first = await memco.memory.startSession('coding', {
      externalId: XID
    })
    harness.admin.delays.set('endImpersonation', 50)
    const closing = first.close()
    const second = await memco.memory.startSession('coding', {
      externalId: XID
    })
    await closing
    assert.deepEqual(ends(harness), [[XID, 'key-1']])
    await second.search('works')
    assert.deepEqual(carried(harness, 'search'), [KEY_2])
  })
})

test('sessions dropped without closing still have their keys ended, and never exhaust the cap', async () => {
  const gc = collector()
  await withClient(async (memco, harness) => {
    // Five past the service's cap of 20 live keys per user: without the keys
    // of dropped sessions being ended, the 21st open would be refused.
    for (let n = 0; n < 25; n += 1) {
      await openAndDrop(memco, XID)
      // Collection queues a dropped session's key; the next call ends it.
      gc()
      await sleep(5)
    }
    await eventually(async () => {
      gc()
      await sleep(5)
      await memco.networks.list()
      return ends(harness).length === 25
    })
    const ended = ends(harness).map(([, keyId]) => keyId)
    assert.deepEqual(
      [...ended].sort(),
      Array.from({ length: 25 }, (_, n) => `key-${n + 1}`).sort()
    )
    assert.ok(harness.admin.keysOf(XID).size < 20)
  })
})

// --- crosstalk -----------------------------------------------------------

test('concurrent sessions for two users never cross keys', async () => {
  await withClient(async (memco, harness) => {
    const actFor = async (user: string): Promise<void> => {
      await using session = await memco.memory.withSession('coding', {
        externalId: user
      })
      await Promise.all(
        Array.from({ length: 10 }, (_, n) =>
          session.search(`${user} query ${n}`)
        )
      )
    }
    await Promise.all([actFor('u1'), actFor('u2')])

    // Pair every search with the key it carried, and every end with the key it
    // named.
    const sent = harness.memory.calls.flatMap((name, at) => {
      if (name !== 'search') {
        return []
      }
      const request = harness.memory.received[at] as pb.SearchRequest
      return [
        [request.query.split(' ')[0], harness.memory.authorization[at]] as const
      ]
    })
    assert.equal(sent.length, 20)
    for (const [user, credentials] of sent) {
      assert.equal(credentials?.length, 1)
      assert.match(
        credentials?.[0] ?? '',
        new RegExp(`^Bearer impersonation-${user}-\\d+$`)
      )
    }
    const keys = new Set(sent.map(([, credentials]) => credentials?.[0]))
    assert.equal(keys.size, 2)
    assert.deepEqual(
      ends(harness)
        .map(([user]) => user)
        .sort(),
      ['u1', 'u2']
    )
    for (const [user, keyId] of ends(harness)) {
      assert.ok(
        keys.has(`Bearer impersonation-${user}-${keyId.replace('key-', '')}`),
        `${user} ended ${keyId}, which none of its calls carried`
      )
    }
  })
})
