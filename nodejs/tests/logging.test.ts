/**
 * The SDK's loggers, the level they run at, and the one thing they must never
 * write.
 *
 * Node has no standard library logger, so the module under test is the whole
 * implementation rather than a configuration of one: the level, the record
 * format, where the record goes, and when a bad setting warns instead of
 * throwing are all its own behaviour and all pinned here.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { MemcoConfigError } from '../src/errors.js'
import { buildTransport } from '../src/internal/channel.js'
import { DEFAULT_HOST, DEFAULT_PORT, resolve } from '../src/internal/config.js'
import * as pb from '../src/internal/gen.js'
import {
  DEFAULT_LEVEL,
  LOG_ENV,
  ROOT,
  configureLogging,
  getLogger,
  setLevel
} from '../src/internal/logging.js'
import type { HealthCheckResponse } from '../src/internal/wire.js'
import { withHarness } from './fakeServer.js'

/** Redirect the error stream into an array until `restore` is called. */
function stderrTap(): { chunks: string[]; restore: () => void } {
  const chunks: string[] = []
  const original = process.stderr.write
  process.stderr.write = ((chunk: string | Uint8Array): boolean => {
    chunks.push(
      typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8')
    )
    return true
  }) as typeof process.stderr.write
  return {
    chunks,
    restore: () => {
      process.stderr.write = original
    }
  }
}

/** Run `body` with everything written to the error stream collected instead. */
function capture(body: () => void): string {
  const tap = stderrTap()
  try {
    body()
  } finally {
    tap.restore()
  }
  return tap.chunks.join('')
}

/** {@link capture}, for work that finishes on a later turn of the loop. */
async function captureAsync(body: () => Promise<void>): Promise<string> {
  const tap = stderrTap()
  try {
    await body()
  } finally {
    tap.restore()
  }
  return tap.chunks.join('')
}

/** Run `body` with `process.emitWarning` captured rather than printed. */
function warnings(body: () => void): string[] {
  const seen: string[] = []
  const original = process.emitWarning
  process.emitWarning = ((message: string | Error): void => {
    seen.push(typeof message === 'string' ? message : message.message)
  }) as typeof process.emitWarning
  try {
    body()
  } finally {
    process.emitWarning = original
  }
  return seen
}

// --- the default ---------------------------------------------------------

test('the default is info, so a debug record is dropped and an info record is not', () => {
  configureLogging({})
  const log = getLogger(`${ROOT}.client`)
  const written = capture(() => {
    log.debug('this is below the default')
    log.info('this is at the default')
  })
  assert.ok(!written.includes('below the default'))
  assert.ok(written.includes('this is at the default'))
  assert.equal(DEFAULT_LEVEL, 'info')
})

test('a blank MEMCO_LOG is the same as an unset one', () => {
  configureLogging({ [LOG_ENV]: '   ' })
  const written = capture(() => {
    getLogger(`${ROOT}.client`).debug('still below the default')
  })
  assert.equal(written, '')
})

// --- MEMCO_LOG -----------------------------------------------------------

test('MEMCO_LOG overrides the default level', () => {
  configureLogging({ [LOG_ENV]: 'debug' })
  const written = capture(() => {
    getLogger(`${ROOT}.channel`).debug('dialling %s', 'example:443')
  })
  assert.ok(written.includes('dialling example:443'))

  configureLogging({ [LOG_ENV]: 'error' })
  const quiet = capture(() => {
    getLogger(`${ROOT}.channel`).warning('not loud enough')
    getLogger(`${ROOT}.channel`).error('loud enough')
  })
  assert.ok(!quiet.includes('not loud enough'))
  assert.ok(quiet.includes('loud enough'))
})

test('the MEMCO_LOG value tolerates case and surrounding whitespace', () => {
  configureLogging({ [LOG_ENV]: '  DeBuG \n' })
  const written = capture(() => {
    getLogger(`${ROOT}.config`).debug('read after trimming')
  })
  assert.ok(written.includes('read after trimming'))
})

