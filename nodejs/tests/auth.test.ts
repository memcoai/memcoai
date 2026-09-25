/**
 * Where the credential goes, and where it deliberately does not.
 *
 * Asserted on the wire wherever it can be: the fake server records the metadata
 * each call arrived with, so "the health probe carries no credential" is proven
 * by what the server saw rather than by what the SDK was asked to do.
 *
 * The credential is passed with each call rather than attached to the channel.
 * A client holds more than one credential at once — its own, and a key per
 * impersonated session — so which one a call carries has to be an argument of
 * that call, not state every call on the channel shares.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { ServiceError } from '@grpc/grpc-js'

import { Memco } from '../src/client.js'
import { MemcoConfigError } from '../src/errors.js'
import { AUTH_HEADER, AUTH_SCHEME, metadata } from '../src/internal/auth.js'
import { buildTransport } from '../src/internal/channel.js'
import { resolve } from '../src/internal/config.js'
import * as pb from '../src/internal/gen.js'
import { withHarness } from './fakeServer.js'

const TOKEN = 'test-token'

test('the scheme is spelled with a capital B and a trailing space', () => {
  // The service matches it with a case-sensitive prefix check, so a lowercase
  // "bearer" or a missing space is rejected as an invalid credential.
  assert.equal(AUTH_SCHEME, 'Bearer ')
  assert.equal(AUTH_HEADER, 'authorization')
})

test('the metadata one call sends holds exactly one credential', () => {
  const sent = metadata('real-token')
  assert.deepEqual(sent.get(AUTH_HEADER), ['Bearer real-token'])
  assert.deepEqual(sent.toHttp2Headers()[AUTH_HEADER], ['Bearer real-token'])
})

test('a credential grpc-js would quote back is refused without being quoted', () => {
  // grpc-js reports an out-of-range metadata value by putting the value in the
  // Error message. resolve() refuses such a token first, and an issued token or
  // key would have to be malformed by the service to reach here — but this is
  // the last line before the credential leaves the SDK, and what it must never
  // do is repeat what grpc-js would have said.
  assert.throws(
    () => metadata('sk-live-supersecret​tail'),
    (error: unknown) => {
      assert.ok(error instanceof MemcoConfigError)
      assert.ok(!error.message.includes('supersecret'), error.message)
      assert.ok(!error.message.includes('sk-live'), error.message)
      return true
    }
  )
})

test('every call a client makes carries exactly one credential, and the probe none', async () => {
  await withHarness(async harness => {
    const memco = new Memco({ token: TOKEN, host: harness.address, tls: false })
    try {
      await memco.connect()
      await memco.memory.startSession('coding')
      await memco.memory.getMemory('memory-a-1')
      await memco.networks.list()
    } finally {
      await memco.close()
    }
    assert.ok(harness.health.metadata.length > 0, 'the probe did not run')
    assert.ok(!(AUTH_HEADER in harness.health.metadata[0]))
    const sent = [
      ...harness.memory.authorization,
      ...harness.admin.authorization
    ]
    assert.equal(sent.length, 5)
    for (const credentials of sent) {
      assert.deepEqual(credentials, [`Bearer ${TOKEN}`])
    }
  })
})

test('the token exchange carries no credential: the one in its body is what it proves', async () => {
  await withHarness(async harness => {
    const memco = new Memco({
      clientId: 'client-a',
      clientSecret: 'secret-a',
      host: harness.address,
      tls: false
    })
    try {
      await memco.connect()
    } finally {
      await memco.close()
    }
    assert.deepEqual(harness.tokens.calls, ['issueToken'])
    assert.deepEqual(harness.tokens.authorization, [[]])
  })
})

test('a call made on the transport directly carries no credential at all', async () => {
  // The credential is an argument of each call rather than state on the
  // channel, so a call that bypasses the SDK's own path fails closed: the
  // server sees nothing to authenticate with, rather than somebody's token.
  await withHarness(async harness => {
    const transport = buildTransport(
      resolve({ token: TOKEN, host: harness.address, tls: false, env: {} })
    )
    try {
      await new Promise<pb.ListDomainsResponse>((settle, fail) => {
        transport.memory.listDomains(
          {},
          (error: ServiceError | null, response) => {
            if (error) {
              fail(error)
              return
            }
            settle(response)
          }
        )
      })
    } finally {
      transport.close()
    }
    assert.deepEqual(harness.memory.authorization, [[]])
  })
})
