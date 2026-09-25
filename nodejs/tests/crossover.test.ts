/**
 * Many sessions, for many users, on one client at once: every call must carry
 * exactly its own session's key.
 *
 * A stress run rather than a scenario. Twenty-six sessions — two of them for
 * the same user — are opened concurrently in every form the SDK offers, and
 * each makes interleaved calls of every kind while keys and the client token
 * renew every second and random calls give up on short deadlines. Memories are
 * handed between sessions and rated from the wrong one, which must still send
 * the rating under the session that returned them. Some sessions are left open
 * for the client's close to sweep.
 *
 * The fake answers with handles that name their user — `sess::<user>::n`,
 * `mem::<user>::n`, `create::<user>::n` — so every request can be read back
 * against the key it carried, and every call lands in one journal in the order
 * the fake received it. {@link verify} then fails on any call carrying the
 * wrong key or more than one credential, any session call carrying a key
 * minted for a different session of the same user, any administration call
 * not carrying the client token, any key used after its end, any key ended
 * more than once, and any key never ended that had not expired by the time the
 * client finished closing — an expired key is let go rather than ended.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout as sleep } from 'node:timers/promises'

import type { Metadata } from '@grpc/grpc-js'

import { Memco } from '../src/client.js'
import { MemcoConfigError, MemcoTimeoutError } from '../src/errors.js'
import type { Minted as Held } from '../src/internal/auth.js'
import * as pb from '../src/internal/gen.js'
import { DEFAULT_LEVEL, setLevel } from '../src/internal/logging.js'
import type { Session } from '../src/operations.js'
import type { Memory } from '../src/types.js'
import { withHarness, type Arrival, type Harness } from './fakeServer.js'

const USERS = [...Array.from({ length: 24 }, (_, n) => `u${n}`), 'dup', 'dup']
const CALLS = 14
const SEEDS = [7, 11]

/**
 * A small seeded generator for the mix of calls and pauses.
 *
 * Seeding fixes which calls each session makes; it cannot replay a run
 * exactly, since renewals, pauses and deadlines run on the real clock.
 */
function seeded(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let mixed = Math.imul(state ^ (state >>> 15), 1 | state)
    mixed ^= mixed + Math.imul(mixed ^ (mixed >>> 7), 61 | mixed)
    return ((mixed ^ (mixed >>> 14)) >>> 0) / 4294967296
  }
}

/** The user an impersonation bearer acts for, or `null` if it is not one. */
function keyUser(bearer: string): string | null {
  const prefix = 'Bearer impersonation-'
  if (!bearer.startsWith(prefix)) {
    return null
  }
  const rest = bearer.slice(prefix.length)
  return rest.slice(0, rest.lastIndexOf('-'))
}

/** The user a handle names: `<kind>::<user>::<n>`. */
function handleUser(handle: string): string {
  const parts = handle.split('::')
  assert.equal(parts.length, 3, handle)
  return parts[1] ?? ''
}

function firstWord(text: string): string {
  return text.split(' ')[0] ?? ''
}

/** Every user a memory request names, by its content and its session. */
function markers(method: string, request: never): string[] {
  const named: string[] = []
  const sent = request as Record<string, unknown>
  const sessionId =
    method === 'getMemory' ? '' : String(sent['sessionId'] ?? '')
  if (sessionId !== '') {
    named.push(handleUser(sessionId))
  }
  switch (method) {
    case 'search':
      named.push(firstWord((request as pb.SearchRequest).query))
      break
    case 'createMemory':
      named.push(firstWord((request as pb.CreateMemoryRequest).title))
      break
    case 'enrichMemory': {
      const enrich = request as pb.EnrichMemoryRequest
      named.push(firstWord(enrich.title), handleUser(enrich.memoryIdx))
      break
    }
    case 'shareFeedback':
      named.push(
        ...(request as pb.ShareFeedbackRequest).feedback.map(rating =>
          handleUser(rating.idx)
        )
      )
      break
    case 'revertMemory':
      named.push(handleUser((request as pb.RevertMemoryRequest).opId))
      break
    case 'importMemories':
      named.push(
        ...(request as pb.ImportMemoriesRequest).memories.map(memory =>
          firstWord(memory.queries[0] ?? '')
        )
      )
      break
    case 'getMemory':
      named.push(handleUser((request as pb.GetMemoryRequest).idx))
      break
    case 'startSession':
      named.push(
        (request as pb.StartSessionRequest).domain.replace(/^dom-/, '')
      )
      break
  }
  return named
}

