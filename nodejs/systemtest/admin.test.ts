/**
 * Network and user administration against the real service, as an API client.
 *
 * A customer network under a root, its groups, an external user, that user's
 * keys, and where the user may be placed — split by concern, so a failure names
 * the part that broke rather than everything after it.
 *
 * Everything is created fresh, under names this run alone carries, and
 * removed again: each test removes what it made and asserts that it went, and
 * {@link Created} removes whatever a failing test left behind.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  MemcoAlreadyExistsError,
  MemcoExternalUserNeedsCustomerNetworkError,
  MemcoInvalidRequestError,
  MemcoNotFoundError,
  MemcoPermissionError,
  MemcoPreconditionFailedError,
  MemcoUserAlreadyAssignedNetworkError
} from '../src/index.js'
import type { Memco } from '../src/index.js'
import {
  Created,
  NO_CLIENT,
  hasClient,
  rootNetwork,
  withAdmin
} from './support.js'

// A key created without an expiry is given three months, which the calendar
// makes anything from 89 to 92 days; the margin either side absorbs the clock
// and the service's rounding.
const DAY_MS = 86_400_000
const KEY_LIFETIME_FLOOR_MS = 88 * DAY_MS
const KEY_LIFETIME_CEILING_MS = 93 * DAY_MS

/** Run `body` with an API client and a record of what it creates. */
async function withCreated(
  body: (admin: Memco, created: Created) => Promise<void>
): Promise<void> {
  await withAdmin(async admin => {
    const created = new Created(admin, await rootNetwork(admin))
    try {
      await body(admin, created)
    } finally {
      await created.remove()
    }
  })
}

const skip = hasClient() ? false : NO_CLIENT

test(
  'a customer network is created, changed, found and deleted',
  { skip },
  async () => {
    await withCreated(async (admin, created) => {
      const root = await rootNetwork(admin)
      const roots = (
        await admin.networks.list({ parentId: 'root', domain: root.domain })
      ).networks
      assert.ok(roots.some(each => each.id === root.id))
      assert.deepEqual(
        new Set(roots.map(each => each.domain)),
        new Set([root.domain])
      )
      // Pages count from 1: asking for page 1 returns what asking for no page
      // does, where a count from 0 would return the second page instead.
      const first = await admin.networks.list({ parentId: 'root', pageSize: 1 })
      const paged = await admin.networks.list({
        parentId: 'root',
        page: 1,
        pageSize: 1
      })
      assert.deepEqual(paged.networks, first.networks)

      const network = await created.network()
      console.log(
        `\ncreated network ${network.id} under ${root.id} in ${network.domain}`
      )
      assert.deepEqual(
        [network.parentId, network.domain, network.scope],
        [root.id, root.domain, 'customer']
      )

      const description = 'A network the Node.js SDK system test has changed.'
      const changed = await admin.networks.update(network.id, { description })
      assert.equal(changed.description, description)
      assert.equal(changed.name, network.name, 'a field left out was changed')

      const found = (await admin.networks.list({ ids: [network.id] })).networks
      assert.deepEqual(
        found.map(each => [each.id, each.description]),
        [[network.id, description]]
      )

      await assert.rejects(
        admin.networks.update('', { description }),
        MemcoInvalidRequestError
      )

      assert.equal((await admin.networks.delete(network.id)).id, network.id)
      assert.deepEqual(
        (await admin.networks.list({ ids: [network.id] })).networks,
        []
      )
    })
  }
)

test(
  'groups are refused outside an enterprise, and assigned within one',
  { skip },
  async t => {
    // Groups belong to enterprise organisations only, and the service refuses
    // the whole surface to any other rather than listing none, so either outcome
    // passes; what fails is anything in between.
    await withCreated(async (admin, created) => {
      const network = await created.network()
      let groups
      try {
        groups = (await admin.networks.listGroups()).groups
      } catch (error) {
        if (!(error instanceof MemcoPermissionError)) {
          throw error
        }
        console.log(
          '\ngroups are refused: this is not an enterprise organisation'
        )
        await assert.rejects(
          admin.networks.addGroup(network.id, {
            groupId: 'group-that-is-never-reached'
          }),
          MemcoPermissionError
        )
        return
      }
      // Assigning replaces a group's network in the network's domain, and a
      // group reports one network id however many domains it is placed in, so a
      // real group's placement could not be put back for certain. Only a group
      // placed nowhere is used: taking it out again restores exactly that.
      const group = groups.find(each => each.memoryNetworkId === null)
      if (group === undefined) {
        t.skip(
          'the organisation has no identity-provider group placed in no network'
        )
        return
      }
      const members = await admin.networks.listGroupMembers(group.id)
      console.log(
        `\nassigning group ${group.id}, of ${members.length} members, to ${network.id}`
      )
      await admin.networks.addGroup(network.id, { groupId: group.id })
      try {
        const assigned = await admin.networks.listGroups({
          networkId: network.id
        })
        assert.ok(assigned.groups.some(each => each.id === group.id))
      } finally {
        await admin.networks.removeGroup(network.id, { groupId: group.id })
      }
      const remaining = await admin.networks.listGroups({
        networkId: network.id
      })
      assert.ok(!remaining.groups.some(each => each.id === group.id))
      const [restored] = (await admin.networks.listGroups({ ids: [group.id] }))
        .groups
      assert.equal(restored?.memoryNetworkId, null)
    })
  }
)

