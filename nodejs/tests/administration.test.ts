/**
 * Network and user administration: what each method sends, and what it hands
 * back.
 *
 * Every method is driven against the fake administration service and read off
 * the wire, so a field sent under the wrong name, or a result read from the
 * wrong one, fails here rather than against the real service.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { inspect } from 'node:util'

import { status } from '@grpc/grpc-js'

import { Memco } from '../src/client.js'
import {
  MemcoAlreadyExistsError,
  MemcoExternalUserNeedsCustomerNetworkError,
  MemcoInternalError,
  MemcoInvalidRequestError,
  MemcoPreconditionFailedError,
  MemcoUserAlreadyAssignedNetworkError
} from '../src/errors.js'
import * as pb from '../src/internal/gen.js'
import { CreatedKey } from '../src/types.js'
import {
  KEY_VALUE,
  withHarness,
  type AdminMethod,
  type Harness
} from './fakeServer.js'

function credentialed(harness: Harness): Memco {
  return new Memco({
    clientId: 'client-a',
    clientSecret: 'secret-a',
    host: harness.address,
    tls: false
  })
}

/** Run `body` against a client authenticated as an API client. */
async function withClient(
  body: (memco: Memco, harness: Harness) => Promise<void>
): Promise<void> {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await body(memco, harness)
    } finally {
      await memco.close()
    }
  })
}

const NETWORK = {
  id: 'network-a',
  name: 'Acme',
  parentId: 'network-root',
  domain: 'coding',
  region: 'global',
  scope: 'customer',
  owner: 'acme',
  description: "Acme's support knowledge"
}

const MEMBER = { userId: 'user-a', email: 'ada@example.com', name: 'Ada' }

const EXTERNAL_USER = {
  id: 'xuser-a',
  externalId: 'customer-42',
  name: 'Ada',
  email: 'ada@example.com',
  roles: ['reader'],
  active: true
}

const KEY = {
  id: 'apikey-a',
  name: 'ci',
  valuePrefix: 'mk_live_ab',
  roles: ['reader'],
  scopes: ['mcp:read'],
  validUntil: new Date('2026-01-01T00:00:00Z')
}

/** One method: how to call it, what must arrive, and what must come back. */
interface Case {
  readonly name: string
  readonly call: (memco: Memco) => Promise<unknown>
  readonly rpc: AdminMethod
  readonly sent: unknown
  readonly returned: unknown
}

const a = pb.admin