/** What the run recorded beside the fake's journal. */
interface Ledger {
  /** Session id to the bearer it was opened with. */
  readonly opened: Map<string, string>
  /** `Bearer <key>` to the id of the session credential that minted it. */
  readonly owners: Map<string, number>
}

/** What the fake minted, by key id. */
type Minted = ReadonlyMap<string, { externalId: string; expiresAt: number }>

/**
 * Check every call the fake received against the credential it carried.
 *
 * @param journal Every call, in the order the fake received them.
 * @param ledger What the run recorded beside the journal.
 * @param minted Every key the fake minted.
 * @param closedAt When the client finished closing, in epoch milliseconds.
 * @returns Counts of what was checked.
 * @throws AssertionError Listing every violation found.
 */
function verify(
  journal: readonly Arrival[],
  ledger: Ledger,
  minted: Minted,
  closedAt: number
): Record<string, number> {
  const problems: string[] = []
  const counts: Record<string, number> = {}
  const mintedFor = new Map(
    [...minted].map(([keyId, key]) => [keyId, key.externalId])
  )
  const lastUse = new Map<string, number>()
  const endedAt = new Map<string, number>()
  const ends = new Map<string, number>()

  journal.forEach((arrival, at) => {
    counts[arrival.method] = (counts[arrival.method] ?? 0) + 1
    if (arrival.authorization.length > 1) {
      problems.push(
        `#${at} ${arrival.method} carried ${arrival.authorization.length} credentials`
      )
    }
    const bearer = arrival.authorization[0] ?? ''
    if (arrival.service === 'tokens') {
      if (bearer !== '') {
        problems.push(`#${at} IssueToken carried a bearer`)
      }
      return
    }
    if (arrival.service === 'admin') {
      if (!bearer.startsWith('Bearer client-token-')) {
        problems.push(
          `#${at} ${arrival.method} carried ${bearer}, not the client token`
        )
      }
      if (arrival.method === 'endImpersonation') {
        const end = arrival.request as pb.admin.EndImpersonationRequest
        if (mintedFor.get(end.keyId) !== end.externalId) {
          problems.push(
            `#${at} End names ${end.externalId} for ${end.keyId}, minted for ${mintedFor.get(end.keyId)}`
          )
        }
        const number = end.keyId.replace('key-', '')
        endedAt.set(`Bearer impersonation-${end.externalId}-${number}`, at)
        ends.set(end.keyId, (ends.get(end.keyId) ?? 0) + 1)
      }
      return
    }
    const user = keyUser(bearer)
    if (user === null) {
      problems.push(
        `#${at} ${arrival.method} carried ${bearer || 'nothing'}, not a key`
      )
      return
    }
    lastUse.set(bearer, at)
    const sent = arrival.request as Record<string, unknown>
    const sessionId =
      arrival.method === 'getMemory' ? '' : String(sent['sessionId'] ?? '')
    if (sessionId !== '') {
      const opened = ledger.opened.get(sessionId) ?? ''
      if (ledger.owners.get(opened) !== ledger.owners.get(bearer)) {
        problems.push(
          `#${at} ${arrival.method} on ${sessionId}, opened with credential ` +
            `#${ledger.owners.get(opened)}, carried credential #${ledger.owners.get(bearer)}'s key`
        )
      }
    }
    for (const named of markers(arrival.method, arrival.request as never)) {
      if (named !== user) {
        problems.push(
          `#${at} ${arrival.method} names ${named} but carried ${user}'s key`
        )
      }
    }
  })

  for (const [bearer, used] of lastUse) {
    const end = endedAt.get(bearer)
    if (end !== undefined && used > end) {
      problems.push(`${bearer} was used at #${used}, after its end at #${end}`)
    }
  }
  for (const [keyId, key] of minted) {
    const times = ends.get(keyId) ?? 0
    if (times > 1) {
      problems.push(`${keyId} was ended ${times} times`)
    }
    if (times === 0 && key.expiresAt > closedAt) {
      problems.push(`${keyId} was never ended, and was still live at close`)
    }
    if (times === 0) {
      counts['expiredUnended'] = (counts['expiredUnended'] ?? 0) + 1
    }
    if (
      !ledger.owners.has(
        `Bearer impersonation-${key.externalId}-${keyId.replace('key-', '')}`
      )
    ) {
      problems.push(`${keyId} was minted, but never reached a session`)
    }
  }
  assert.deepEqual(problems.slice(0, 40), [], `${problems.length} violations`)
  counts['keys'] = mintedFor.size
  return counts
}

