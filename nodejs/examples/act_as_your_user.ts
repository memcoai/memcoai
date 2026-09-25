/**
 * Act as one of your users: search and write in a session that carries their
 * key.
 *
 * Opening a session with an `externalId` mints a short-lived key acting as that
 * user, and every call through the session carries it — so what the session
 * finds and writes is that user's, bounded by the network they are in. The key
 * renews itself while the session is open and is ended when it closes.
 *
 * Run it with:
 *
 *     export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...
 *     npm run build:test && node build/js/examples/act_as_your_user.js
 */

import { randomBytes } from 'node:crypto'
import { setTimeout as sleep } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

import {
  Memco,
  RevertOutcome,
  type ExternalUser,
  type RevertResult
} from '../src/index.js'

const DOMAIN = 'coding'
// A second apart: how long to wait for the write to be ingested.
const REVERT_ATTEMPTS = 60

/** Run the example. */
async function main(): Promise<void> {
  // Keeps this run's names, and what it writes, apart from any other run's.
  const run = `nodeex-${randomBytes(4).toString('hex')}`

  await using client = await new Memco().connect()

  // The user to act as, placed in a customer network of their own; see
  // map_your_users.ts. Removed again at the end, even after an error, but not
  // after Ctrl-C.
  const [root] = (
    await client.networks.list({ parentId: 'root', domain: DOMAIN })
  ).networks
  if (root === undefined) {
    throw new Error(`no root network in ${DOMAIN}`)
  }
  const network = await client.networks.create({
    name: `${run} Acme Corp`,
    parentId: root.id,
    scope: 'customer'
  })
  let user: ExternalUser | undefined
  try {
    user = await client.users.create(`${run}-acme-alice`, {
      name: 'Alice Andersson',
      roles: ['creator']
    })
    await client.networks.addMember(network.id, { userId: user.id })

    // Leaving the block ends the key. A session opened with startSession
    // instead must be closed with session.close().
    await using session = await client.memory.withSession(DOMAIN, {
      externalId: user.externalId
    })
    const question = `how does a Node.js service act for one of its own users (${run})`
    const result = await session.search(question)
    console.log(`${user.externalId} sees ${result.memories.length} memories`)

    const written = await session.createMemory({
      query: question,
      title: "A Node.js service acts for a user through that user's session",
      content:
        'In the Memco Node.js SDK, a service acts for one of its own users by ' +
        "opening a memory session with that user's externalId, usually in an " +
        '`await using` block. The session carries a short-lived key acting as ' +
        'the user, so what it finds and writes is bounded by the network the ' +
        'user was placed in, and leaving the block ends the key at once.'
    })
    console.log(`written as ${user.externalId}: ${written.operationId}`)

    // The write is the user's, so it is undone through their session. It is
    // ingested asynchronously, and until it has been, reverting reports
    // NOT_FOUND rather than removing it.
    if (written.operationId !== null) {
      let reverted: RevertResult | undefined
      for (let attempt = 0; attempt < REVERT_ATTEMPTS; attempt += 1) {
        reverted = await session.revertMemory(written.operationId)
        if (reverted.outcome !== RevertOutcome.NOT_FOUND) break
        await sleep(1000)
      }
      console.log(`revert: ${RevertOutcome[reverted!.outcome]}`)
    }
  } finally {
    if (user !== undefined) {
      await client.users.delete(user.externalId)
    }
    await client.networks.delete(network.id)
    console.log('removed again')
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