const CASES: readonly Case[] = [
  {
    name: 'networks.list',
    call: memco =>
      memco.networks.list({
        name: 'Acme',
        scope: 'customer',
        owner: 'acme',
        domain: 'coding',
        parentId: 'root',
        ids: ['network-a'],
        page: 2,
        pageSize: 10
      }),
    rpc: 'listNetworks',
    sent: a.ListNetworksRequest.fromPartial({
      name: 'Acme',
      scope: 'customer',
      owner: 'acme',
      domain: 'coding',
      parentId: 'root',
      ids: ['network-a'],
      page: 2,
      pageSize: 10
    }),
    returned: { networks: [NETWORK], totalCount: 1 }
  },
  {
    name: 'networks.create',
    call: memco =>
      memco.networks.create({
        name: 'Acme',
        parentId: 'network-root',
        domain: 'coding',
        region: 'global',
        scope: 'customer',
        owner: 'acme',
        description: "Acme's support knowledge"
      }),
    rpc: 'createNetwork',
    sent: a.CreateNetworkRequest.fromPartial({
      name: 'Acme',
      parentId: 'network-root',
      domain: 'coding',
      region: 'global',
      scope: 'customer',
      owner: 'acme',
      description: "Acme's support knowledge"
    }),
    returned: { ...NETWORK, id: 'network-new' }
  },
  {
    name: 'networks.update',
    call: memco =>
      memco.networks.update('network-a', {
        name: 'Acme Corp',
        description: ''
      }),
    rpc: 'updateNetwork',
    sent: a.UpdateNetworkRequest.fromPartial({
      id: 'network-a',
      name: 'Acme Corp',
      description: ''
    }),
    returned: { ...NETWORK, name: 'Acme Corp', description: '' }
  },
  {
    name: 'networks.delete',
    call: memco => memco.networks.delete('network-a'),
    rpc: 'deleteNetwork',
    sent: a.DeleteNetworkRequest.fromPartial({ id: 'network-a' }),
    returned: { id: 'network-a', removed: { memories: 3, network_members: 1 } }
  },
  {
    name: 'networks.listMembers',
    call: memco =>
      memco.networks.listMembers('network-a', {
        search: 'ada',
        page: 1,
        pageSize: 5
      }),
    rpc: 'listNetworkMembers',
    sent: a.ListNetworkMembersRequest.fromPartial({
      id: 'network-a',
      search: 'ada',
      page: 1,
      pageSize: 5
    }),
    returned: { members: [MEMBER], totalCount: 1 }
  },
  {
    name: 'networks.addMember',
    call: memco =>
      memco.networks.addMember('network-a', { userId: 'user-a', force: true }),
    rpc: 'addNetworkMember',
    sent: a.AddNetworkMemberRequest.fromPartial({
      id: 'network-a',
      userId: 'user-a',
      force: true
    }),
    returned: { networkId: 'network-a', userId: 'user-a', movedFrom: null }
  },
  {
    name: 'networks.removeMember',
    call: memco =>
      memco.networks.removeMember('network-a', { userId: 'user-a' }),
    rpc: 'removeNetworkMember',
    sent: a.RemoveNetworkMemberRequest.fromPartial({
      id: 'network-a',
      userId: 'user-a'
    }),
    returned: undefined
  },
  {
    name: 'networks.listGroups',
    call: memco =>
      memco.networks.listGroups({
        name: 'Support',
        networkId: 'network-a',
        ids: ['group-a'],
        page: 1,
        pageSize: 5
      }),
    rpc: 'listGroups',
    sent: a.ListGroupsRequest.fromPartial({
      name: 'Support',
      networkId: 'network-a',
      ids: ['group-a'],
      page: 1,
      pageSize: 5
    }),
    returned: {
      groups: [
        {
          id: 'group-a',
          name: 'Support',
          memoryNetworkId: 'network-a',
          memberCount: 2
        }
      ],
      totalCount: 1
    }
  },
  {
    name: 'networks.listGroupMembers',
    call: memco => memco.networks.listGroupMembers('group-a'),
    rpc: 'listGroupMembers',
    sent: a.ListGroupMembersRequest.fromPartial({ id: 'group-a' }),
    returned: [MEMBER]
  },
  {
    name: 'networks.addGroup',
    call: memco => memco.networks.addGroup('network-a', { groupId: 'group-a' }),
    rpc: 'addNetworkGroup',
    sent: a.AddNetworkGroupRequest.fromPartial({
      id: 'network-a',
      groupId: 'group-a'
    }),
    returned: undefined
  },
  {
    name: 'networks.removeGroup',
    call: memco =>
      memco.networks.removeGroup('network-a', { groupId: 'group-a' }),
    rpc: 'removeNetworkGroup',
    sent: a.RemoveNetworkGroupRequest.fromPartial({
      id: 'network-a',
      groupId: 'group-a'
    }),
    returned: undefined
  },
  {
    name: 'users.list',
    call: memco => memco.users.list({ search: 'acme', page: 1, pageSize: 5 }),
    rpc: 'listExternalUsers',
    sent: a.ListExternalUsersRequest.fromPartial({
      search: 'acme',
      page: 1,
      pageSize: 5
    }),
    returned: { externalUsers: [EXTERNAL_USER], totalCount: 1 }
  },
  {
    name: 'users.get',
    call: memco => memco.users.get('customer-42'),
    rpc: 'getExternalUser',
    sent: a.GetExternalUserRequest.fromPartial({ externalId: 'customer-42' }),
    returned: EXTERNAL_USER
  },
  {
    name: 'users.create',
    call: memco =>
      memco.users.create('customer-42', {
        roles: ['reader', 'creator'],
        name: 'Ada',
        email: 'ada@example.com'
      }),
    rpc: 'createExternalUser',
    sent: a.CreateExternalUserRequest.fromPartial({
      externalId: 'customer-42',
      roles: ['reader', 'creator'],
      name: 'Ada',
      email: 'ada@example.com'
    }),
    returned: {
      ...EXTERNAL_USER,
      id: 'xuser-new',
      roles: ['reader', 'creator']
    }
  },
  {
    name: 'users.update',
    call: memco => memco.users.update('customer-42', { roles: ['creator'] }),
    rpc: 'updateExternalUser',
    sent: a.UpdateExternalUserRequest.fromPartial({
      externalId: 'customer-42',
      roles: ['creator']
    }),
    returned: { ...EXTERNAL_USER, roles: ['creator'] }
  },
  {
    name: 'users.delete',
    call: memco => memco.users.delete('customer-42'),
    rpc: 'deleteExternalUser',
    sent: a.DeleteExternalUserRequest.fromPartial({
      externalId: 'customer-42'
    }),
    returned: undefined
  },
  {
    name: 'users.listKeys',
    call: memco => memco.users.listKeys('customer-42'),
    rpc: 'listExternalUserKeys',
    sent: a.ListExternalUserKeysRequest.fromPartial({
      externalId: 'customer-42'
    }),
    returned: [KEY]
  },
  {
    name: 'users.createKey',
    call: async memco => {
      const created = await memco.users.createKey('customer-42', {
        preset: 'mcp_ro',
        name: 'ci',
        validUntil: new Date('2026-01-01T00:00:00Z')
      })
      assert.ok(created instanceof CreatedKey)
      return { key: created.key, value: created.value }
    },
    rpc: 'createExternalUserKey',
    sent: a.CreateExternalUserKeyRequest.fromPartial({
      externalId: 'customer-42',
      preset: 'mcp_ro',
      name: 'ci',
      validUntil: 1767225600
    }),
    returned: { key: KEY, value: KEY_VALUE }
  },
  {
    name: 'users.deleteKey',
    call: memco => memco.users.deleteKey('customer-42', { keyId: 'apikey-a' }),
    rpc: 'deleteExternalUserKey',
    sent: a.DeleteExternalUserKeyRequest.fromPartial({
      externalId: 'customer-42',
      keyId: 'apikey-a'
    }),
    returned: undefined
  }
]

