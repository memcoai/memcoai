/**
 * A client built from an API client's id and secret: the exchange, and the
 * token it renews.
 *
 * An issued token carries no content role, so the memory service refuses it.
 * Connecting therefore proves the credentials with the exchange itself and
 * calls no memory method; every call after carries the issued token until it
 * falls due, and the next one exchanges again.
 */

import assert from 'node:assert/strict'
import { test, type TestContext } from 'node:test'
import { inspect } from 'node:util'

import { status } from '@grpc/grpc-js'

import { Memco, type MemcoOptions } from '../src/client.js'
import {
  MemcoAuthenticationError,
  MemcoTimeoutError,
  MemcoUnavailableError
} from '../src/errors.js'
import * as pb from '../src/internal/gen.js'
import { DEFAULT_LEVEL, setLevel } from '../src/internal/logging.js'
import { FakeClock } from './fakeClock.js'
import { Hold, withHarness, type Harness } from './fakeServer.js'
import { stderrOf } from './stderr.js'

const SECRET = 'cs-live-supersecret-9f2b'

function credentialed(harness: Harness, options: MemcoOptions = {}): Memco {
  return new Memco({
    clientId: 'client-a',
    clientSecret: SECRET,
    host: harness.address,
    tls: false,
    ...options
  })
}

function issued(harness: Harness): pb.auth.IssueTokenRequest {
  return harness.tokens.requests.get('issueToken') as pb.auth.IssueTokenRequest
}

/** Wait until a token exchange in flight has been answered, and taken up. */
async function landed(harness: Harness): Promise<void> {
  const asked = harness.tokens.calls.length
  for (let attempt = 0; attempt < 100; attempt += 1) {
    await new Promise(settle => setTimeout(settle, 5))
    if (harness.tokens.calls.length >= asked) {
      break
    }
  }
  await new Promise(settle => setTimeout(settle, 20))
}

function faked(t: TestContext): FakeClock {
  const fake = new FakeClock().install()
  t.after(() => fake.restore())
  return fake
}

// --- connecting ----------------------------------------------------------

test('connecting probes health, then issues a token, and calls no memory method', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await memco.connect()
      assert.deepEqual(harness.health.checkedServices, [''])
      assert.deepEqual(harness.tokens.calls, ['issueToken'])
      // An issued token carries no content role, so ListDomains would be
      // refused: the exchange is the proof.
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('the health probe runs before a token is asked for', async () => {
  await withHarness(async harness => {
    harness.health.transientErrors.push(
      ...Array(3).fill({ code: status.UNAVAILABLE, details: 'down' })
    )
    const memco = credentialed(harness)
    try {
      await assert.rejects(memco.connect(), MemcoUnavailableError)
      assert.deepEqual(harness.tokens.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('the token request carries the credentials, and no bearer of its own', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await memco.connect()
    } finally {
      await memco.close()
    }
    assert.deepEqual(issued(harness), {
      grantType: 'client_credentials',
      clientId: 'client-a',
      clientSecret: SECRET,
      scope: [],
      // Zero takes the service's default lifetime.
      ttlSeconds: 0
    })
    assert.deepEqual(harness.tokens.authorization, [[]])
  })
})

test('tokenLifetime is sent as ttlSeconds', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness, { tokenLifetime: 600 })
    try {
      await memco.connect()
    } finally {
      await memco.close()
    }
    assert.equal(issued(harness).ttlSeconds, 600)
  })
})

test("a lifetime over the service cap is the service's to refuse", async () => {
  await withHarness(async harness => {
    harness.tokens.error = {
      code: status.INVALID_ARGUMENT,
      details: 'ttl_seconds exceeds 86400'
    }
    const memco = credentialed(harness, { tokenLifetime: 7 * 86400 })
    try {
      await assert.rejects(memco.connect(), {
        name: 'MemcoInvalidRequestError'
      })
      assert.equal(issued(harness).ttlSeconds, 7 * 86400)
    } finally {
      await memco.close()
    }
  })
})

test('connecting again reuses a token that is not yet due', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await memco.connect()
      await memco.connect()
      assert.deepEqual(harness.tokens.calls, ['issueToken'])
      assert.equal(harness.health.checkedServices.length, 2)
    } finally {
      await memco.close()
    }
  })
})

// --- the token each call carries -----------------------------------------

