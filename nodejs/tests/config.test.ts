/**
 * Credential, host and port resolution, and the deadline one call runs under.
 *
 * The port half of this is where a mistake is quietest: a wrong split dials an
 * address nobody named and surfaces as a name-resolution failure rather than as
 * the configuration error it is. So every message is pinned, not just the fact
 * that something was raised.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { inspect } from 'node:util'

import { MemcoConfigError } from '../src/errors.js'
import {
  DEFAULT_HOST,
  DEFAULT_PORT,
  DEFAULT_TIMEOUT,
  deadline,
  resolve
} from '../src/internal/config.js'

/** One captured `process.emitWarning` call: its text and the type it was given. */
interface Warning {
  /** The message as it would have been printed. */
  message: string
  /** The warning type, which is what a `process.on('warning')` handler filters on. */
  type: string | undefined
}

/** Run `body` with `process.emitWarning` captured rather than printed. */
function warnings(body: () => void): Warning[] {
  const seen: Warning[] = []
  const original = process.emitWarning
  process.emitWarning = ((message: string | Error, type?: unknown): void => {
    seen.push({
      message: typeof message === 'string' ? message : message.message,
      type: typeof type === 'string' ? type : undefined
    })
  }) as typeof process.emitWarning
  try {
    body()
  } finally {
    process.emitWarning = original
  }
  return seen
}

// --- the credential ------------------------------------------------------

test('an explicit token wins over the environment', () => {
  const config = resolve({
    token: 'explicit',
    env: { MEMCO_API_TOKEN: 'from-env' }
  })
  assert.equal(config.token, 'explicit')
})

test('a token is taken from MEMCO_API_TOKEN when none is passed', () => {
  assert.equal(resolve({ env: { MEMCO_API_TOKEN: '  t  ' } }).token, 't')
})

test('the deprecated MEMCO_API_KEY still supplies the token, warning once', () => {
  // Once per process, because that is what a caller can act on: a warning per
  // client construction is noise they cannot switch off.
  //
  // The counter this asserts on is module state with no reset, so this must
  // stay the first test in this file that resolves from MEMCO_API_KEY.
  const emitted = warnings(() => {
    assert.equal(resolve({ env: { MEMCO_API_KEY: 'legacy' } }).token, 'legacy')
    assert.equal(resolve({ env: { MEMCO_API_KEY: 'legacy' } }).token, 'legacy')
  })
  assert.equal(emitted.length, 1)
  assert.match(emitted[0].message, /MEMCO_API_KEY is deprecated/)
  assert.match(emitted[0].message, /rename it to MEMCO_API_TOKEN/)
  // The type is what a `process.on('warning')` handler filters on, and what
  // makes `--no-deprecation` silence this and nothing else.
  assert.equal(emitted[0].type, 'DeprecationWarning')
})

test('MEMCO_API_TOKEN takes precedence over the deprecated name', () => {
  const emitted = warnings(() => {
    const config = resolve({
      env: { MEMCO_API_TOKEN: 'new', MEMCO_API_KEY: 'old' }
    })
    assert.equal(config.token, 'new')
  })
  assert.deepEqual(emitted, [])
})

test('a missing token is refused before anything is dialled', () => {
  assert.throws(() => resolve({ env: {} }), {
    name: 'MemcoConfigError',
    message: 'no API token: pass token=... or set MEMCO_API_TOKEN'
  })
})

test('a token that cannot be sent as a header is refused, without quoting it', () => {
  // grpc-js rejects a metadata value outside printable ASCII by throwing an
  // Error that quotes the value, so a token carrying a stray newline or a
  // zero-width space would otherwise put the whole credential into a message
  // the application logs. Refused here instead, and the message names the
  // position rather than the character.
  for (const token of [
    'sk-live-supersecret​tail',
    'sk-live-supersecret\ntail',
    'sk-live-supersecrét'
  ]) {
    assert.throws(
      () => resolve({ token, env: {} }),
      (error: unknown) => {
        assert.ok(error instanceof MemcoConfigError)
        assert.match(error.message, /must be printable ASCII/)
        assert.ok(!error.message.includes('sk-live'), error.message)
        assert.ok(!error.message.includes('supersecret'), error.message)
        return true
      }
    )
  }
  // A real token is unaffected: every character base64 and JWT use is in range.
  assert.ok(resolve({ token: 'sk-live_AB.cd-09+/=~', env: {} }).token)
})