test('the round trip covers every administration method but impersonation', () => {
  // Impersonation is not an administration method: it is how a session acting
  // for a user is opened, and is covered where sessions are.
  const covered = new Set<string>(CASES.map(one => one.rpc))
  const declared = Object.keys(pb.admin.AdminServiceService).filter(
    name => !['impersonateExternalUser', 'endImpersonation'].includes(name)
  )
  assert.deepEqual([...covered].sort(), declared.sort())
})

for (const one of CASES) {
  test(`${one.name} sends its request and returns its result`, async () => {
    await withClient(async (memco, harness) => {
      const returned = await one.call(memco)
      assert.deepEqual(harness.admin.calls, [one.rpc])
      assert.deepEqual(harness.admin.requests.get(one.rpc), one.sent)
      assert.deepEqual(returned, one.returned)
      // Under the client's own token, which the exchange just issued.
      assert.deepEqual(harness.admin.authorization, [['Bearer client-token-1']])
    })
  })
}

// --- what is sent --------------------------------------------------------

test('filters and pages left unset send nothing', async () => {
  await withClient(async (memco, harness) => {
    await memco.networks.list()
    await memco.networks.listGroups()
    await memco.networks.listMembers('network-a')
    await memco.users.list()
    assert.deepEqual(harness.admin.received, [
      a.ListNetworksRequest.fromPartial({}),
      a.ListGroupsRequest.fromPartial({}),
      a.ListNetworkMembersRequest.fromPartial({ id: 'network-a' }),
      a.ListExternalUsersRequest.fromPartial({})
    ])
  })
})

test('ids may be any iterable, a one-shot generator included', async () => {
  await withClient(async (memco, harness) => {
    function* ids(): Generator<string> {
      yield 'network-a'
      yield 'network-b'
    }
    await memco.networks.list({ ids: ids() })
    assert.deepEqual(
      (
        harness.admin.requests.get(
          'listNetworks'
        ) as pb.admin.ListNetworksRequest
      ).ids,
      ['network-a', 'network-b']
    )
  })
})