/** Answers naming the user each request is about, as the probe's fake does. */
function nameEverything(harness: Harness, ledger: Ledger): void {
  let next = 0
  const numbered = (): number => (next += 1)
  const memory = harness.memory.responses
  memory.set(
    'startSession',
    (_request: pb.StartSessionRequest, metadata: Metadata) => {
      const bearer = String(metadata.get('authorization')[0] ?? '')
      const sessionId = `sess::${keyUser(bearer)}::${numbered()}`
      ledger.opened.set(sessionId, bearer)
      return pb.StartSessionResponse.fromPartial({ sessionId })
    }
  )
  memory.set('search', (request: pb.SearchRequest) =>
    pb.SearchResponse.fromPartial({
      sessionId: request.sessionId,
      memories: [{ idx: `mem::${firstWord(request.query)}::${numbered()}` }]
    })
  )
  memory.set('getMemory', (request: pb.GetMemoryRequest) =>
    pb.GetMemoryResponse.fromPartial({ memory: { idx: request.idx } })
  )
  memory.set('createMemory', (request: pb.CreateMemoryRequest) =>
    pb.CreateMemoryResponse.fromPartial({
      operationId: `create::${firstWord(request.title)}::${numbered()}`
    })
  )
  memory.set('shareFeedback', (request: pb.ShareFeedbackRequest) =>
    pb.ShareFeedbackResponse.fromPartial({
      sessionId: request.sessionId,
      entries: request.feedback.map(rating => ({
        idx: rating.idx,
        relevant: rating.relevant,
        correct: rating.correct
      }))
    })
  )
}

/** Record which session credential minted each key, through the client's own mint. */
function recordOwners(memco: Memco, ledger: Ledger): void {
  const ids = new WeakMap<object, number>()
  let credentials = 0
  const internals = memco as unknown as {
    mintKey(externalId: string, owner: object): Promise<Held>
  }
  const mint = internals.mintKey.bind(memco)
  internals.mintKey = async (externalId, owner) => {
    const minted = await mint(externalId, owner)
    if (!ids.has(owner)) {
      credentials += 1
      ids.set(owner, credentials)
    }
    ledger.owners.set(`Bearer ${minted.value}`, ids.get(owner) ?? 0)
    return minted
  }
}