test('a blank token argument is a caller bug, not a request for the environment', () => {
  // Falling through to MEMCO_API_TOKEN would authenticate as somebody the
  // caller did not name, which is worse than the error.
  assert.throws(
    () => resolve({ token: '   ', env: { MEMCO_API_TOKEN: 't' } }),
    {
      name: 'MemcoConfigError',
      message:
        'the token passed to the client is blank: pass a real token or set MEMCO_API_TOKEN'
    }
  )
})

// --- the endpoint --------------------------------------------------------

test('the endpoint defaults to the production host and port', () => {
  const config = resolve({ token: 't', env: {} })
  assert.equal(config.host, DEFAULT_HOST)
  assert.equal(config.port, DEFAULT_PORT)
  assert.equal(config.target, `${DEFAULT_HOST}:${DEFAULT_PORT}`)
})

test('the host is taken from MEMCO_API_HOST, and an argument beats it', () => {
  assert.equal(
    resolve({ token: 't', env: { MEMCO_API_HOST: 'example.test' } }).host,
    'example.test'
  )
  const config = resolve({
    token: 't',
    host: 'arg.test',
    env: { MEMCO_API_HOST: 'env.test' }
  })
  assert.equal(config.host, 'arg.test')
})

test('a blank MEMCO_API_HOST falls through to the default', () => {
  // An unset CI variable interpolated into a compose file arrives as "" or as
  // whitespace; treating that as an endpoint would dial nothing at all.
  for (const value of ['', '   ']) {
    assert.equal(
      resolve({ token: 't', env: { MEMCO_API_HOST: value } }).host,
      DEFAULT_HOST
    )
  }
})

test('the environment defaults to the real one', () => {
  // Every other test passes `env` explicitly, so without this the default is
  // never exercised and could be changed to `{}` with the suite still green.
  //
  // The client pair is set aside for the duration: exported in a developer's
  // shell, it would win over the token this test sets.
  const names = [
    'MEMCO_API_TOKEN',
    'MEMCO_API_HOST',
    'MEMCO_CLIENT_ID',
    'MEMCO_CLIENT_SECRET'
  ]
  const previous = names.map(name => [name, process.env[name]] as const)
  process.env['MEMCO_API_TOKEN'] = 'from-the-real-environment'
  process.env['MEMCO_API_HOST'] = 'real.test:1234'
  delete process.env['MEMCO_CLIENT_ID']
  delete process.env['MEMCO_CLIENT_SECRET']
  try {
    const config = resolve()
    assert.equal(config.token, 'from-the-real-environment')
    assert.equal(config.target, 'real.test:1234')
  } finally {
    for (const [name, value] of previous) {
      if (value === undefined) {
        delete process.env[name]
      } else {
        process.env[name] = value
      }
    }
  }
})

test('a blank host argument is refused rather than treated as absent', () => {
  assert.throws(() => resolve({ token: 't', host: ' ', env: {} }), {
    name: 'MemcoConfigError',
    message:
      'the host passed to the client is blank: pass a real host or set MEMCO_API_HOST'
  })
})

test('a host may carry its own port', () => {
  const config = resolve({ token: 't', host: 'localhost:50051', env: {} })
  assert.equal(config.host, 'localhost')
  assert.equal(config.port, 50051)
  assert.equal(config.target, 'localhost:50051')
})

