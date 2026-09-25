/**
 * Map a customer's organisation into Memco: a company network, projects under
 * it, and its people.
 *
 * A consulting company becomes a customer network of its own, and each of its
 * client projects a customer network under it. Its engineers become external
 * users — no sign-in of their own; your API client acts for them — and each is
 * placed in exactly one network per domain:
 *
 * - placed in a project, an engineer sees that project's knowledge and the
 *   company's, and never another project's;
 * - placed in the company network, they see only what the company shares.
 *
 * Moving an engineer to another project is refused unless the move is asked
 * for. Everything created here is removed again when the program finishes, or
 * fails part-way; a real integration keeps it. A run interrupted with Ctrl-C
 * leaves what it made, named with its `nodeex-` prefix.
 *
 * Run it with:
 *
 *     export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...
 *     npm run build:test && node build/js/examples/map_your_users.js
 */

import { randomBytes } from 'node:crypto'
import { fileURLToPath } from 'node:url'

import {
  Memco,
  MemcoAlreadyExistsError,
  MemcoUserAlreadyAssignedNetworkError,
  type ExternalUser,
  type Network
} from '../src/index.js'

const DOMAIN = 'coding'
const COMPANY = 'Acme Consulting'
const PROJECTS = { billing: 'Billing platform', mobile: 'Mobile app' }

// Each engineer by your own id for them — Memco keeps it as their external id,
// so you never need to store an id of Memco's — with where they work.
const ENGINEERS: Record<string, [string, 'company' | keyof typeof PROJECTS]> = {
  maria: ['Maria Lind', 'company'], // the practice lead, across every project
  alice: ['Alice Andersson', 'billing'],
  bob: ['Bob Berg', 'billing'],
  carol: ['Carol Chen', 'mobile'],
  dave: ['Dave Dahl', 'mobile']
}

/** Run the example. */
async function main(): Promise<void> {
  // Keeps this run's names apart from any other run's.
  const run = `nodeex-${randomBytes(4).toString('hex')}`

  // Reads MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET, exchanges them for a token,
  // and renews that token by itself for as long as the client is open.
  await using client = await new Memco().connect()

  // The company network hangs under the domain's root network, and each
  // project under the company, taking its domain from it.
  const [root] = (
    await client.networks.list({ parentId: 'root', domain: DOMAIN })
  ).networks
  if (root === undefined) {
    throw new Error(`no root network in ${DOMAIN}`)
  }
  const networks: Partial<Record<'company' | keyof typeof PROJECTS, Network>> =
    {}
  const users: Record<string, ExternalUser> = {}
  try {
    const company = await client.networks.create({
      name: `${run} ${COMPANY}`,
      parentId: root.id,
      scope: 'customer',
      description: 'Practice that applies to every client project'
    })
    networks.company = company
    for (const [key, title] of Object.entries(PROJECTS)) {
      networks[key as keyof typeof PROJECTS] = await client.networks.create({
        name: `${run} ${COMPANY} / ${title}`,
        parentId: company.id,
        scope: 'customer'
      })
    }

    for (const [handle, [name, where]] of Object.entries(ENGINEERS)) {
      const externalId = `${run}-acme-${handle}`
      let user: ExternalUser
      try {
        user = await client.users.create(externalId, {
          name,
          roles: ['creator']
        })
      } catch (error) {
        // A real integration's ids carry no run suffix, so mapping someone a
        // second time finds them already there.
        if (!(error instanceof MemcoAlreadyExistsError)) throw error
        user = await client.users.get(externalId)
      }
      users[handle] = user
      await client.networks.addMember(networks[where]!.id, { userId: user.id })
    }

    for (const network of Object.values(networks)) {
      const { members } = await client.networks.listMembers(network.id)
      console.log(
        `${network.name}: ${members.map(member => member.name).join(', ')}`
      )
    }

    // Bob moves from billing to the mobile app. Placing him again is refused
    // while he is in another network of the same domain, as
    // USER_ALREADY_ASSIGNED_NETWORK, naming the network he is in.
    const bob = users['bob']!
    const mobile = networks.mobile!
    try {
      await client.networks.addMember(mobile.id, { userId: bob.id })
    } catch (error) {
      if (!(error instanceof MemcoUserAlreadyAssignedNetworkError)) throw error
      console.log(
        `\nrefused: ${MemcoUserAlreadyAssignedNetworkError.reason}: ${bob.name} ` +
          `is already in ${error.currentNetworkName} (${error.currentNetworkId})`
      )
    }
    // Asking for the move takes him out of billing and into mobile.
    const moved = await client.networks.addMember(mobile.id, {
      userId: bob.id,
      force: true
    })
    const left = Object.values(networks).find(
      network => network.id === moved.movedFrom
    )
    console.log(`moved ${bob.name} out of ${left?.name}`)

    // A key lets one engineer's own agent reach Memco directly, over MCP. Its
    // value is returned here and never again: hand it over now.
    const alice = users['alice']!
    const created = await client.users.createKey(alice.externalId, {
      preset: 'mcp_ro'
    })
    console.log(
      `\nkey ${created.key.valuePrefix}... expires ${created.key.validUntil?.toISOString()}`
    )
    await client.users.deleteKey(alice.externalId, { keyId: created.key.id })
  } finally {
    // Deleting a user revokes its keys. Projects go before the company network
    // they hang under.
    for (const user of Object.values(users)) {
      await client.users.delete(user.externalId)
    }
    for (const network of Object.values(networks).toReversed()) {
      await client.networks.delete(network.id)
    }
    console.log('removed again')
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
