/**
 * Contribute a batch of memories in one call, and read what became of each.
 *
 * Use this to move knowledge you already hold — a wiki export, a runbook,
 * notes from another system — into a domain in bulk. For knowledge learned
 * during a task, `createMemory` is the call: it mints an operation id you can
 * undo with, and an import does not.
 *
 * Run it with:
 *
 *     export MEMCO_API_TOKEN=...
 *     npm run build:test && node build/js/examples/import_memories.js
 */

import { fileURLToPath } from 'node:url'

import { ImportStatus, Memco, type ImportedMemory } from '../src/index.js'

const DOMAIN = 'coding'

const BATCH: ImportedMemory[] = [
  {
    // Queries are what someone would search to find this memory. More than
    // one is worth giving: they are the ways in, not a description of it.
    queries: [
      'how do I authenticate against the Memco memory API',
      'which header does the memory API take a token in'
    ],
    insights: [
      {
        title: 'The Bearer prefix is case-sensitive',
        content:
          'Both an API key and a session token go in the same Authorization ' +
          "header. A lowercase 'bearer' is rejected with UNAUTHENTICATED, " +
          'which reads as a bad credential rather than a malformed header.'
      }
    ],
    tags: [{ type: 'language', value: 'typescript' }]
  },
  {
    queries: ['why is a memory write not retried after a connection blip'],
    insights: [
      {
        title:
          'gRPC retries are at-least-once, so writes stay out of the policy',
        content:
          'A retry sent after the server committed produces a duplicate, not ' +
          'a second chance. Only ListDomains and GetMemory are replayed.'
      }
    ]
  }
]

/** Run the example. */
async function main(): Promise<void> {
  await using client = await new Memco().connect()

  // No session: a standalone upload belongs to no series of work. Pass
  // sessionId instead when the batch was gathered during one task.
  //
  // BATCH can be any length. The service caps how many memories one call
  // carries, and the SDK divides a longer batch into that many per call — so a
  // wiki export goes in as one statement, not a chunking loop.
  const result = await client.memory.importMemories(BATCH, { domain: DOMAIN })

  for (const outcome of result.results) {
    // outcome.index always indexes BATCH, whatever the service's batch limit
    // is: the SDK renumbers every response back into the array it was given.
    const { queries } = BATCH[outcome.index]
    console.log(
      `[${outcome.index}] ${ImportStatus[outcome.status]}: ${[...queries][0]}`
    )
    if (outcome.status === ImportStatus.REJECTED) {
      // The entry itself was not usable; fix it and send it again.
      for (const problem of outcome.errors) {
        console.log(`      ${problem}`)
      }
    } else if (outcome.status === ImportStatus.ERROR) {
      // Usable, but not queued. Resubmitting is the whole remedy.
      console.log('      not queued; send this one again')
    }
  }

  // Sending the same batch twice is safe and free: an import is written under
  // an identity derived from its own content, so a second run reports
  // DUPLICATE and writes nothing. Nothing undoes an import, and this is what
  // stands in for that.
  const again = await client.memory.importMemories(BATCH, { domain: DOMAIN })
  const statuses = again.results.map(outcome => ImportStatus[outcome.status])
  console.log(`resent: [${statuses.join(', ')}]`)
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