test('a bracketed IPv6 literal keeps its brackets only in the target', () => {
  const config = resolve({ token: 't', host: '[2001:db8::1]:50051', env: {} })
  assert.equal(config.host, '2001:db8::1')
  assert.equal(config.port, 50051)
  assert.equal(config.target, '[2001:db8::1]:50051')
})

test('a bracketed IPv6 literal without a port gets the default one', () => {
  const config = resolve({ token: 't', host: '[2001:db8::1]', env: {} })
  assert.equal(config.host, '2001:db8::1')
  assert.equal(config.port, DEFAULT_PORT)
})

test('a bare IPv6 literal is not split on its last colon', () => {
  // Splitting it would silently produce a wrong host and a wrong port rather
  // than an error, which is the failure mode this branch exists to prevent.
  const config = resolve({ token: 't', host: '2001:db8::1', env: {} })
  assert.equal(config.host, '2001:db8::1')
  assert.equal(config.port, DEFAULT_PORT)
  assert.equal(config.target, '[2001:db8::1]:443')
})

test('an unbalanced bracket is named as such', () => {
  assert.throws(() => resolve({ token: 't', host: '[2001:db8::1', env: {} }), {
    message: 'host "[2001:db8::1" opens a bracket that is never closed'
  })
})

test('empty brackets are named as such', () => {
  assert.throws(() => resolve({ token: 't', host: '[]:443', env: {} }), {
    message: 'host "[]:443" has no address inside its brackets'
  })
})

test('text after the closing bracket that is not a port is named as such', () => {
  assert.throws(() => resolve({ token: 't', host: '[::1]x', env: {} }), {
    message: 'host "[::1]x" has unexpected text after the closing bracket'
  })
})

test('a port with no hostname is refused', () => {
  // ':50051' is what `${process.env.MY_HOST ?? ''}:${port}` produces, and
  // dialling it surfaces as a retryable transport failure instead.
  assert.throws(() => resolve({ token: 't', host: ':50051', env: {} }), {
    message: 'host ":50051" has a port but no hostname'
  })
  // The whitespace form arrives the same way and must not survive the trim.
  assert.throws(() => resolve({ token: 't', host: ' :443', env: {} }), {
    message: 'host ":443" has a port but no hostname'
  })
})

test('an unparseable port is named with the text that could not be read', () => {
  assert.throws(
    () => resolve({ token: 't', host: 'localhost:not-a-port', env: {} }),
    {
      message:
        'host "localhost:not-a-port" has an unparseable port: "not-a-port" is not an integer'
    }
  )
})

test('a port outside the TCP range is named with the number', () => {
  assert.throws(
    () => resolve({ token: 't', host: 'localhost:99999', env: {} }),
    {
      message:
        'host "localhost:99999" has a port outside the range 1-65535: 99999'
    }
  )
  assert.throws(() => resolve({ token: 't', host: 'localhost:0', env: {} }), {
    message: 'host "localhost:0" has a port outside the range 1-65535: 0'
  })
  // Named back exactly as written. Parsing this through a double would report
  // 100000000000000000000, a number the caller never typed.
  assert.throws(
    () =>
      resolve({ token: 't', host: 'localhost:99999999999999999999', env: {} }),
    {
      message:
        'host "localhost:99999999999999999999" has a port outside the range 1-65535: 99999999999999999999'
    }
  )
})

// --- call defaults -------------------------------------------------------

test('TLS is on and the deadline is thirty seconds unless asked otherwise', () => {
  const config = resolve({ token: 't', env: {} })
  assert.equal(config.tls, true)
  assert.equal(config.timeout, DEFAULT_TIMEOUT)
  assert.equal(DEFAULT_TIMEOUT, 30)
})

test('a non-positive timeout is refused before the token is even looked for', () => {
  // Order matters: a caller who passed a bad timeout and no token should be
  // told about the timeout, which is the thing they actually got wrong.
  assert.throws(() => resolve({ timeout: 0, env: {} }), {
    name: 'MemcoConfigError',
    message: 'timeout must be positive, got 0'
  })
  assert.throws(() => resolve({ token: 't', timeout: -1, env: {} }), {
    message: 'timeout must be positive, got -1'
  })
})