test('administration calls carry the issued token', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await memco.connect()
      await memco.networks.list()
      await memco.users.list()
      assert.deepEqual(harness.admin.authorization, [
        ['Bearer client-token-1'],
        ['Bearer client-token-1']
      ])
    } finally {
      await memco.close()
    }
  })
})

test('a first call issues the token itself when connect was never called', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await memco.networks.list()
      assert.deepEqual(harness.tokens.calls, ['issueToken'])
      assert.deepEqual(harness.admin.authorization, [['Bearer client-token-1']])
    } finally {
      await memco.close()
    }
  })
})

test('memory calls carry the client token too, for the service to judge', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await memco.memory.listDomains()
      assert.deepEqual(harness.memory.authorization, [
        ['Bearer client-token-1']
      ])
    } finally {
      await memco.close()
    }
  })
})

test('calls made at once share a single exchange', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await Promise.all(Array.from({ length: 8 }, () => memco.networks.list()))
      assert.deepEqual(harness.tokens.calls, ['issueToken'])
    } finally {
      await memco.close()
    }
  })
})

test('the token is renewed at four fifths of its lifetime', async t => {
  const fake = faked(t)
  await withHarness(async harness => {
    harness.tokens.expiresIn = 1000
    const memco = credentialed(harness)
    try {
      await memco.connect()
      fake.advance(799)
      await memco.networks.list()
      assert.deepEqual(harness.tokens.calls, ['issueToken'])
      fake.advance(2)
      // Due but valid: this call goes on under token 1 while the renewal runs
      // beside it, and the calls after it carry token 2.
      await memco.networks.list()
      await landed(harness)
      await memco.networks.list()
      assert.deepEqual(harness.tokens.calls, ['issueToken', 'issueToken'])
      assert.deepEqual(harness.admin.authorization, [
        ['Bearer client-token-1'],
        ['Bearer client-token-1'],
        ['Bearer client-token-2']
      ])
    } finally {
      await memco.close()
    }
  })
})

test('a token renewal that fails before the token expires keeps the token, with a warning', async t => {
  // The token service down at 0.85 of the lifetime: the token still works, so
  // the call goes out under it rather than failing.
  const fake = faked(t)
  await withHarness(async harness => {
    harness.tokens.expiresIn = 1000
    const memco = credentialed(harness)
    try {
      await memco.connect()
      fake.advance(850)
      harness.tokens.transientErrors.set('issueToken', [
        { code: status.UNAVAILABLE, details: 'token service down' }
      ])
      setLevel(DEFAULT_LEVEL)
      const written = await stderrOf(async () => {
        await Promise.all([memco.networks.list(), memco.users.list()])
        await landed(harness)
      })
      assert.deepEqual(harness.admin.authorization, [
        ['Bearer client-token-1'],
        ['Bearer client-token-1']
      ])
      assert.deepEqual(harness.tokens.calls, ['issueToken', 'issueToken'])
      const warnings = written
        .split('\n')
        .filter(line => line.includes('WARNING'))
      assert.equal(warnings.length, 1, written)
      assert.match(warnings[0] ?? '', /the client token/)
      assert.ok(!written.includes('client-token-1'), written)
      assert.ok(!written.includes('supersecret'), written)

      // At the end of its lifetime the token is gone, so a failure is raised.
      fake.advance(150)
      harness.tokens.transientErrors.set('issueToken', [
        { code: status.UNAVAILABLE, details: 'token service down' }
      ])
      await assert.rejects(memco.networks.list(), MemcoUnavailableError)
    } finally {
      await memco.close()
    }
  })
})

test('a slow token renewal does not hold calls while the token is still valid', async t => {
  const fake = faked(t)
  await withHarness(async harness => {
    harness.tokens.expiresIn = 1000
    const memco = credentialed(harness)
    try {
      await memco.connect()
      fake.advance(850)
      const hold = new Hold()
      harness.tokens.holds.set('issueToken', hold)
      const started = Date.now()
      await Promise.all([
        memco.networks.list({ timeout: 0.2 }),
        memco.users.list({ timeout: 0.2 })
      ])
      assert.ok(Date.now() - started < 150, 'the calls waited on the renewal')
      assert.deepEqual(harness.admin.authorization, [
        ['Bearer client-token-1'],
        ['Bearer client-token-1']
      ])
      await hold.arrived
      hold.release()
      harness.tokens.holds.delete('issueToken')
      await landed(harness)
      await memco.networks.list()
      assert.deepEqual(harness.admin.authorization.at(-1), [
        'Bearer client-token-2'
      ])
    } finally {
      await memco.close()
    }
  })
})