const NETWORK_PATCHABLE = [
  'name',
  'parentId',
  'scope',
  'owner',
  'description'
] as const

for (const field of NETWORK_PATCHABLE) {
  test(`a network patch sends ${field} only when given, and sends "" to clear it`, async () => {
    await withClient(async (memco, harness) => {
      await memco.networks.update('network-a', { [field]: 'changed' })
      const given = harness.admin.requests.get(
        'updateNetwork'
      ) as pb.admin.UpdateNetworkRequest
      for (const other of NETWORK_PATCHABLE) {
        assert.equal(given[other], other === field ? 'changed' : undefined)
      }
      await memco.networks.update('network-a', { [field]: '' })
      const cleared = harness.admin.requests.get(
        'updateNetwork'
      ) as pb.admin.UpdateNetworkRequest
      assert.equal(cleared[field], '')
    })
  })
}

test('a network patch given nothing sends no field, and a null means unset', async () => {
  await withClient(async (memco, harness) => {
    await memco.networks.update('network-a')
    await memco.networks.update('network-a', {
      name: null as unknown as string
    })
    for (const sent of harness.admin.received) {
      assert.deepEqual(
        sent,
        a.UpdateNetworkRequest.fromPartial({ id: 'network-a' })
      )
    }
  })
})

test('a user patch sends only what it was given', async () => {
  await withClient(async (memco, harness) => {
    await memco.users.update('customer-42', { name: 'Ada Lovelace' })
    assert.deepEqual(
      harness.admin.requests.get('updateExternalUser'),
      a.UpdateExternalUserRequest.fromPartial({
        externalId: 'customer-42',
        name: 'Ada Lovelace'
      })
    )
    await memco.users.update('customer-42', { email: '' })
    const cleared = harness.admin.requests.get(
      'updateExternalUser'
    ) as pb.admin.UpdateExternalUserRequest
    assert.deepEqual(
      [cleared.name, cleared.email, cleared.roles],
      [undefined, '', []]
    )
  })
})

test("roles, presets and scopes are the service's to judge", async () => {
  // Which roles exist, and which presets a user's roles allow, is the
  // service's to say: an SDK that hardcoded them would go stale.
  await withClient(async (memco, harness) => {
    await memco.users.create('customer-42', { roles: ['admin'] })
    await memco.users.createKey('customer-42', { preset: 'no-such-preset' })
    await memco.networks.create({ name: 'Acme', scope: 'no-such-scope' })
    assert.deepEqual(harness.admin.calls, [
      'createExternalUser',
      'createExternalUserKey',
      'createNetwork'
    ])
  })
})

test('no expiry sends zero, for the service default', async () => {
  await withClient(async (memco, harness) => {
    await memco.users.createKey('customer-42', { preset: 'mcp_ro' })
    const sent = harness.admin.requests.get(
      'createExternalUserKey'
    ) as pb.admin.CreateExternalUserKeyRequest
    assert.equal(sent.validUntil, 0)
  })
})

test('an expiry is sent as the whole second it falls in', async () => {
  await withClient(async (memco, harness) => {
    await memco.users.createKey('customer-42', {
      preset: 'mcp_ro',
      validUntil: new Date(1767225600_999)
    })
    const sent = harness.admin.requests.get(
      'createExternalUserKey'
    ) as pb.admin.CreateExternalUserKeyRequest
    assert.equal(sent.validUntil, 1767225600)
  })
})

// --- what comes back -----------------------------------------------------

test('a network reads its absent fields as null', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.responses.set(
      'createNetwork',
      a.Network.fromPartial({
        id: 'network-root',
        name: 'Root',
        domain: 'coding'
      })
    )
    const network = await memco.networks.create({ name: 'Root' })
    assert.deepEqual(
      [network.parentId, network.scope, network.owner, network.description],
      [null, null, null, '']
    )
  })
})