/** Run one seeded stress pass, returning what it did. */
async function stress(seed: number): Promise<Record<string, number>> {
  const random = seeded(seed)
  const pick = <T>(options: readonly T[]): T =>
    options[Math.floor(random() * options.length)] as T
  const stats: Record<string, number> = {}
  const count = (what: string): void => {
    stats[what] = (stats[what] ?? 0) + 1
  }
  const pool: [string, Memory][] = []
  const ledger: Ledger = { opened: new Map(), owners: new Map() }
  let journal: Arrival[] = []
  let minted: Minted = new Map()
  let closedAt = 0

  await withHarness(async harness => {
    // Short lifetimes, so keys and the client token renew many times a run.
    // A key's expiry arrives in whole seconds, so a one-second key minted late
    // in a second can arrive with no time left, and is refused as a clock
    // fault; two seconds leave at least one, and it still renews every second
    // or so.
    harness.admin.keyLifetime = 2
    harness.tokens.expiresIn = 1
    nameEverything(harness, ledger)
    const memco = new Memco({
      clientId: 'client-a',
      clientSecret: 'secret-a',
      host: harness.address,
      tls: false
    })
    recordOwners(memco, ledger)

    const oneCall = async (session: Session, user: string): Promise<void> => {
      const kind = pick([
        'search',
        'get',
        'create',
        'enrich',
        'feedback',
        'revert',
        'import',
        'tool',
        'memoryFeedback',
        'crossFeedback',
        'admin',
        'hurry',
        'hurry',
        'hurry'
      ])
      count(kind)
      switch (kind) {
        case 'search': {
          const found = await session.search(`${user} q`)
          pool.push([user, found.memories[0] as Memory])
          break
        }
        case 'get':
          pool.push([user, await session.getMemory(`mem::${user}::0`)])
          break
        case 'create': {
          const written = await session.createMemory({
            query: `${user} q`,
            title: `${user} t`,
            content: 'c'
          })
          await session.revertMemory(written.operationId ?? '')
          break
        }
        case 'enrich':
          await session.enrichMemory({
            memoryIdx: `mem::${user}::0`,
            title: `${user} t`,
            content: 'c'
          })
          break
        case 'feedback':
          await session.shareFeedback({
            feedback: [
              { idx: `mem::${user}::0`, relevant: true, correct: true }
            ]
          })
          break
        case 'revert':
          await session.revertMemory(`create::${user}::0`)
          break
        case 'import':
          await session.importMemories([
            { queries: [`${user} q`], insights: [{ title: 't', content: 'c' }] }
          ])
          break
        case 'tool':
          await session
            .tools()
            .call('memco_search', { query: `${user} via tool` })
          break
        case 'memoryFeedback': {
          const found = await session.search(`${user} q`)
          await found.memories[0]?.feedback({ relevant: true, correct: false })
          break
        }
        case 'crossFeedback': {
          // A memory another session returned, rated from here: it must go
          // out under the session that returned it, never this one.
          if (pool.length > 0) {
            const [owner, memory] = pick(pool)
            await memory.feedback({ relevant: false, correct: true })
            if (owner !== user) {
              count('crossed')
            }
          }
          break
        }
        case 'admin':
          await memco.users.get(user)
          await memco.networks.list()
          break
        case 'hurry':
          // The Node form of an abort: a deadline too short to wait out a
          // renewal, or even the call itself.
          try {
            await session.search(`${user} hurried`, {
              timeout: random() * 0.004 + 0.0001
            })
          } catch (error) {
            if (!(error instanceof MemcoTimeoutError)) {
              throw error
            }
            count('timedOut')
          }
          break
      }
    }

    const body = async (session: Session, user: string): Promise<void> => {
      for (let n = 0; n < CALLS; n += 1) {
        try {
          await oneCall(session, user)
        } catch (error) {
          // A memory handed over from a session since closed is refused
          // locally, which is what closing promises.
          if (!(error instanceof MemcoConfigError)) {
            throw error
          }
          count('closedRefusals')
        }
        await sleep(random() * 80)
      }
    }

    const leftOpen: [string, Session][] = []
    const actFor = async (user: string, form: number): Promise<void> => {
      const domain = `dom-${user}`
      if (form === 0) {
        await using session = await memco.memory.withSession(domain, {
          externalId: user
        })
        await body(session, user)
      } else if (form === 1) {
        const session = await memco.memory.startSession(domain, {
          externalId: user
        })
        try {
          await body(session, user)
        } finally {
          await session.close()
        }
      } else if (form === 2) {
        const opener = memco.memory.withSession(domain, { externalId: user })
        const [first, second] = await Promise.all([opener, opener])
        assert.equal(first, second)
        await Promise.all([body(first, user), body(second, user)])
        await first.close()
      } else {
        const session = await memco.memory.startSession(domain, {
          externalId: user
        })
        await body(session, user)
        leftOpen.push([user, session])
      }
    }

    const reconnect = async (): Promise<void> => {
      for (let n = 0; n < 6; n += 1) {
        await sleep(150)
        await memco.connect()
      }
    }

    await memco.connect()
    await Promise.all([
      reconnect(),
      ...USERS.map((user, at) => actFor(user, at % 4))
    ])
    // The sessions left open go on beside new ones, and the client's close
    // sweeps their keys.
    await Promise.all([
      ...leftOpen.map(([user, session]) => body(session, user)),
      ...USERS.slice(0, 6).map(user => actFor(`${user}-again`, 0))
    ])
    await memco.close()
    closedAt = Date.now()
    journal = [...harness.journal]
    minted = new Map(harness.admin.minted)
  })
  return { ...stats, ...verify(journal, ledger, minted, closedAt) }
}

for (const seed of SEEDS) {
  test(`concurrent sessions never cross keys, and every key ends once [seed ${seed}]`, async t => {
    setLevel('none')
    try {
      const counts = await stress(seed)
      t.diagnostic(JSON.stringify(counts))
      assert.ok((counts['impersonateExternalUser'] ?? 0) > USERS.length)
      assert.ok(
        (counts['crossed'] ?? 0) > 0,
        'no memory was rated across sessions'
      )
    } finally {
      setLevel(DEFAULT_LEVEL)
    }
  })
}