test('a failed renewal is raised typed, and the next call tries again', async t => {
  const fake = faked(t)
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await memco.connect()
      fake.advance(3600)
      harness.tokens.transientErrors.set('issueToken', [
        { code: status.UNAVAILABLE, details: 'token service down' }
      ])
      await assert.rejects(memco.networks.list(), MemcoUnavailableError)
      // Nothing was sent to the admin service on a credential that failed.
      assert.deepEqual(harness.admin.calls, [])
      await memco.networks.list()
      assert.deepEqual(harness.admin.authorization, [['Bearer client-token-2']])
    } finally {
      await memco.close()
    }
  })
})

test('calls waiting on one failed exchange share its failure rather than each exchanging again', async () => {
  // Each queued caller exchanging in turn would make the last of them wait out
  // every failure before its own, far past its deadline.
  await withHarness(async harness => {
    harness.tokens.delays.set('issueToken', 50)
    harness.tokens.transientErrors.set('issueToken', [
      { code: status.UNAVAILABLE, details: 'token service down' }
    ])
    const memco = credentialed(harness)
    try {
      const settled = await Promise.allSettled(
        Array.from({ length: 8 }, () => memco.networks.list())
      )
      assert.ok(settled.every(one => one.status === 'rejected'))
      assert.deepEqual(harness.tokens.calls, ['issueToken'])
      await memco.networks.list()
      assert.deepEqual(harness.tokens.calls, ['issueToken', 'issueToken'])
    } finally {
      await memco.close()
    }
  })
})

test('a close made while connecting waits for the exchange in flight', async () => {
  await withHarness(async harness => {
    harness.tokens.delays.set('issueToken', 100)
    const memco = credentialed(harness)
    const order: string[] = []
    const connecting = memco.connect().then(() => order.push('connected'))
    // After the health probe, so the exchange is the call in flight.
    while (harness.tokens.calls.length === 0) {
      await new Promise(settle => setTimeout(settle, 5))
    }
    const closing = memco.close().then(() => order.push('closed'))
    await Promise.all([connecting, closing])
    assert.deepEqual(order, ['connected', 'closed'])
  })
})

test('a call waits for an exchange no longer than its own deadline', async () => {
  await withHarness(async harness => {
    harness.tokens.delays.set('issueToken', 1000)
    const memco = credentialed(harness)
    try {
      const started = Date.now()
      await assert.rejects(
        memco.networks.list({ timeout: 0.05 }),
        MemcoTimeoutError
      )
      assert.ok(Date.now() - started < 500, 'the call outwaited its deadline')
      // The exchange it gave up on still lands, and serves the next call.
      await memco.networks.list()
      assert.deepEqual(harness.tokens.calls, ['issueToken'])
      assert.deepEqual(harness.admin.authorization, [['Bearer client-token-1']])
    } finally {
      await memco.close()
    }
  })
})

// --- one deadline for the whole call ------------------------------------

/**
 * How long a call took to fail with a timeout.
 *
 * @param call The call to make.
 * @returns The milliseconds from the call to its `MemcoTimeoutError`.
 */
async function timedOut(call: () => Promise<unknown>): Promise<number> {
  const started = Date.now()
  await assert.rejects(call(), MemcoTimeoutError)
  return Date.now() - started
}