test('none silences even a critical record', () => {
  // Spelled the same three ways every other level may be, since `none` is the
  // one a caller reaches for when the SDK is drowning their output.
  for (const setting of ['none', 'NONE', '  none  ']) {
    configureLogging({ [LOG_ENV]: setting })
    const written = capture(() => {
      getLogger(`${ROOT}.client`).critical('this must not appear')
    })
    assert.equal(written, '', setting)
  }
})

test('an unrecognised MEMCO_LOG warns and leaves the SDK at the default', () => {
  // A typo in an ambient debug knob must never break a program, and must never
  // leave the SDK louder or quieter than it would have been without it.
  let written = ''
  const emitted = warnings(() => {
    configureLogging({ [LOG_ENV]: 'verbose' })
    written = capture(() => {
      getLogger(`${ROOT}.client`).debug('below the default')
      getLogger(`${ROOT}.client`).info('at the default')
    })
  })
  assert.equal(emitted.length, 1)
  assert.equal(
    emitted[0],
    'MEMCO_LOG ignored: "verbose" is not a log level; use one of critical, debug, error, info, warning, or none to silence'
  )
  assert.ok(!written.includes('below the default'))
  assert.ok(written.includes('at the default'))
})

test('an alias for a level is not a level', () => {
  // warn, fatal, trace and off all read as levels somewhere else. Accepting
  // them here would make MEMCO_LOG mean something different per SDK.
  for (const alias of ['warn', 'fatal', 'trace', 'off']) {
    assert.throws(() => setLevel(alias), {
      name: 'MemcoConfigError',
      message: `${JSON.stringify(alias)} is not a log level; use one of critical, debug, error, info, warning, or none to silence`
    })
  }
})

// --- setLevel ------------------------------------------------------------

test('a level passed as an argument raises rather than warning', () => {
  // An argument is the caller's own code, so unlike a stray MEMCO_LOG this is
  // a bug worth raising for.
  configureLogging({})
  assert.throws(
    () => setLevel('loud'),
    (error: unknown) => {
      assert.ok(error instanceof MemcoConfigError)
      return true
    }
  )
  // And the level it was already at is untouched.
  const written = capture(() => {
    getLogger(`${ROOT}.client`).info('still at info')
  })
  assert.ok(written.includes('still at info'))
})

test('setLevel accepts the same spellings the environment does', () => {
  setLevel('  CRITICAL  ')
  const written = capture(() => {
    getLogger(`${ROOT}.client`).error('below critical')
    getLogger(`${ROOT}.client`).critical('at critical')
  })
  assert.ok(!written.includes('below critical'))
  assert.ok(written.includes('at critical'))
})

// --- the record ----------------------------------------------------------

test('a record names the time, the logger, the level and the message, once', () => {
  configureLogging({ [LOG_ENV]: 'debug' })
  const written = capture(() => {
    getLogger(`${ROOT}.channel`).debug('dialling %s', 'example:443')
  })
  assert.equal(written.split('\n').filter(line => line !== '').length, 1)
  assert.match(
    written,
    /^\S+ memco\.channel DEBUG dialling example:443\n$/,
    written
  )
})

test('a record goes to the error stream as it is now, not as it was at import', () => {
  // A process that daemonizes, redirects or reopens its error stream after
  // importing the SDK would otherwise have every record still going to the
  // original one, or to a closed descriptor.
  setLevel('debug')
  const descriptor = Object.getOwnPropertyDescriptor(process, 'stderr')
  assert.ok(descriptor)
  const chunks: string[] = []
  Object.defineProperty(process, 'stderr', {
    configurable: true,
    value: {
      write: (chunk: string): boolean => {
        chunks.push(chunk)
        return true
      }
    }
  })
  try {
    getLogger(`${ROOT}.client`).debug('after the redirect')
  } finally {
    Object.defineProperty(process, 'stderr', descriptor)
  }
  assert.ok(chunks.join('').includes('after the redirect'))
})

test('a failed write cannot break the call that logged', () => {
  // A daemonized process that closed fd 2 is exactly the case the late lookup
  // of `process.stderr` exists for, and an EBADF there must not turn a
  // `resolve()` into a throw.
  setLevel('debug')
  const descriptor = Object.getOwnPropertyDescriptor(process, 'stderr')
  assert.ok(descriptor)
  Object.defineProperty(process, 'stderr', {
    configurable: true,
    value: {
      write: (): boolean => {
        throw new Error('EBADF: bad file descriptor, write')
      }
    }
  })
  try {
    assert.doesNotThrow(() => {
      getLogger(`${ROOT}.client`).debug('into a closed descriptor')
    })
  } finally {
    Object.defineProperty(process, 'stderr', descriptor)
  }
})