test('a placement names the network a moved user left', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.responses.set(
      'addNetworkMember',
      a.AddNetworkMemberResponse.fromPartial({
        id: 'network-b',
        userId: 'user-a',
        movedFrom: 'network-a'
      })
    )
    const placed = await memco.networks.addMember('network-b', {
      userId: 'user-a',
      force: true
    })
    assert.equal(placed.movedFrom, 'network-a')
  })
})

test('a placement made without force sends force false', async () => {
  await withClient(async (memco, harness) => {
    await memco.networks.addMember('network-a', { userId: 'user-a' })
    const sent = harness.admin.requests.get(
      'addNetworkMember'
    ) as pb.admin.AddNetworkMemberRequest
    assert.equal(sent.force, false)
  })
})

test('a deletion reports what the cascade removed, sorted by table', async () => {
  await withClient(async memco => {
    const deleted = await memco.networks.delete('network-a')
    // The wire keeps no order in a map; sorted, it reads the same every time.
    assert.deepEqual(Object.keys(deleted.removed), [
      'memories',
      'network_members'
    ])
  })
})

test('a group assigned to no network reads it as null', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.responses.set(
      'listGroups',
      a.ListGroupsResponse.fromPartial({
        groups: [{ id: 'group-a', name: 'Support', memberCount: 0 }],
        totalCount: 1
      })
    )
    const { groups } = await memco.networks.listGroups()
    assert.equal(groups[0]?.memoryNetworkId, null)
  })
})

test('a key that reports no expiry reads it as null', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.responses.set(
      'listExternalUserKeys',
      a.ListExternalUserKeysResponse.fromPartial({
        keys: [{ id: 'apikey-a', validUntil: 0 }]
      })
    )
    const keys = await memco.users.listKeys('customer-42')
    assert.equal(keys[0]?.validUntil, null)
  })
})

test('a created key carries its value, and never shows it', async () => {
  await withClient(async memco => {
    const created = await memco.users.createKey('customer-42', {
      preset: 'mcp_ro'
    })
    assert.equal(created.value, KEY_VALUE)
    for (const rendered of [
      inspect(created),
      inspect(created, { showHidden: true, depth: null, getters: true }),
      JSON.stringify(created),
      JSON.stringify({ created }),
      String(created),
      inspect({ ...created }),
      JSON.stringify(structuredClone(created))
    ]) {
      assert.ok(!rendered.includes('supersecret'), rendered)
    }
    // What describes the key is still legible.
    assert.match(inspect(created), /apikey-a/)
    assert.match(JSON.stringify(created), /mk_live_ab/)
  })
})

test('a created key arriving without its key is an internal error that names no value', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.responses.set(
      'createExternalUserKey',
      a.CreateExternalUserKeyResponse.fromPartial({ value: KEY_VALUE })
    )
    await assert.rejects(
      memco.users.createKey('customer-42', { preset: 'mcp_ro' }),
      (error: unknown) => {
        assert.ok(error instanceof MemcoInternalError)
        assert.ok(!inspect(error).includes('supersecret'), inspect(error))
        return true
      }
    )
  })
})

// --- refused before anything is sent -------------------------------------