test('a per-call deadline falls back to the client default', () => {
  assert.equal(deadline(undefined, 30), 30)
  assert.equal(deadline(5, 30), 5)
})

test('a per-call deadline that expires before it is sent is refused', () => {
  // Substituting the default would silently ignore what the caller asked for.
  assert.throws(
    () => deadline(0, 30),
    (error: unknown) => {
      assert.ok(error instanceof MemcoConfigError)
      assert.equal(error.message, 'timeout must be positive, got 0')
      return true
    }
  )
  assert.throws(() => deadline(-3, 30), {
    message: 'timeout must be positive, got -3'
  })
})

// --- the credential must not be renderable -------------------------------

test('the token is absent from every rendering of the config', () => {
  // Error trackers such as Sentry capture local variables by default, and this
  // object is live while the channel is being dialled.
  const config = resolve({
    token: 'sk-live-supersecret-9f2b',
    host: 'localhost:50051',
    env: {}
  })
  const renderings = [
    inspect(config),
    // showHidden is what would otherwise report a non-enumerable property, and
    // is what `console.dir(config, {showHidden: true})` in a debug session does.
    inspect(config, { showHidden: true, depth: null, getters: true }),
    JSON.stringify(config),
    // Nested, because that is the shape a crash report or a structured log
    // record puts it in, and toJSON has to survive being reached indirectly.
    JSON.stringify({ config }),
    String(config),
    inspect({ ...config }),
    JSON.stringify(structuredClone(config))
  ]
  for (const rendering of renderings) {
    assert.ok(
      !rendering.includes('sk-live'),
      `the token leaked into ${rendering}`
    )
    assert.ok(
      !rendering.includes('supersecret'),
      `a fragment of the token leaked into ${rendering}`
    )
  }
  // The rest of the settings are still legible, which is the point of hiding
  // only the one field.
  assert.match(inspect(config), /localhost/)
  assert.match(JSON.stringify(config), /"port":50051/)
  // And the token is still readable by the code that needs it.
  assert.equal(config.token, 'sk-live-supersecret-9f2b')
})

test('the token is not an enumerable property', () => {
  const config = resolve({ token: 'secret', env: {} })
  assert.ok(!Object.keys(config).includes('token'))
  assert.deepEqual(
    Object.entries(config)
      .map(([key]) => key)
      .sort(),
    ['clientId', 'host', 'port', 'timeout', 'tls', 'tokenLifetime']
  )
})

// --- TLS -----------------------------------------------------------------

for (const [value, expected] of [
  ['true', true],
  ['false', false],
  ['FALSE', false],
  [' False ', false],
  ['True', true]
] as const) {
  test(`MEMCO_API_TLS=${JSON.stringify(value)} reads as ${expected}`, () => {
    assert.equal(
      resolve({ token: 't', env: { MEMCO_API_TLS: value } }).tls,
      expected
    )
  })
}

test('a blank MEMCO_API_TLS keeps TLS on', () => {
  for (const value of ['', '   ']) {
    assert.equal(
      resolve({ token: 't', env: { MEMCO_API_TLS: value } }).tls,
      true
    )
  }
})

test('an explicit tls wins over MEMCO_API_TLS', () => {
  for (const explicit of [true, false]) {
    assert.equal(
      resolve({
        token: 't',
        tls: explicit,
        env: { MEMCO_API_TLS: String(!explicit) }
      }).tls,
      explicit
    )
  }
})

test('an unreadable MEMCO_API_TLS is refused rather than guessed at', () => {
  // Whether traffic is encrypted is not something to infer from a typo.
  for (const value of ['0', 'no', 'off', 'flase', 'yes']) {
    assert.throws(
      () => resolve({ token: 't', env: { MEMCO_API_TLS: value } }),
      {
        name: 'MemcoConfigError',
        message: `MEMCO_API_TLS must be true or false, got ${JSON.stringify(value)}`
      }
    )
  }
})