for (const [how, perCall] of [
  ["the call's own timeout", true],
  ['the client default', false]
] as const) {
  // One deadline per call, fixed as it starts, covers waiting for a credential
  // and the RPC together: the RPC has whatever the wait left of it.
  const options = perCall ? { timeout: 0.4 } : {}
  const client = (harness: Harness): Memco =>
    credentialed(harness, perCall ? {} : { timeout: 0.4 })

  test(`a token that never arrives times the call out at ${how}`, async () => {
    await withHarness(async harness => {
      const hold = new Hold()
      harness.tokens.holds.set('issueToken', hold)
      const memco = client(harness)
      try {
        const took = await timedOut(() => memco.networks.list(options))
        assert.ok(took >= 350 && took < 600, `timed out after ${took}ms`)
        assert.deepEqual(harness.admin.calls, [])
      } finally {
        hold.release()
        await memco.close()
      }
    })
  })

  test(`a slow token leaves the RPC only what remains of ${how}`, async () => {
    await withHarness(async harness => {
      harness.tokens.delays.set('issueToken', 200)
      const hold = new Hold()
      harness.admin.holds.set('listNetworks', hold)
      const memco = client(harness)
      try {
        // 200ms waiting for the token, then 200ms for the RPC: a fresh budget
        // for the RPC would make this take 600ms.
        const took = await timedOut(() => memco.networks.list(options))
        assert.ok(took >= 350 && took < 550, `timed out after ${took}ms`)
        assert.deepEqual(harness.admin.calls, ['listNetworks'])
      } finally {
        hold.release()
        await memco.close()
      }
    })
  })

  test(`with a valid token the RPC has the whole of ${how}`, async () => {
    await withHarness(async harness => {
      const memco = client(harness)
      try {
        await memco.connect()
        // Longer than half the budget, which it has all of.
        harness.admin.delays.set('listNetworks', 300)
        await memco.networks.list(options)
        assert.deepEqual(harness.tokens.calls, ['issueToken'])
      } finally {
        await memco.close()
      }
    })
  })
}

// --- a rejected credential -----------------------------------------------

test('a rejected credential is typed and logged', async () => {
  await withHarness(async harness => {
    harness.tokens.error = {
      code: status.UNAUTHENTICATED,
      details: 'invalid client credentials'
    }
    const memco = credentialed(harness)
    try {
      setLevel(DEFAULT_LEVEL)
      const written = await stderrOf(async () => {
        await assert.rejects(memco.connect(), MemcoAuthenticationError)
      })
      assert.ok(
        written.includes(`ERROR credential rejected by ${harness.address}`),
        written
      )
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('the secret never reaches a log, an error or a rendering of the client', async () => {
  await withHarness(async harness => {
    harness.tokens.error = {
      code: status.UNAUTHENTICATED,
      details: 'invalid client credentials'
    }
    const memco = credentialed(harness)
    let caught: unknown
    try {
      setLevel('debug')
      const written = await stderrOf(async () => {
        await memco.connect().catch((error: unknown) => {
          caught = error
        })
      })
      assert.ok(caught instanceof MemcoAuthenticationError)
      for (const rendered of [
        written,
        inspect(caught, { showHidden: true, depth: null }),
        JSON.stringify(caught),
        String(caught),
        (caught as Error).stack ?? '',
        inspect(memco, { showHidden: true, depth: null }),
        JSON.stringify(memco)
      ]) {
        assert.ok(!rendered.includes('supersecret'), rendered)
      }
    } finally {
      setLevel(DEFAULT_LEVEL)
      await memco.close()
    }
  })
})

test('an issued token never reaches a rendering of the client', async () => {
  await withHarness(async harness => {
    const memco = credentialed(harness)
    try {
      await memco.connect()
      for (const rendered of [
        inspect(memco, { showHidden: true, depth: null }),
        JSON.stringify(memco)
      ]) {
        assert.ok(!rendered.includes('client-token-1'), rendered)
        assert.ok(!rendered.includes('supersecret'), rendered)
      }
    } finally {
      await memco.close()
    }
  })
})

// --- which credential the environment gives ------------------------------

test('client credentials in the environment win over a token there', async () => {
  await withHarness(async harness => {
    const memco = new Memco({
      host: harness.address,
      tls: false,
      env: {
        MEMCO_CLIENT_ID: 'client-env',
        MEMCO_CLIENT_SECRET: 'secret-env',
        MEMCO_API_TOKEN: 'token-env'
      }
    })
    try {
      await memco.connect()
      assert.equal(issued(harness).clientId, 'client-env')
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('a token argument ignores client credentials in the environment', async () => {
  await withHarness(async harness => {
    const memco = new Memco({
      token: 'token-arg',
      host: harness.address,
      tls: false,
      env: { MEMCO_CLIENT_ID: 'client-env', MEMCO_CLIENT_SECRET: 'secret-env' }
    })
    try {
      await memco.connect()
      assert.deepEqual(harness.tokens.calls, [])
      assert.deepEqual(harness.memory.authorization, [['Bearer token-arg']])
    } finally {
      await memco.close()
    }
  })
})
