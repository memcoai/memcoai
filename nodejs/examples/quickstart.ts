/**
 * Connect, see what domains exist, and run one search.
 *
 * Node has no synchronous gRPC, so everything in this SDK is asynchronous and
 * there is no blocking client to show alongside it.
 *
 * Run it with:
 *
 *     export MEMCO_API_TOKEN=...
 *     npm run build:test && node build/js/examples/quickstart.js
 */

import { fileURLToPath } from 'node:url'

import { Memco } from '../src/index.js'

/** Run the example. */
async function main(): Promise<void> {
  // Reads MEMCO_API_TOKEN and MEMCO_API_HOST. The constructor sends nothing;
  // connect() probes the service's health endpoint and then lists domains,
  // which proves the credential and records the limits the service enforces —
  // so a bad endpoint or a bad token fails here rather than on the first real
  // call. `await using` closes the connection when the block ends.
  await using client = await new Memco().connect()

  const { domains } = await client.memory.listDomains()
  console.log(`${domains.length} domain(s) available:`)
  for (const domain of domains) {
    console.log(`  ${domain.slug.padEnd(12)} ${domain.summary.split('\n')[0]}`)
  }

  const first = domains[0]
  if (first === undefined) {
    console.log('this credential reaches no domains')
    return
  }

  const result = await client.memory.search(
    'how should a client authenticate against the memory API',
    { domain: first.slug }
  )
  console.log(`\n${result.memories.length} memories for that query:`)
  for (const memory of result.memories) {
    console.log(`  ${memory.idx}  served ${memory.timesServed}x`)
    for (const insight of memory.insights) {
      console.log(`    ${insight.updated}  ${insight.title}`)
    }
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
