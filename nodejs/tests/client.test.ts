/**
 * The client's lifecycle: what it sends, when, and what it refuses.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { status } from '@grpc/grpc-js'

import { Memco } from '../src/client.js'
import {
  MemcoAuthenticationError,
  MemcoConfigError,
  MemcoInvalidRequestError,
  MemcoUnavailableError,
  MemcoUnhealthyError
} from '../src/errors.js'
import * as pb from '../src/internal/gen.js'
import { AUTH_HEADER } from '../src/internal/auth.js'
import { resetWarnings } from '../src/internal/deprecation.js'
import { DEFAULT_LEVEL, setLevel } from '../src/internal/logging.js'
import { ServingStatus } from '../src/internal/wire.js'
import { withHarness, type Harness } from './fakeServer.js'

function client(harness: Harness): Memco {
  return new Memco({ token: 'test-token', host: harness.address, tls: false })
}

test('constructing a client sends nothing', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      // Proven by absence: the constructor resolves settings and opens a
      // channel, and grpc-js dials nothing until a call is made.
      assert.deepEqual(harness.memory.calls, [])
      assert.deepEqual(harness.health.checkedServices, [])
    } finally {
      await memco.close()
    }
  })
})

test('connecting probes health and then lists domains', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      assert.deepEqual(harness.health.checkedServices, [''])
      assert.deepEqual(harness.memory.calls, ['listDomains'])
    } finally {
      await memco.close()
    }
  })
})

test('the credential is withheld from the health probe and sent to the service', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      assert.equal(harness.health.metadata[0]?.[AUTH_HEADER], undefined)
      assert.equal(
        harness.memory.metadata[0]?.[AUTH_HEADER],
        'Bearer test-token'
      )
    } finally {
      await memco.close()
    }
  })
})

test('an endpoint that is not serving is reported as unhealthy, naming the status', async () => {
  await withHarness(async harness => {
    harness.health.status = ServingStatus.NOT_SERVING
    const memco = client(harness)
    try {
      await assert.rejects(memco.connect(), (error: unknown) => {
        assert.ok(error instanceof MemcoUnhealthyError)
        assert.match(error.message, /reported health status NOT_SERVING/)
        return true
      })
      // The health probe failed, so nothing was ever sent to the service.
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('a rejected credential surfaces from connect, not from the constructor', async () => {
  await withHarness(async harness => {
    harness.memory.error = {
      code: status.UNAUTHENTICATED,
      details: 'invalid credential'
    }
    const memco = client(harness)
    try {
      // The health probe carries no credential, so it is `listDomains` that
      // proves the token — which is why the constructor cannot.
      await assert.rejects(memco.connect(), MemcoAuthenticationError)
      assert.deepEqual(harness.health.checkedServices, [''])
    } finally {
      await memco.close()
    }
  })
})

test('connect can be repeated after a failure', async () => {
  await withHarness(async harness => {
    harness.health.status = ServingStatus.NOT_SERVING
    const memco = client(harness)
    try {
      await assert.rejects(memco.connect(), MemcoUnhealthyError)
      harness.health.status = ServingStatus.SERVING
      // The channel is kept across the failure, so this is a second probe on
      // the same connection rather than a fresh dial.
      await memco.connect()
      assert.equal(harness.health.checkedServices.length, 2)
    } finally {
      await memco.close()
    }
  })
})

test('an unavailable health probe is retried, and a search is not', async () => {
  await withHarness(async harness => {
    // Both staged failures are served once. Check is in the retry policy and
    // Search is deliberately outside it, because a replayed search mints a
    // second session id and comes back with degraded bare references.
    harness.health.transientErrors.push({
      code: status.UNAVAILABLE,
      details: 'starting up'
    })
    harness.memory.transientErrors.set('search', [
      { code: status.UNAVAILABLE, details: 'starting up' }
    ])
    const memco = client(harness)
    try {
      await memco.connect()
      assert.equal(
        harness.health.checkedServices.length,
        2,
        'Check should have been retried'
      )
      await assert.rejects(
        memco.memory.search('anything', { domain: 'coding' })
      )
      assert.equal(
        harness.memory.calls.filter(name => name === 'search').length,
        1,
        'Search should not have been retried'
      )
    } finally {
      await memco.close()
    }
  })
})

test('closing is idempotent, and a call afterwards is refused before anything is sent', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    await memco.connect()
    harness.forget()
    await memco.close()
    await memco.close()
    await assert.rejects(memco.memory.listDomains(), MemcoConfigError)
    assert.deepEqual(harness.memory.calls, [])
  })
})

test('a client disposes itself at the end of an await-using block', async () => {
  await withHarness(async harness => {
    let disposed: Memco
    {
      await using memco = await client(harness).connect()
      disposed = memco
    }
    // Proven by the refusal below: close() ran on the way out of the block.
    await assert.rejects(disposed.memory.listDomains(), MemcoConfigError)
  })
})

test('the limits a listDomains reply reports are applied to later calls', async () => {
  await withHarness(async harness => {
    harness.memory.responses.set(
      'listDomains',
      pb.ListDomainsResponse.fromPartial({
        limits: { maxQueryCharacters: 10 },
        domains: [{ slug: 'coding', maxTagsPerQuery: 2 }]
      })
    )
    const memco = client(harness)
    try {
      await memco.connect()
      harness.forget()
      await assert.rejects(
        memco.memory.search('a query well past the reported limit', {
          domain: 'coding'
        }),
        (error: unknown) => {
          assert.ok(error instanceof MemcoInvalidRequestError)
          assert.match(error.detail, /exceeds the limit of 10/)
          return true
        }
      )
      // The whole point of learning the limits: the call never left the client.
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('a deprecation notice from the service is surfaced once', async t => {
  resetWarnings()
  t.after(resetWarnings)
  await withHarness(async harness => {
    harness.memory.responses.set(
      'listDomains',
      pb.ListDomainsResponse.fromPartial({
        deprecated: true,
        deprecationMessage: 'upgrade the SDK',
        sunsetDate: '2027-01-01'
      })
    )
    const warnings: string[] = []
    const onWarning = (warning: Error): void => {
      warnings.push(warning.message)
    }
    process.on('warning', onWarning)
    const memco = client(harness)
    try {
      await memco.connect()
      await memco.memory.listDomains()
      await new Promise(settle => setImmediate(settle))
      assert.deepEqual(warnings, [
        'upgrade the SDK (stops working on 2027-01-01)'
      ])
    } finally {
      process.off('warning', onWarning)
      await memco.close()
    }
  })
})

test('provenance reports the contract without reaching the service', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      assert.match(memco.provenance().serverCommit, /^[0-9a-f]{40}$/)
      assert.deepEqual(harness.memory.calls, [])
    } finally {
      await memco.close()
    }
  })
})

test('closing waits for a call already in flight rather than cancelling it', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    await memco.connect()
    // Delayed well past the point close() is called, so the ordering below is
    // decided by the drain and not by a race.
    harness.memory.delays.set('search', 300)

    const order: string[] = []
    const pending = memco.memory
      .search('anything', { domain: 'coding' })
      .then(() => {
        order.push('call')
      })
    const closing = memco.close().then(() => {
      order.push('close')
    })

    await Promise.all([pending, closing])
    // Cancelling instead would surface at the call site as a failure
    // indistinguishable from the caller cancelling it themselves.
    assert.deepEqual(order, ['call', 'close'])
  })
})

test('a health probe that stays unavailable is reported once the retries are spent', async () => {
  await withHarness(async harness => {
    // Three, because the retry policy allows three attempts in total: the
    // failure only reaches the caller once the policy has given up.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      harness.health.transientErrors.push({
        code: status.UNAVAILABLE,
        details: 'still starting up'
      })
    }
    const memco = client(harness)
    try {
      await assert.rejects(memco.connect(), MemcoUnavailableError)
      assert.equal(harness.health.checkedServices.length, 3)
    } finally {
      await memco.close()
    }
  })
})

test('a log level the client does not recognise is refused, unlike a bad MEMCO_LOG', async () => {
  await withHarness(async harness => {
    // The environment variable warns and falls back, because a mistyped one
    // should not stop a program starting. This is the caller's own code, so it
    // throws.
    assert.throws(
      () =>
        new Memco({
          token: 'test-token',
          host: harness.address,
          tls: false,
          logLevel: 'verbose'
        }),
      MemcoConfigError
    )
  })
})

test('the log level a client is given governs what it writes', async t => {
  const written: string[] = []
  const original = process.stderr.write.bind(process.stderr)
  process.stderr.write = (chunk: unknown): boolean => {
    written.push(String(chunk))
    return true
  }
  t.after(() => {
    process.stderr.write = original
    setLevel(DEFAULT_LEVEL)
  })

  await withHarness(async harness => {
    const memco = new Memco({
      token: 'test-token',
      host: harness.address,
      tls: false,
      logLevel: 'none'
    })
    try {
      await memco.connect()
      // Asserted on length: assert.deepEqual is an assertion signature, so
      // comparing against a bare [] narrows `written` to never[].
      assert.equal(
        written.length,
        0,
        'none should silence even the connect record'
      )

      setLevel('debug')
      written.length = 0
      await memco.memory.listDomains()
      assert.ok(
        written.some(line =>
          line.includes('memco.client DEBUG ListDomains ok in')
        ),
        `expected a per-RPC debug record, got ${JSON.stringify(written)}`
      )
      // The record says where a credential came from, never what it was.
      assert.ok(!written.some(line => line.includes('test-token')))
    } finally {
      await memco.close()
    }
  })
})

test('connecting after close is refused with a typed error, not a raw channel error', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    await memco.connect()
    await memco.close()
    // The health probe does not go through `call`, and grpc-js throws
    // synchronously from a shut-down channel, so without an explicit guard the
    // raw "Channel has been shut down" would escape untyped.
    await assert.rejects(memco.connect(), (error: unknown) => {
      assert.ok(error instanceof MemcoConfigError)
      assert.match(error.message, /this client is closed/)
      return true
    })
  })
})

test('a second close does not report success while the first is still draining', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    await memco.connect()
    harness.memory.delays.set('search', 300)
    const call = memco.memory.search('anything', { domain: 'coding' })

    const order: string[] = []
    // `closed` is set before the drain begins, so short-circuiting on it would
    // let this second call resolve immediately — telling its caller the channel
    // was closed while it was still open and still serving a request.
    const first = memco.close()
    const second = memco.close()

    await Promise.all([
      call.then(() => order.push('call')),
      first.then(() => order.push('first')),
      second.then(() => order.push('second'))
    ])
    assert.equal(
      order[0],
      'call',
      `close resolved before the call: ${order.join(', ')}`
    )
  })
})

test('a timeout that is not a finite number is refused before it can reach grpc-js', async () => {
  await withHarness(async harness => {
    const memco = client(harness)
    try {
      await memco.connect()
      // NaN is what `Number(process.env.SOMETHING_UNSET)` produces. Passed
      // through, grpc-js throws it from a continuation this SDK does not own,
      // so it joins no promise chain, reaches no catch, and ends the process.
      for (const timeout of [Number.NaN, Number.POSITIVE_INFINITY]) {
        await assert.rejects(
          memco.memory.listDomains({ timeout }),
          (error: unknown) => {
            assert.ok(error instanceof MemcoConfigError)
            assert.match(error.message, /timeout must be a finite number/)
            return true
          }
        )
      }
    } finally {
      await memco.close()
    }
  })
})
