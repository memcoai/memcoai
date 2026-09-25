/**
 * Sessions acting as an external user, against the real service.
 *
 * An API client places its own users in customer networks, then opens memory
 * sessions as them. The fake server in `tests/` already proves which key each
 * call carries; what only the real service can prove is what those keys do. A
 * write made through a session is the user's: the user finds it, and the user
 * alone can revert it, since a revert answers only for its caller's own
 * writes. Two sessions running at once each act as their own user, each in a
 * network of its own, so a crossed key would show as one finding what the
 * other wrote. And closing a session really revokes its key.
 *
 * The memories written follow the lifecycle suite's rules, for the same
 * reasons: the run marker lives only in the query, which becomes the memory's
 * intent, and the insight is plain prose about this SDK, which the quality gate
 * accepts. Each subject is distinct, and about the Node.js SDK alone, so no
 * write reads as the same knowledge as another — this one's or another SDK's.
 * A write a failing test leaves behind goes with its customer network, whose
 * deletion takes the memories placed in it.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout as sleep } from 'node:timers/promises'

import {
  DataSource,
  Memco,
  MemcoAuthenticationError,
  MemcoNotFoundError,
  RevertOutcome
} from '../src/index.js'
import type { Memory } from '../src/index.js'
import type { Minted } from '../src/internal/auth.js'
import * as pb from '../src/internal/gen.js'
import type { Caller } from '../src/operations.js'
import {
  Created,
  INGEST_TIMEOUT_MS,
  NO_CLIENT,
  adminClient,
  hasClient,
  pollWaits,
  rootNetwork,
  withAdmin
} from './support.js'

interface Fields {
  query: string
  title: string
  content: string
}

const OPENING: Fields = {
  query:
    "How does the Memco Node.js SDK open a memory session as one of an organisation's own users?",
  title: 'Node.js SDK impersonated sessions',
  content:
    "The Memco Node.js SDK opens a memory session as one of an organisation's own users " +
    'when `startSession` or `withSession` is given an `externalId`. It mints an ' +
    "impersonation key under the API client's token, lists the domains under that key " +
    'so the session learns its limits as that user, and only then starts the session.\n\n' +
    "Every call made through the session carries that key rather than the client's own " +
    'token. Closing the session, or leaving its `await using` block, ends the key on the ' +
    'service instead of leaving it live until it expires.'
}
const RENEWAL: Fields = {
  query:
    'When does the Memco Node.js SDK renew the impersonation key a session holds?',
  title: 'Node.js SDK impersonation key renewal',
  content:
    'The Memco Node.js SDK renews an impersonation key once four fifths of its lifetime ' +
    'have passed. Calls arriving together at that point share a single mint, and share its ' +
    'failure too, and the key it replaces is ended only after the last call still carrying ' +
    'it has finished, so a renewal never revokes a key under a call in flight.'
}
const SWEEP: Fields = {
  query:
    'What happens to open impersonation keys when a Memco Node.js SDK client closes?',
  title: 'Node.js SDK impersonation key cleanup',
  content:
    'Closing a Memco Node.js SDK client ends every impersonation key a session left open. ' +
    "It waits for calls in flight to finish, then ends each key under the client's own " +
    'token, counting a key the service reports as already gone as ended. A session dropped ' +
    'without closing has its key ended once it has been garbage-collected, by the next call ' +
    'the client makes.'
}

/** The fields to write, with the run marker added to the query alone. */
function asked(fields: Fields, marker: string): Fields {
  return { ...fields, query: `${fields.query} (system test ${marker})` }
}

/**
 * Whether a memory was written with this marker in its query.
 *
 * The query passed to createMemory becomes one of the memory's intents.
 */
function carries(memory: Memory, marker: string): boolean {
  return memory.intents.some(intent => intent.includes(marker))
}

/**
 * Collect every impersonation key the client mints from here on.
 *
 * Private access, deliberately: the SDK exposes a key's value nowhere, which
 * is the point of it, and only the value lets the test ask the service itself
 * whether the key still works once its session is closed. Each is kept as the
 * SDK holds it, whose renderings leave the value out, so an assertion printing
 * one on failure cannot put a live key in the CI log.
 */
function recordKeys(client: Memco): Minted[] {
  const keys: Minted[] = []
  const internals = client as unknown as {
    mintKey(externalId: string, owner: object): Promise<Minted>
  }
  const mint = internals.mintKey.bind(client)
  internals.mintKey = async (externalId, owner) => {
    const key = await mint(externalId, owner)
    keys.push(key)
    return key
  }
  return keys
}

/** Whether the service still accepts a key, as a client authenticating with it. */
async function accepted(key: Minted): Promise<boolean> {
  const probe = new Memco({ token: key.value })
  try {
    await probe.connect()
    return true
  } catch (error) {
    if (error instanceof MemcoAuthenticationError) {
      return false
    }
    throw error
  } finally {
    await probe.close()
  }
}

/**
 * Poll until the user finds the memory carrying the marker, failing the moment
 * they find what `other` wrote.
 *
 * A fresh session per attempt: within one session a memory already delivered
 * comes back as a bare reference carrying no intents, so a second search in
 * the same session could never recognise it.
 */