// --- client credentials --------------------------------------------------

test('client credentials are taken from the arguments', () => {
  const config = resolve({
    clientId: ' id ',
    clientSecret: ' secret ',
    env: {}
  })
  assert.equal(config.clientId, 'id')
  assert.equal(config.clientSecret, 'secret')
  // The token is issued by the service, not configured.
  assert.equal(config.token, '')
  assert.equal(config.tokenLifetime, null)
})

test('client credential arguments win over the environment', () => {
  const config = resolve({
    clientId: 'arg-id',
    clientSecret: 'arg-secret',
    env: {
      MEMCO_CLIENT_ID: 'env-id',
      MEMCO_CLIENT_SECRET: 'env-secret',
      MEMCO_API_TOKEN: 'env-token'
    }
  })
  assert.deepEqual(
    [config.clientId, config.clientSecret],
    ['arg-id', 'arg-secret']
  )
})

test('client credentials in the environment win over a token there', () => {
  // CI exports both: the token for the memory suite, the pair for the
  // administration one.
  const config = resolve({
    env: {
      MEMCO_CLIENT_ID: 'env-id',
      MEMCO_CLIENT_SECRET: 'env-secret',
      MEMCO_API_TOKEN: 'env-token'
    }
  })
  assert.deepEqual(
    [config.clientId, config.clientSecret, config.token],
    ['env-id', 'env-secret', '']
  )
})

test('a token argument ignores client credentials in the environment', () => {
  for (const env of [
    { MEMCO_CLIENT_ID: 'env-id', MEMCO_CLIENT_SECRET: 'env-secret' },
    // Half a pair too: the caller named their credential, so the environment's
    // pair is not consulted at all.
    { MEMCO_CLIENT_ID: 'env-id' }
  ]) {
    const config = resolve({ token: 'arg-token', env })
    assert.deepEqual([config.token, config.clientId], ['arg-token', null])
  }
})

test('client credentials skip the deprecated token variable and its warning', () => {
  const emitted = warnings(() => {
    const config = resolve({
      env: {
        MEMCO_CLIENT_ID: 'env-id',
        MEMCO_CLIENT_SECRET: 'env-secret',
        MEMCO_API_KEY: 'legacy'
      }
    })
    assert.equal(config.clientId, 'env-id')
  })
  assert.deepEqual(emitted, [])
})

test('a blank pair in the environment reads as unset', () => {
  // It is what an unset CI secret expands to.
  const config = resolve({
    env: {
      MEMCO_CLIENT_ID: '  ',
      MEMCO_CLIENT_SECRET: '',
      MEMCO_API_TOKEN: 'env-token'
    }
  })
  assert.deepEqual([config.token, config.clientId], ['env-token', null])
})

test('a token argument beside client arguments is refused', () => {
  // Two credentials of different kinds: sending either would be a guess.
  for (const given of [
    { clientId: 'id', clientSecret: 'secret' },
    { clientId: 'id' },
    { clientSecret: 'secret' }
  ]) {
    assert.throws(() => resolve({ token: 't', ...given, env: {} }), {
      name: 'MemcoConfigError',
      message: 'pass either a token or a clientId with a clientSecret, not both'
    })
  }
})

test('half a pair of arguments is refused, naming the half that is missing', () => {
  assert.throws(() => resolve({ clientId: 'id', env: {} }), {
    name: 'MemcoConfigError',
    message:
      'client credentials need both clientId and clientSecret; clientSecret is missing'
  })
  assert.throws(
    () =>
      resolve({
        clientSecret: 'secret',
        env: { MEMCO_CLIENT_ID: 'env-id' }
      }),
    {
      message:
        'client credentials need both clientId and clientSecret; clientId is missing'
    }
  )
})