test('a message is only formatted when the level lets it through', () => {
  // Formatting eagerly would make a silenced SDK cost as much as a loud one.
  configureLogging({ [LOG_ENV]: 'none' })
  let rendered = 0
  const expensive = {
    toString(): string {
      rendered += 1
      return 'expensive'
    }
  }
  capture(() => {
    getLogger(`${ROOT}.client`).critical('%s', expensive)
  })
  assert.equal(rendered, 0)
})

// --- what the records say, and what they must never say ------------------

test('a debug record names where the credential came from, never what it is', () => {
  setLevel('debug')
  const fromArgument = capture(() => {
    resolve({ token: 'sk-live-supersecret-9f2b', env: {} })
  })
  assert.ok(fromArgument.includes('credential taken from the token argument'))

  const fromToken = capture(() => {
    resolve({ env: { MEMCO_API_TOKEN: 'sk-live-supersecret-9f2b' } })
  })
  assert.ok(fromToken.includes('credential taken from MEMCO_API_TOKEN'))

  let fromLegacy = ''
  warnings(() => {
    fromLegacy = capture(() => {
      resolve({ env: { MEMCO_API_KEY: 'sk-live-supersecret-9f2b' } })
    })
  })
  assert.ok(fromLegacy.includes('credential taken from MEMCO_API_KEY'))

  for (const written of [fromArgument, fromToken, fromLegacy]) {
    assert.ok(!written.includes('sk-live'), written)
    assert.ok(!written.includes('supersecret'), written)
  }
})

test('a debug record names the endpoint and which setting chose it', () => {
  setLevel('debug')
  assert.ok(
    capture(() => {
      resolve({ token: 't', host: 'localhost:50051', tls: false, env: {} })
    }).includes(
      'endpoint localhost:50051 tls=false (host from the host argument)'
    )
  )
  assert.ok(
    capture(() => {
      resolve({ token: 't', env: { MEMCO_API_HOST: 'example.test' } })
    }).includes('endpoint example.test:443 tls=true (host from MEMCO_API_HOST)')
  )
  assert.ok(
    capture(() => {
      resolve({ token: 't', env: {} })
    }).includes(
      `endpoint ${DEFAULT_HOST}:${DEFAULT_PORT} tls=true (host from the default)`
    )
  )
})

test('no credential reaches a record, over a whole exchange at debug', () => {
  // The level that logs the most is the level a credential would leak at. This
  // runs a real exchange — resolve, dial, an authenticated call, the
  // unauthenticated probe, teardown — with everything written to stderr kept.
  const token = 'sk-live-supersecret-9f2b'
  return withHarness(async harness => {
    const written = await captureAsync(async () => {
      setLevel('debug')
      const transport = buildTransport(
        resolve({ token, host: harness.address, tls: false, env: {} })
      )
      try {
        await new Promise<pb.ListDomainsResponse>((settle, fail) => {
          transport.memory.listDomains({}, (error, response) => {
            if (error) {
              fail(error)
              return
            }
            settle(response)
          })
        })
        await new Promise<HealthCheckResponse>((settle, fail) => {
          transport.health.check({ service: '' }, {}, (error, response) => {
            if (error) {
              fail(error)
              return
            }
            settle(response)
          })
        })
      } finally {
        transport.close()
      }
    })
    // The exchange really was logged, so the absence below means something.
    assert.ok(written.includes('credential taken from the token argument'))
    assert.ok(written.includes(`endpoint ${harness.address} tls=false`))
    // And the server really did receive the credential, so it was in play.
    assert.equal(harness.memory.metadata[0]['authorization'], `Bearer ${token}`)

    assert.ok(!written.includes(token), written)
    assert.ok(!written.includes('supersecret'), written)
    assert.ok(!written.includes('Bearer'), written)
  })
})
