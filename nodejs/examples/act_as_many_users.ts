/**
 * Act as several of your users at once, each in a session of their own.
 *
 * One client holds one connection, and each session carries its own user's
 * key, so sessions for different users run side by side over it without ever
 * sharing a credential. Each key is ended as its session's block is left.
 *
 * Run it with:
 *
 *     export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...
 *     npm run build:test && node build/js/examples/act_as_many_users.js
 */

import { randomBytes } from 'node:crypto'
import { fileURLToPath } from 'node:url'

import { Memco, type ExternalUser } from '../src/index.js'

const DOMAIN = 'coding'
const QUESTION = 'how does a Node.js service act for one of its own users'

/**
 * Search as one user, in a session of their own.
 *
 * @param client A connected client holding the API client's credentials.
 * @param externalId Your id for the user to act as.
 * @returns A one-line summary of what the user found.
 */
async function ask(client: Memco, externalId: string): Promise<string> {
  await using session = await client.memory.withSession(DOMAIN, {
    externalId
  })
  const result = await session.search(QUESTION)
  return `${externalId} sees ${result.memories.length} memories`
}

/** Run the example. */
async function main(): Promise<void> {
  // Keeps this run's names apart from any other run's.
  const run = `nodeex-${randomBytes(4).toString('hex')}`

  await using client = await new Memco().connect()

  // Three users to act as, in one customer network; see map_your_users.ts.
  // Removed again at the end, even after an error, but not after Ctrl-C.
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
  const users: ExternalUser[] = []
  try {
    for (const handle of ['alice', 'bob', 'carol']) {
      const user = await client.users.create(`${run}-acme-${handle}`, {
        roles: ['reader']
      })
      users.push(user)
      await client.networks.addMember(network.id, { userId: user.id })
    }

    // All three sessions are open at the same time, each under its own key.
    const lines = await Promise.all(
      users.map(user => ask(client, user.externalId))
    )
    for (const line of lines) {
      console.log(line)
    }
  } finally {
    for (const user of users) {
      await client.users.delete(user.externalId)
    }
    await client.networks.delete(network.id)
    console.log('removed again')
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