test('half a pair in the environment is refused, even beside a token', () => {
  // A deployment missing one of its two secrets must fail loudly rather than
  // quietly run as something else.
  assert.throws(
    () =>
      resolve({
        env: { MEMCO_CLIENT_ID: 'env-id', MEMCO_API_TOKEN: 'env-token' }
      }),
    {
      name: 'MemcoConfigError',
      message:
        'MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET must be set together; MEMCO_CLIENT_SECRET is not'
    }
  )
  assert.throws(() => resolve({ env: { MEMCO_CLIENT_SECRET: 'env-secret' } }), {
    message:
      'MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET must be set together; MEMCO_CLIENT_ID is not'
  })
})

test('a blank client credential argument is refused', () => {
  assert.throws(
    () => resolve({ clientId: ' ', clientSecret: 'secret', env: {} }),
    { message: 'the clientId passed to the client is blank' }
  )
  assert.throws(() => resolve({ clientId: 'id', clientSecret: '', env: {} }), {
    message: 'the clientSecret passed to the client is blank'
  })
})

test('a client credential that is not a string is refused', () => {
  // A JavaScript caller has no compiler to stop them.
  assert.throws(
    () =>
      resolve({
        clientId: 42 as unknown as string,
        clientSecret: 'secret',
        env: {}
      }),
    {
      name: 'MemcoConfigError',
      message: 'the clientId passed to the client must be a string, got number'
    }
  )
})

test('tokenLifetime needs client credentials', () => {
  // Only an issued token has a lifetime to ask for; accepting one beside a
  // static token would silently ignore it.
  for (const options of [
    { token: 't', env: {} },
    { env: { MEMCO_API_TOKEN: 'env-token' } }
  ]) {
    assert.throws(() => resolve({ ...options, tokenLifetime: 600 }), {
      name: 'MemcoConfigError',
      message: 'tokenLifetime applies only to client credentials, not a token'
    })
  }
})

test('tokenLifetime must be a positive whole number of seconds the wire can carry', () => {
  for (const lifetime of [
    0,
    -60,
    1.5,
    Number.NaN,
    Number.POSITIVE_INFINITY,
    2 ** 31
  ]) {
    assert.throws(
      () =>
        resolve({
          clientId: 'id',
          clientSecret: 'secret',
          tokenLifetime: lifetime,
          env: {}
        }),
      {
        name: 'MemcoConfigError',
        message: `tokenLifetime must be a whole number of seconds from 1 to 2147483647, got ${lifetime}`
      }
    )
  }
})

test('tokenLifetime has no maximum of its own: the service owns that', () => {
  const config = resolve({
    clientId: 'id',
    clientSecret: 'secret',
    tokenLifetime: 7 * 86400,
    env: {}
  })
  assert.equal(config.tokenLifetime, 7 * 86400)
})

test('tokenLifetime applies to client credentials from the environment', () => {
  const config = resolve({
    tokenLifetime: 600,
    env: { MEMCO_CLIENT_ID: 'env-id', MEMCO_CLIENT_SECRET: 'env-secret' }
  })
  assert.equal(config.tokenLifetime, 600)
})

test('the client secret is absent from every rendering of the config', () => {
  const config = resolve({
    clientId: 'client-a',
    clientSecret: 'cs-live-supersecret-9f2b',
    host: 'localhost:50051',
    env: {}
  })
  for (const rendering of [
    inspect(config),
    inspect(config, { showHidden: true, depth: null, getters: true }),
    JSON.stringify(config),
    JSON.stringify({ config }),
    String(config),
    inspect({ ...config }),
    JSON.stringify(structuredClone(config))
  ]) {
    assert.ok(
      !rendering.includes('supersecret'),
      `the secret leaked into ${rendering}`
    )
  }
  // The id names the client and revokes nothing, so it stays legible.
  assert.match(inspect(config), /client-a/)
  assert.equal(config.clientSecret, 'cs-live-supersecret-9f2b')
})
