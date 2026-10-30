/**
 * The invariants the package layout rests on.
 *
 * Everything here fails loudly if a decision recorded in tsconfig, package.json
 * or the build scripts is quietly undone later.
 */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import {
  cpSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  symlinkSync,
  writeFileSync
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { pathToFileURL } from 'node:url'

import { ChannelCredentials, Metadata, status } from '@grpc/grpc-js'

import {
  MemcoAuthenticationError,
  MemcoInternalError,
  MemcoPreconditionFailedError,
  MemcoResourceExhaustedError,
  MemcoSunsetError,
  ResourceExhaustedKind,
  SunsetKind,
  fromServiceError
} from '../src/errors.js'
import { VERSION } from '../src/gen/version.js'
import { USER_AGENT } from '../src/internal/channel.js'
import * as pb from '../src/internal/gen.js'
import { parse, provenance } from '../src/internal/provenance.js'
import {
  STATUS_DETAILS_KEY,
  encodeStatusDetails,
  errorDetails
} from '../src/internal/wire.js'
import { withHarness } from './fakeServer.js'
import { packageRoot } from './packageFiles.js'

test('the packaged licence matches the repository root', () => {
  // The tarball needs its own copy — `files` cannot reach outside the package
  // root — so this is what stops the two drifting apart.
  const packaged = readFileSync(join(packageRoot, 'LICENSE'), 'utf8')
  const root = readFileSync(join(packageRoot, '..', 'LICENSE'), 'utf8')
  assert.equal(
    packaged,
    root,
    'nodejs/LICENSE has drifted from the repository LICENSE'
  )
})

test('the declared dependencies are the ones the export asks for', () => {
  // The repository's provenance check never compares this package's declared
  // dependencies against the descriptor, so until it does, this is the guard.
  const manifest = JSON.parse(
    readFileSync(join(packageRoot, 'package.json'), 'utf8')
  )
  const declared = JSON.parse(
    readFileSync(join(packageRoot, 'client', 'dependencies.json'), 'utf8')
  )
  assert.deepEqual(manifest.dependencies, declared.dependencies)
})

test('the compiled version is the one package.json declares', () => {
  const manifest = JSON.parse(
    readFileSync(join(packageRoot, 'package.json'), 'utf8')
  )
  assert.equal(VERSION, manifest.version)
  assert.equal(USER_AGENT, `memco-node/${manifest.version}`)
})

test('the compiled provenance is the descriptor the export wrote', () => {
  assert.deepEqual(
    provenance(),
    parse(
      readFileSync(join(packageRoot, 'client', 'SDK_PROVENANCE.yaml'), 'utf8')
    )
  )
})

test('nothing in the package asks where it is running or reads a file', () => {
  // A bundler moves this code and may empty `import.meta`, so either would
  // break an application that bundles the SDK.
  const source = join(packageRoot, 'src')
  const offenders = (readdirSync(source, { recursive: true }) as string[])
    .filter(name => /\.[cm]?[jt]s$/.test(name))
    .filter(name =>
      /['"](?:node:)?fs(?:\/promises)?['"]|import\.meta|__dirname|__filename/.test(
        readFileSync(join(source, name), 'utf8')
      )
    )
  assert.deepEqual(offenders, [])
})

test('the package loads and answers with none of its files beside it', () => {
  const root = mkdtempSync(join(tmpdir(), 'memco-bundled-'))
  try {
    const out = join(root, 'out', 'js')
    cpSync(join(packageRoot, 'build', 'js', 'src'), join(out, 'src'), {
      recursive: true
    })
    cpSync(join(packageRoot, 'build', 'js', 'client'), join(out, 'client'), {
      recursive: true
    })
    writeFileSync(join(out, 'package.json'), '{"type":"module"}\n')
    symlinkSync(join(packageRoot, 'node_modules'), join(root, 'node_modules'))

    const entry = pathToFileURL(join(out, 'src', 'index.js')).href
    const loaded = spawnSync(
      process.execPath,
      [
        '--input-type=module',
        '-e',
        `const sdk = await import(${JSON.stringify(entry)})
         console.log(sdk.provenance().serverCommit)`
      ],
      // Without the coverage variable: the copy's source maps name .ts files that
      // are not beside it, which would fail the coverage report of this run.
      { encoding: 'utf8', env: { ...process.env, NODE_V8_COVERAGE: undefined } }
    )
    assert.equal(loaded.status, 0, loaded.stderr)
    assert.equal(loaded.stdout.trim(), provenance().serverCommit)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('provenance reports the contract the generated client was built from', () => {
  const recorded = provenance()
  assert.match(recorded.serverCommit, /^[0-9a-f]{40}$/)
  assert.ok(
    recorded.protos.some(
      proto => proto.path === 'memcoai/memory/v1/memory.proto'
    )
  )
  assert.equal(
    provenance(),
    recorded,
    'the descriptor should be read once and reused'
  )
})

test('a descriptor missing its server commit is refused', () => {
  assert.throws(
    () => parse('protos:\n  - path: a\n    sha256: b\n'),
    /missing server_commit/
  )
})

test('a comment inside a quoted value is not treated as a comment', () => {
  const recorded = parse(
    'server_commit: "abc#123"\nprotos:\n  - path: a\n    sha256: b\n'
  )
  assert.equal(recorded.serverCommit, 'abc#123')
})

test('a status-details trailer round trips through the hand-written codecs', () => {
  const trailer = encodeStatusDetails(status.FAILED_PRECONDITION, 'too old', {
    reason: 'CLIENT_VERSION_SUNSET',
    domain: 'memco.ai',
    metadata: { sunset: '2027-01-01', remedy: 'upgrade' }
  })
  assert.deepEqual(errorDetails(trailer), [
    {
      reason: 'CLIENT_VERSION_SUNSET',
      domain: 'memco.ai',
      metadata: { sunset: '2027-01-01', remedy: 'upgrade' }
    }
  ])
})

test('a trailer that is not a status is reported as carrying no details', () => {
  // Losing a discriminator is a smaller loss than replacing the caller's error
  // with a parse error, so this must not throw.
  assert.deepEqual(errorDetails(Buffer.from([0xff, 0xff, 0xff])), [])
})

test('a grpc status becomes the error that says what happened', () => {
  const unauthenticated = fromServiceError({
    code: status.UNAUTHENTICATED,
    details: 'invalid credential'
  })
  assert.equal(unauthenticated.name, 'MemcoAuthenticationError')
  assert.ok(unauthenticated instanceof MemcoAuthenticationError)
  assert.equal(unauthenticated.message, 'UNAUTHENTICATED: invalid credential')
  assert.equal(unauthenticated.codeName, 'UNAUTHENTICATED')
})

test('a status this SDK does not map becomes an internal error', () => {
  assert.ok(
    fromServiceError({ code: status.DATA_LOSS, details: '' }) instanceof
      MemcoInternalError
  )
  assert.ok(fromServiceError({}) instanceof MemcoInternalError)
})

test('a daily rate limit is classified as a quota, not a rate limit', () => {
  // Quota markers are checked first: the longer window is the one that decides
  // whether retrying can help.
  const error = fromServiceError({
    code: status.RESOURCE_EXHAUSTED,
    details: 'daily rate limit reached'
  }) as MemcoResourceExhaustedError
  assert.equal(error.kind, ResourceExhaustedKind.QUOTA)
})

test('a precondition failure naming a memco sunset becomes a sunset error', () => {
  const metadata = new Metadata()
  metadata.set(
    STATUS_DETAILS_KEY,
    encodeStatusDetails(status.FAILED_PRECONDITION, 'too old', {
      reason: 'CLIENT_VERSION_SUNSET',
      domain: 'memco.ai',
      metadata: {}
    })
  )
  const error = fromServiceError({
    code: status.FAILED_PRECONDITION,
    details: 'too old',
    metadata
  })
  assert.ok(error instanceof MemcoSunsetError)
  assert.equal(error.kind, SunsetKind.CLIENT_VERSION)
})

test('a precondition failure from another domain stays a plain precondition failure', () => {
  const metadata = new Metadata()
  metadata.set(
    STATUS_DETAILS_KEY,
    encodeStatusDetails(status.FAILED_PRECONDITION, 'nope', {
      reason: 'CLIENT_VERSION_SUNSET',
      domain: 'example.com',
      metadata: {}
    })
  )
  const error = fromServiceError({
    code: status.FAILED_PRECONDITION,
    details: 'nope',
    metadata
  })
  assert.ok(error instanceof MemcoPreconditionFailedError)
  assert.ok(!(error instanceof MemcoSunsetError))
})

test('the harness answers a real call over a real socket', async () => {
  await withHarness(async harness => {
    const client = new pb.MemoryServiceClient(
      harness.address,
      ChannelCredentials.createInsecure()
    )
    try {
      const response = await new Promise<pb.StartSessionResponse>(
        (resolve, reject) => {
          client.startSession({ domain: 'coding' }, (error, value) => {
            if (error) {
              reject(error)
              return
            }
            resolve(value)
          })
        }
      )
      assert.equal(response.sessionId, 'session-a')
      assert.deepEqual(harness.memory.calls, ['startSession'])
      assert.deepEqual(harness.memory.requests.get('startSession'), {
        domain: 'coding'
      })
    } finally {
      client.close()
    }
  })
})

test('the harness can abort a call with a status the SDK then maps', async () => {
  await withHarness(async harness => {
    harness.memory.error = {
      code: status.UNAUTHENTICATED,
      details: 'invalid credential'
    }
    const client = new pb.MemoryServiceClient(
      harness.address,
      ChannelCredentials.createInsecure()
    )
    try {
      const raised = await new Promise<unknown>(resolve => {
        client.listDomains({}, error => {
          resolve(error)
        })
      })
      const mapped = fromServiceError(
        raised as Partial<import('@grpc/grpc-js').ServiceError>
      )
      assert.ok(mapped instanceof MemcoAuthenticationError)
      assert.equal(mapped.detail, 'invalid credential')
    } finally {
      client.close()
    }
  })
})

test('a protos entry with no fields under it is refused, not skipped', () => {
  // The reader's contract is to refuse a shape it does not model. Dropping the
  // entry would let a wrongly assembled package report a provenance that omits
  // one of the contract files it was built from.
  assert.throws(
    () =>
      parse(
        'server_commit: abc\nprotos:\n  -\n  - path: a.proto\n    sha256: aa\n'
      ),
    /has an empty protos entry/
  )
})

test('a protos entry missing half its fields is refused', () => {
  assert.throws(
    () => parse('server_commit: abc\nprotos:\n  - path: a.proto\n'),
    /missing path or sha256/
  )
})

test('a block scalar is refused rather than read as text', () => {
  assert.throws(
    () => parse('server_commit: |\n  abc\n'),
    /unsupported block scalar/
  )
})

test('a protos entry naming a field twice is refused', () => {
  assert.throws(
    () =>
      parse(
        'server_commit: abc\nprotos:\n  - path: a\n    path: b\n    sha256: aa\n'
      ),
    /naming path twice/
  )
})