const REJECTED: readonly [string, (memco: Memco) => Promise<unknown>][] = [
  ['networks.update blank', m => m.networks.update(' ', { name: 'x' })],
  ['networks.delete blank', m => m.networks.delete('')],
  ['networks.listMembers blank', m => m.networks.listMembers('')],
  [
    'networks.addMember blank network',
    m => m.networks.addMember('', { userId: 'u' })
  ],
  [
    'networks.addMember blank user',
    m => m.networks.addMember('n', { userId: ' ' })
  ],
  [
    'networks.removeMember blank user',
    m => m.networks.removeMember('n', { userId: '' })
  ],
  [
    'networks.removeMember blank network',
    m => m.networks.removeMember('', { userId: 'u' })
  ],
  ['networks.listGroupMembers blank', m => m.networks.listGroupMembers('')],
  [
    'networks.addGroup blank group',
    m => m.networks.addGroup('n', { groupId: '' })
  ],
  [
    'networks.removeGroup blank network',
    m => m.networks.removeGroup('', { groupId: 'g' })
  ],
  ['networks.list bare string ids', m => m.networks.list({ ids: 'network-a' })],
  [
    'networks.listGroups bare string ids',
    m => m.networks.listGroups({ ids: 'group-a' })
  ],
  ['users.get blank', m => m.users.get('')],
  ['users.create blank', m => m.users.create(' ', { roles: ['reader'] })],
  ['users.create no roles', m => m.users.create('x', { roles: [] })],
  [
    'users.create roles missing',
    m => m.users.create('x', {} as { roles: string[] })
  ],
  [
    'users.create bare string roles',
    m => m.users.create('x', { roles: 'reader' })
  ],
  ['users.update blank', m => m.users.update('', { name: 'x' })],
  ['users.update no roles', m => m.users.update('x', { roles: [] })],
  [
    'users.update bare string roles',
    m => m.users.update('x', { roles: 'reader' })
  ],
  ['users.delete blank', m => m.users.delete('')],
  ['users.listKeys blank', m => m.users.listKeys('')],
  ['users.createKey blank', m => m.users.createKey('', { preset: 'mcp_ro' })],
  [
    'users.createKey invalid expiry',
    m =>
      m.users.createKey('x', { preset: 'mcp_ro', validUntil: new Date('nope') })
  ],
  [
    'users.createKey expiry not a Date',
    m =>
      m.users.createKey('x', {
        preset: 'mcp_ro',
        validUntil: '2026-01-01' as unknown as Date
      })
  ],
  ['users.deleteKey blank key', m => m.users.deleteKey('x', { keyId: '' })],
  ['users.deleteKey blank user', m => m.users.deleteKey(' ', { keyId: 'k' })]
]

for (const [name, call] of REJECTED) {
  test(`${name} is refused before anything is sent`, async () => {
    await withClient(async (memco, harness) => {
      await assert.rejects(call(memco), MemcoInvalidRequestError)
      // Not even the token exchange: the request is refused as it is built.
      assert.deepEqual(harness.admin.calls, [])
      assert.deepEqual(harness.tokens.calls, [])
    })
  })
}

// --- how the service refuses ---------------------------------------------

test('a duplicate raises AlreadyExists', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.error = {
      code: status.ALREADY_EXISTS,
      details: 'external user already exists'
    }
    await assert.rejects(
      memco.users.create('customer-42', { roles: ['reader'] }),
      MemcoAlreadyExistsError
    )
  })
})

test('a refused move names the network the user is already in', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.richError = {
      code: status.FAILED_PRECONDITION,
      details: 'network_move_required: user is already in Acme',
      reason: 'USER_ALREADY_ASSIGNED_NETWORK',
      domain: 'memco.ai',
      metadata: {
        current_network_id: 'network-a',
        current_network_name: 'Acme'
      }
    }
    await assert.rejects(
      memco.networks.addMember('network-b', { userId: 'user-a' }),
      (error: unknown) => {
        assert.ok(error instanceof MemcoUserAlreadyAssignedNetworkError)
        assert.equal(error.currentNetworkId, 'network-a')
        assert.equal(error.currentNetworkName, 'Acme')
        assert.match(error.message, /network_move_required/)
        return true
      }
    )
  })
})

test('a customer-network refusal names the scope it needs, and is no sunset', async () => {
  await withClient(async (memco, harness) => {
    harness.admin.richError = {
      code: status.FAILED_PRECONDITION,
      details: 'external users can only join customer networks',
      reason: 'EXTERNAL_USER_NEEDS_CUSTOMER_NETWORK',
      domain: 'memco.ai',
      metadata: { required_network_scope: 'customer' }
    }
    await assert.rejects(
      memco.networks.addMember('network-root', { userId: 'user-a' }),
      (error: unknown) => {
        assert.ok(error instanceof MemcoExternalUserNeedsCustomerNetworkError)
        assert.ok(error instanceof MemcoPreconditionFailedError)
        assert.equal(error.requiredNetworkScope, 'customer')
        assert.deepEqual(error.metadata, { required_network_scope: 'customer' })
        return true
      }
    )
  })
})
