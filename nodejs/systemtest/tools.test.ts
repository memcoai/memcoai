/**
 * ListTools against the live service, run with a creator credential.
 *
 * A tool silently reported unavailable here would mean the access-set
 * filtering wired into session.tools() is misreading the service's own
 * response -- the class of bug the fake server in tests/ cannot catch, since
 * it never talks to the real authorization stack.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { OFFERED, TOOL_PREFIX } from '../src/gen/toolCopy.js'
import { Memco } from '../src/index.js'
import { TOKEN_ENV } from './support.js'
const EXPECTED_TOOLS = new Set(OFFERED.map(name => TOOL_PREFIX + name))

/**
 * Run `body` against a live client, closing it whatever happens.
 *
 * Mirrors `lifecycle.test.ts`'s helper of the same name: `connect()`
 * deliberately leaves the channel open when it fails, so the caller owns the
 * close either way.
 */
async function withClient<T>(body: (client: Memco) => Promise<T>): Promise<T> {
  // Named explicitly, as in lifecycle.test.ts: the API client's variables
  // would otherwise win over MEMCO_API_TOKEN.
  const client = new Memco({ token: process.env[TOKEN_ENV] })
  try {
    await client.connect()
    return await body(client)
  } finally {
    await client.close()
  }
}

if (process.env[TOKEN_ENV]) {
  const domains = await withClient(async client => {
    const listed = await client.memory.listDomains()
    return listed.domains.map(entry => entry.slug)
  })
  assert.ok(domains.length > 0, 'the service returned no domains')

  for (const domain of domains) {
    test(`a creator credential is offered every tool [${domain}]`, async () => {
      await withClient(async client => {
        const catalog = await client.memory.listTools()
        assert.ok(catalog.length > 0, 'the service returned no tools')
        const unavailable = catalog
          .filter(tool => !tool.available)
          .map(tool => tool.name)
        assert.deepEqual(
          unavailable,
          [],
          `reported unavailable to this creator credential: ${unavailable.join(', ')}`
        )

        const session = await client.memory.startSession(domain)
        const offered = new Set(session.tools().tools.map(tool => tool.name))
        assert.deepEqual(
          offered,
          EXPECTED_TOOLS,
          `expected every offered tool, got: ${[...offered].sort().join(', ')}`
        )
      })
    })
  }
} else {
  test(
    'a creator credential is offered every tool',
    { skip: `${TOKEN_ENV} is not set: the system test needs a credential` },
    () => {}
  )
}
