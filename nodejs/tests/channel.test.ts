/**
 * Channel construction, and the retry policy it carries.
 *
 * Two kinds of assertion live here. The options are inspected directly, because
 * the TLS branch is the production default and no fake server can exercise it —
 * losing an option there would otherwise be shipped rather than caught. The
 * retries are counted on the wire instead, because a policy that reads correct
 * and never takes effect is exactly the defect that inspection cannot see.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { ChannelCredentials, status, type ServiceError } from '@grpc/grpc-js'

import { MemcoUnavailableError, fromServiceError } from '../src/errors.js'
import {
  CHANNEL_OPTIONS,
  HEALTH_SERVICE,
  MEMORY_SERVICE,
  RETRYABLE_METHODS,
  USER_AGENT,
  buildTransport
} from '../src/internal/channel.js'
import { resolve } from '../src/internal/config.js'
import * as pb from '../src/internal/gen.js'
import {
  HealthService,
  type HealthCheckResponse
} from '../src/internal/wire.js'
import { withHarness } from './fakeServer.js'

const BLIP = { code: status.UNAVAILABLE, details: 'try again' }

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

// --- what every channel announces ---------------------------------------

test('the user agent is a single space-free product token', () => {
  // The service matches deprecation rules against this token, so a second term
  // or a changed shape could break that match, and a client sending nothing
  // recognisable can never be told its build is out of date.
  assert.ok(USER_AGENT.startsWith('memco-node/'))
  assert.ok(!USER_AGENT.includes(' '))
  assert.match(USER_AGENT, /^memco-node\/\d+\.\d+\.\d+/)
})

test('the channel is given exactly the five options the SDK sets', () => {
  // Nothing here is incidental, and nothing absent is an oversight: no
  // keepalive and no message-size caps are set, and pinning the whole list is
  // what makes an addition argue for itself here rather than arrive unnoticed.
  assert.deepEqual(Object.keys(CHANNEL_OPTIONS).sort(), [
    'grpc.enable_retries',
    'grpc.initial_reconnect_backoff_ms',
    'grpc.max_reconnect_backoff_ms',
    'grpc.primary_user_agent',
    'grpc.service_config'
  ])
  assert.equal(CHANNEL_OPTIONS['grpc.primary_user_agent'], USER_AGENT)
  assert.equal(CHANNEL_OPTIONS['grpc.enable_retries'], 1)
  assert.equal(CHANNEL_OPTIONS['grpc.initial_reconnect_backoff_ms'], 200)
  assert.equal(CHANNEL_OPTIONS['grpc.max_reconnect_backoff_ms'], 5000)
})

test('the policy is set as service_config, not as default_service_config', () => {
  // `default_service_config` is the option that reads as correct — it yields to
  // a policy the service publishes — and it is not the one that takes effect.
  // A policy set that way silently never retries; the counting tests below are
  // what would fail if this were swapped back.
  assert.ok(!('grpc.default_service_config' in CHANNEL_OPTIONS))
  assert.equal(typeof CHANNEL_OPTIONS['grpc.service_config'], 'string')
})

// --- what the policy covers ---------------------------------------------

test('the retry policy names only the calls a replay cannot change', () => {
  const config = JSON.parse(CHANNEL_OPTIONS['grpc.service_config'] as string)
  assert.equal(config.methodConfig.length, 1)
  const entry = config.methodConfig[0]
  assert.deepEqual(entry.name, [
    { service: MEMORY_SERVICE, method: 'ListDomains' },
    { service: MEMORY_SERVICE, method: 'GetMemory' },
    { service: HEALTH_SERVICE, method: 'Check' }
  ])
  assert.deepEqual(entry.retryPolicy, {
    maxAttempts: 3,
    initialBackoff: '0.1s',
    maxBackoff: '1s',
    backoffMultiplier: 2,
    // UNAVAILABLE only: DEADLINE_EXCEEDED is the caller's own deadline, and
    // RESOURCE_EXHAUSTED is a limit the caller handles by kind.
    retryableStatusCodes: ['UNAVAILABLE']
  })
  assert.deepEqual([...RETRYABLE_METHODS], ['ListDomains', 'GetMemory'])
})

test('every retryable method is one the generated client declares', () => {
  // The policy matches on wire method names, so a renamed RPC leaves a stale
  // entry that raises nothing and simply stops retrying.
  const declared = new Set(
    Object.values(pb.MemoryServiceService).map(method =>
      method.path.slice(method.path.lastIndexOf('/') + 1)
    )
  )
  for (const method of RETRYABLE_METHODS) {
    assert.ok(declared.has(method), `${method} is not a method of the service`)
  }
})

test('the health entry names the service the health client actually calls', () => {
  // The service name in the policy and the path in the codec are written in two
  // places; this is what stops them drifting into a policy that matches nothing.
  assert.equal(HealthService.check.path, `/${HEALTH_SERVICE}/Check`)
  assert.equal(pb.MemoryServiceClient.serviceName, MEMORY_SERVICE)
})

// --- what the policy does -----------------------------------------------

test('a read survives one blip', async () => {
  await withHarness(async harness => {
    harness.memory.transientErrors.set('getMemory', [BLIP])
    const transport = buildTransport(
      resolve({ token: 't', host: harness.address, tls: false, env: {} })
    )
    try {
      const response = await unary<pb.GetMemoryResponse>(callback => {
        transport.memory.getMemory({ idx: 'memory-a-1' }, callback)
      })
      assert.equal(response.memory?.idx, 'memory-a-1')
    } finally {
      transport.close()
    }
    // Counted on the wire: two attempts is the policy taking effect.
    assert.deepEqual(harness.memory.calls, ['getMemory', 'getMemory'])
  })
})

test('searching is not retried', async () => {
  // A search with no session id opens one, so replaying it can orphan a session
  // the caller never learns the id of; replaying a scoped one can come back as
  // bare references carrying no insights, which reads as "no results".
  await withHarness(async harness => {
    harness.memory.transientErrors.set('search', [BLIP])
    const transport = buildTransport(
      resolve({ token: 't', host: harness.address, tls: false, env: {} })
    )
    let raised: unknown
    try {
      await unary<pb.SearchResponse>(callback => {
        transport.memory.search(
          pb.SearchRequest.fromPartial({
            query: 'how does X work',
            domain: 'coding'
          }),
          callback
        )
      })
    } catch (error) {
      raised = error
    } finally {
      transport.close()
    }
    assert.ok(raised, 'the blip should have reached the caller')
    assert.ok(
      fromServiceError(raised as Partial<ServiceError>) instanceof
        MemcoUnavailableError
    )
    assert.deepEqual(harness.memory.calls, ['search'])
  })
})

test('a write is not retried', async () => {
  // The whole point: one attempt, so a committed write cannot be duplicated.
  await withHarness(async harness => {
    harness.memory.transientErrors.set('createMemory', [BLIP])
    const transport = buildTransport(
      resolve({ token: 't', host: harness.address, tls: false, env: {} })
    )
    try {
      await assert.rejects(
        unary<pb.CreateMemoryResponse>(callback => {
          transport.memory.createMemory(
            pb.CreateMemoryRequest.fromPartial({
              query: 'q',
              title: 't',
              content: 'c',
              domain: 'coding'
            }),
            callback
          )
        })
      )
    } finally {
      transport.close()
    }
    assert.deepEqual(harness.memory.calls, ['createMemory'])
  })
})

test('a read gives up rather than retrying for ever', async () => {
  await withHarness(async harness => {
    harness.memory.transientErrors.set('getMemory', [BLIP, BLIP, BLIP, BLIP])
    const transport = buildTransport(
      resolve({ token: 't', host: harness.address, tls: false, env: {} })
    )
    try {
      await assert.rejects(
        unary<pb.GetMemoryResponse>(callback => {
          transport.memory.getMemory({ idx: 'memory-a-1' }, callback)
        })
      )
    } finally {
      transport.close()
    }
    // maxAttempts, exactly. A looser bound would also pass with retries off.
    assert.equal(harness.memory.calls.length, 3)
  })
})

test('the health probe is retried too', async () => {
  // Client construction gates on the probe, so without the health entry a blip
  // there is the one transient failure the SDK cannot absorb.
  await withHarness(async harness => {
    harness.health.transientErrors.push(BLIP)
    const transport = buildTransport(
      resolve({ token: 't', host: harness.address, tls: false, env: {} })
    )
    try {
      await unary<HealthCheckResponse>(callback => {
        transport.health.check({ service: '' }, {}, callback)
      })
    } finally {
      transport.close()
    }
    assert.deepEqual(harness.health.checkedServices, ['', ''])
  })
})

// --- the branch no fake server can reach --------------------------------

test('the TLS transport is built from the system trust store', () => {
  // The claim is "no arguments", which is what makes it the system trust store:
  // createSsl(roots) would pin somebody else's, and createFromSecureContext
  // could switch verification off. Neither shows up in the target or the
  // options, so the constructor call itself is what has to be recorded.
  const original = ChannelCredentials.createSsl
  const calls: unknown[][] = []
  ChannelCredentials.createSsl = ((...args: unknown[]) => {
    calls.push(args)
    return original.call(ChannelCredentials)
  }) as typeof ChannelCredentials.createSsl
  try {
    const transport = buildTransport(
      resolve({ token: 't', host: 'localhost:50051', tls: true, env: {} })
    )
    transport.close()
  } finally {
    ChannelCredentials.createSsl = original
  }
  assert.deepEqual(calls, [[]], 'createSsl was not called with no arguments')
})

test('plaintext is reached only by an explicit tls false', () => {
  const original = ChannelCredentials.createInsecure
  let insecure = 0
  ChannelCredentials.createInsecure = (() => {
    insecure += 1
    return original.call(ChannelCredentials)
  }) as typeof ChannelCredentials.createInsecure
  try {
    for (const config of [
      resolve({ token: 't', host: 'localhost:50051', env: {} }),
      resolve({ token: 't', host: 'localhost:50051', tls: true, env: {} })
    ]) {
      buildTransport(config).close()
    }
    assert.equal(insecure, 0, 'TLS is the default and must stay it')
    buildTransport(
      resolve({ token: 't', host: 'localhost:50051', tls: false, env: {} })
    ).close()
    assert.equal(insecure, 1)
  } finally {
    ChannelCredentials.createInsecure = original
  }
})

test('the user agent reaches the server on a real call', () => {
  // CHANNEL_OPTIONS holding the token proves nothing about the channel actually
  // being given those options. The server is what settles it: the harness can
  // read the header the service matches on.
  return withHarness(async harness => {
    const transport = buildTransport(
      resolve({ token: 't', host: harness.address, tls: false, env: {} })
    )
    try {
      await unary<pb.ListDomainsResponse>(callback => {
        transport.memory.listDomains({}, callback)
      })
    } finally {
      transport.close()
    }
    const sent = String(harness.memory.metadata[0]['user-agent'])
    assert.ok(sent.startsWith(USER_AGENT), sent)
  })
})

test('the memory and health clients share one channel', () => {
  // Both stubs sit on one channel, so the health probe proves the connection
  // the memory calls then use rather than a sibling of it.
  const transport = buildTransport(
    resolve({ token: 't', host: 'localhost:50051', tls: false, env: {} })
  )
  try {
    assert.equal(transport.memory.getChannel(), transport.health.getChannel())
  } finally {
    transport.close()
  }
})