test(
  'an external user is created, found, changed and deleted',
  { skip },
  async () => {
    await withCreated(async (admin, created) => {
      const user = await created.user()
      console.log(`\ncreated external user ${user.externalId} as ${user.id}`)
      // A creator can read what it can write, and the service says so.
      assert.deepEqual(new Set(user.roles), new Set(['creator', 'reader']))

      await assert.rejects(
        admin.users.create(user.externalId, { roles: ['creator'] }),
        MemcoAlreadyExistsError
      )
      // An external user never holds admin.
      await assert.rejects(created.user(['admin']), MemcoInvalidRequestError)

      const fetched = await admin.users.get(user.externalId)
      assert.deepEqual(
        [fetched.id, new Set(fetched.roles)],
        [user.id, new Set(user.roles)]
      )
      const listed = await admin.users.list({ search: user.externalId })
      assert.deepEqual(
        listed.externalUsers.map(each => each.id),
        [user.id]
      )

      const name = 'Node.js SDK system test'
      const renamed = await admin.users.update(user.externalId, { name })
      assert.equal(renamed.name, name)
      assert.deepEqual(
        new Set(renamed.roles),
        new Set(user.roles),
        'roles left out were changed'
      )
      assert.equal((await admin.users.get(user.externalId)).name, name)

      await admin.users.delete(user.externalId)
      await assert.rejects(admin.users.get(user.externalId), MemcoNotFoundError)
    })
  }
)

test(
  "an external user's key is shown once, listed and revoked",
  { skip },
  async () => {
    await withCreated(async (admin, created) => {
      const user = await created.user()
      const key = await admin.users.createKey(user.externalId, {
        preset: 'mcp_ro',
        name: 'node-systemtest'
      })
      // Computed first rather than asserted on directly: a failed assertion
      // prints its operands, and the value is a live key.
      const prefixed = key.value.startsWith(key.key.valuePrefix)
      assert.ok(
        prefixed,
        "the key's value does not start with its listed prefix"
      )
      assert.ok(key.key.validUntil !== null)
      const left = key.key.validUntil.getTime() - Date.now()
      assert.ok(
        left >= KEY_LIFETIME_FLOOR_MS && left <= KEY_LIFETIME_CEILING_MS,
        `a key valid for ${(left / DAY_MS).toFixed(1)} days`
      )

      // The audit stream is for auditors, which a creator is not.
      await assert.rejects(
        admin.users.createKey(user.externalId, { preset: 'audit' }),
        MemcoInvalidRequestError
      )

      const ids = async (): Promise<string[]> =>
        (await admin.users.listKeys(user.externalId)).map(each => each.id)
      assert.ok((await ids()).includes(key.key.id))
      await admin.users.deleteKey(user.externalId, { keyId: key.key.id })
      assert.ok(!(await ids()).includes(key.key.id))
      await assert.rejects(
        admin.users.deleteKey(user.externalId, { keyId: key.key.id }),
        MemcoNotFoundError
      )
    })
  }
)

test(
  'an external user is placed only in a customer network, and moved only when asked',
  { skip },
  async () => {
    await withCreated(async (admin, created) => {
      const root = await rootNetwork(admin)
      const network = await created.network()
      const user = await created.user()

      await assert.rejects(
        admin.networks.addMember(root.id, { userId: user.id }),
        (error: unknown) => {
          assert.ok(error instanceof MemcoExternalUserNeedsCustomerNetworkError)
          assert.equal(error.requiredNetworkScope, 'customer')
          return true
        }
      )

      const placed = await admin.networks.addMember(network.id, {
        userId: user.id
      })
      assert.deepEqual(placed, {
        networkId: network.id,
        userId: user.id,
        movedFrom: null
      })
      const members = async (id: string): Promise<string[]> =>
        (await admin.networks.listMembers(id)).members.map(each => each.userId)
      assert.ok((await members(network.id)).includes(user.id))

      // A second network of the same domain refuses them, naming where they are.
      const other = await created.network()
      await assert.rejects(
        admin.networks.addMember(other.id, { userId: user.id }),
        (error: unknown) => {
          assert.ok(error instanceof MemcoUserAlreadyAssignedNetworkError)
          assert.deepEqual(
            [error.currentNetworkId, error.currentNetworkName],
            [network.id, network.name]
          )
          return true
        }
      )
      // Asked for, the move is made, and names the network it came from.
      const moved = await admin.networks.addMember(other.id, {
        userId: user.id,
        force: true
      })
      assert.equal(moved.movedFrom, network.id)
      assert.ok(!(await members(network.id)).includes(user.id))

      // A network holding an external user cannot stop being a customer one.
      await assert.rejects(
        admin.networks.update(other.id, { scope: 'internal' }),
        MemcoPreconditionFailedError
      )

      await admin.networks.removeMember(other.id, { userId: user.id })
      assert.ok(!(await members(other.id)).includes(user.id))
    })
  }
)
