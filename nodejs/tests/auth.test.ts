/**
 * Where the credential goes, and where it deliberately does not.
 *
 * Asserted on the wire wherever it can be: the fake server records the metadata
 * each call arrived with, so "the health probe carries no credential" is proven
 * by what the server saw rather than by what the interceptor was asked to do.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { Metadata, type ServiceError } from '@grpc/grpc-js'

import { MemcoConfigError } from '../src/errors.js'
import {
  AUTH_HEADER,
  AUTH_SCHEME,
  UNAUTHENTICATED_PREFIX,
  merged
} from '../src/internal/auth.js'
import { buildTransport } from '../src/internal/channel.js'
import { resolve } from '../src/internal/config.js'
import * as pb from '../src/internal/gen.js'
import {
  ServingStatus,
  type HealthCheckResponse
} from '../src/internal/wire.js'
import { withHarness } from './fakeServer.js'

const TOKEN = 'test-token'

/** Await a unary call made in grpc-js's callback style. */
function unary<Res>(
  invoke: (
    callback: (error: ServiceError | null, response: Res) => void
  ) => void
): Promise<Res> {
  return new Promise<Res>((settle, fail) => {
    invoke((error, response) => {
      if (error) {
        fail(error)
        return
      }
      settle(response)
    })
  })
}

test('the scheme is spelled with a capital B and a trailing space', () => {
  // The service matches it with a case-sensitive prefix check, so a lowercase
  // "bearer" or a missing space is rejected as an invalid credential.
  assert.equal(AUTH_SCHEME, 'Bearer ')
  assert.equal(AUTH_HEADER, 'authorization')
  assert.equal(UNAUTHENTICATED_PREFIX, '/grpc.health.v1.')
})

test('caller metadata cannot displace the credential', () => {
  // gRPC allows repeated keys and servers disagree about which occurrence wins,
  // so a caller-supplied credential is dropped rather than sent alongside.
  const supplied = new Metadata()
  // `add`, not `set`, and in three spellings: gRPC permits repeated keys, so
  // the input this has to survive is several credentials, not one.
  supplied.add('authorization', 'Bearer ATTACKER')
  supplied.add('Authorization', 'Bearer ATTACKER-2')
  supplied.add('AUTHORIZATION', 'Bearer ATTACKER-3')
  supplied.set('x-other', 'keep')

  const combined = merged(supplied, 'real-token')
  assert.deepEqual(combined.get(AUTH_HEADER), ['Bearer real-token'])
  // And exactly one entry reaches the wire, which `get` above is the only
  // vantage point for: the server harness records only the first value.
  assert.deepEqual(combined.toHttp2Headers()[AUTH_HEADER], [
    'Bearer real-token'
  ])
  assert.deepEqual(combined.get('x-other'), ['keep'])
  // The caller's own object is left as it was — a Metadata reused across calls
  // must not find the credential accumulating in it.
  assert.deepEqual(supplied.get(AUTH_HEADER), [
    'Bearer ATTACKER',
    'Bearer ATTACKER-2',
    'Bearer ATTACKER-3'
  ])
})

test('a token grpc-js would quote back is refused without being quoted', () => {
  // grpc-js reports an out-of-range metadata value by putting the value in the
  // Error message. resolve() refuses such a token first, so this is the guard
  // behind that one — but it is the last line before the credential leaves the
  // SDK, and what it must never do is repeat what grpc-js would have said.
  assert.throws(
    () => merged(new Metadata(), 'sk-live-supersecret​tail'),
    (error: unknown) => {
      assert.ok(error instanceof MemcoConfigError)
      assert.ok(!error.message.includes('supersecret'), error.message)
      assert.ok(!error.message.includes('sk-live'), error.message)
      return true
    }
  )
})

test('a memory call carries the credential', async () => {
  await withHarness(async harness => {
    const transport = buildTransport(
      resolve({ token: TOKEN, host: harness.address, tls: false, env: {} })
    )
    try {
      await unary<pb.ListDomainsResponse>(callback => {
        transport.memory.listDomains({}, callback)
      })
    } finally {
      transport.close()
    }
    assert.equal(harness.memory.metadata.length, 1)
    assert.equal(
      harness.memory.metadata[0][AUTH_HEADER],
      `Bearer ${TOKEN}`,
      'the credential did not reach the server'
    )
  })
})

test('every memory method carries the credential', async () => {
  await withHarness(async harness => {
    const transport = buildTransport(
      resolve({ token: TOKEN, host: harness.address, tls: false, env: {} })
    )
    try {
      await unary<pb.ListDomainsResponse>(callback => {
        transport.memory.listDomains({}, callback)
      })
      await unary<pb.StartSessionResponse>(callback => {
        transport.memory.startSession({ domain: 'coding' }, callback)
      })
      await unary<pb.GetMemoryResponse>(callback => {
        transport.memory.getMemory({ idx: 'memory-a-1' }, callback)
      })
    } finally {
      transport.close()
    }
    assert.equal(harness.memory.metadata.length, 3)
    for (const sent of harness.memory.metadata) {
      assert.equal(sent[AUTH_HEADER], `Bearer ${TOKEN}`)
    }
  })
})

test('the credential is withheld from the health probe', async () => {
  // The health endpoint takes no credential, so sending one there buys nothing
  // and widens its exposure: the probe fires on every client construction, and
  // health paths are reached by infrastructure that logs headers liberally.
  await withHarness(async harness => {
    const transport = buildTransport(
      resolve({ token: TOKEN, host: harness.address, tls: false, env: {} })
    )
    try {
      const response = await unary<HealthCheckResponse>(callback => {
        transport.health.check({ service: '' }, {}, callback)
      })
      assert.equal(response.status, ServingStatus.SERVING)
    } finally {
      transport.close()
    }
    assert.ok(
      harness.health.metadata.length > 0,
      'the health probe did not run'
    )
    assert.ok(
      !(AUTH_HEADER in harness.health.metadata[0]),
      'the credential reached the health endpoint'
    )
  })
})

// The harness records `Metadata.getMap()`, which keeps only the first value per
// key, so this pins which credential the server reads — not how many arrived.
// "exactly one entry" is what the merged() test above proves.
test("the credential the server reads is the SDK's, not the caller's", async () => {
  await withHarness(async harness => {
    const transport = buildTransport(
      resolve({ token: TOKEN, host: harness.address, tls: false, env: {} })
    )
    const supplied = new Metadata()
    supplied.set('authorization', 'Bearer ATTACKER')
    try {
      await unary<pb.ListDomainsResponse>(callback => {
        transport.memory.listDomains({}, supplied, callback)
      })
    } finally {
      transport.close()
    }
    assert.equal(harness.memory.metadata[0][AUTH_HEADER], `Bearer ${TOKEN}`)
  })
})