async function foundAlone(
  client: Memco,
  domain: string,
  user: string,
  query: string,
  other: string | null
): Promise<void> {
  const deadline = Date.now() + INGEST_TIMEOUT_MS
  const waits = pollWaits()
  while (Date.now() < deadline) {
    await using session = await client.memory.withSession(domain, {
      externalId: user
    })
    const { memories } = await session.search(query)
    if (other !== null) {
      assert.ok(
        !memories.some(memory => carries(memory, other)),
        `${user}'s session found what ${other} wrote, in a network ${user} is not in`
      )
    }
    if (memories.some(memory => carries(memory, user))) {
      return
    }
    console.log(`  waiting for ingestion of ${user}'s memory`)
    await sleep(waits.next().value)
  }
  assert.fail(
    `${user}'s memory never became searchable within ${INGEST_TIMEOUT_MS / 1000}s`
  )
}

/** Write, find and revert a memory as one user, never seeing the other's. */
async function act(
  client: Memco,
  domain: string,
  user: string,
  fields: Fields,
  other: string | null
): Promise<void> {
  await using session = await client.memory.withSession(domain, {
    externalId: user
  })
  const before = (await session.search(fields.query)).memories
  assert.ok(!before.some(memory => carries(memory, user)))
  const write = await session.createMemory({
    query: fields.query,
    title: fields.title,
    content: fields.content,
    source: DataSource.AGENT
  })
  assert.ok(
    write.operationId,
    'the write was accepted without an operation id to revert'
  )
  await foundAlone(client, domain, user, fields.query, other)
  const undo = await session.revertMemory(write.operationId)
  assert.equal(
    undo.outcome,
    RevertOutcome.MEMORY_REMOVED,
    `reverting as ${user}, who wrote it, reported ${RevertOutcome[undo.outcome]}`
  )
}

const skip = hasClient() ? false : NO_CLIENT

test(
  'a session acts as an external user, and its key ends with it',
  { skip },
  async () => {
    await withAdmin(async admin => {
      const created = new Created(admin, await rootNetwork(admin))
      try {
        const [network, user] = await created.placed()
        const keys = recordKeys(admin)
        console.log(`\n[${network.domain}] acting as ${user.externalId}`)

        const fields = asked(OPENING, user.externalId)
        {
          await using session = await admin.memory.withSession(network.domain, {
            externalId: user.externalId
          })
          const before = (await session.search(fields.query)).memories
          assert.ok(!before.some(memory => carries(memory, user.externalId)))
          const write = await session.createMemory({
            ...fields,
            source: DataSource.AGENT
          })
          assert.ok(
            write.operationId,
            'the write has no operation id to revert'
          )
          await foundAlone(
            admin,
            network.domain,
            user.externalId,
            fields.query,
            null
          )
          const undo = await session.revertMemory(write.operationId)
          assert.equal(
            undo.outcome,
            RevertOutcome.MEMORY_REMOVED,
            `reverting as the user who wrote it reported ${RevertOutcome[undo.outcome]}`
          )
          // The key works while its session is open, so the refusal below is
          // the close's doing rather than a key that never worked from outside.
          assert.ok(await accepted(keys[0] as Minted))
        }

        const [first] = keys
        assert.ok(first !== undefined)
        assert.ok(
          !(await accepted(first)),
          'the key still works after its session closed'
        )
        // Ending an ended key is answered NOT_FOUND, which the SDK counts as
        // ended already.
        const internals = admin as unknown as {
          call: Caller
          transport: { admin: pb.admin.AdminServiceClient }
        }
        const stub = internals.transport.admin
        await assert.rejects(
          internals.call(
            stub.endImpersonation.bind(stub),
            pb.admin.EndImpersonationRequest.fromPartial({
              externalId: user.externalId,
              keyId: first.keyId
            }),
            undefined,
            'EndImpersonation'
          ),
          MemcoNotFoundError
        )
      } finally {
        await created.remove()
      }
    })
  }
)

test(
  'two users act at once, each under a key of its own',
  { skip },
  async () => {
    await withAdmin(async admin => {
      const created = new Created(admin, await rootNetwork(admin))
      try {
        const [network, first] = await created.placed()
        const [, second] = await created.placed()
        const [one, two] = [first.externalId, second.externalId]
        console.log(`\n[${network.domain}] acting as ${one} and ${two} at once`)

        const client = adminClient()
        try {
          await client.connect()
          const keys = recordKeys(client)
          await Promise.all([
            act(client, network.domain, one, asked(RENEWAL, one), two),
            act(client, network.domain, two, asked(SWEEP, two), one)
          ])
          // Each checked before the client closes, so what ended it is its own
          // session's close rather than the client's sweep.
          assert.ok(keys.length >= 2, `${keys.length} keys were minted`)
          for (const key of keys) {
            assert.ok(!(await accepted(key)), `key ${key.keyId} still works`)
          }
        } finally {
          await client.close()
        }
      } finally {
        await created.remove()
      }
    })
  }
)
