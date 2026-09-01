/**
 * Write knowledge back, add to an existing memory, and undo a write.
 *
 * Run it with:
 *
 *     export MEMCO_API_TOKEN=...
 *     npm run build:test && node build/js/examples/contribute.js
 */

import { fileURLToPath } from 'node:url'

import { Memco, NEW_MEMORY, RevertOutcome } from '../src/index.js'

const DOMAIN = 'coding'

/** Run the example. */
async function main(): Promise<void> {
  await using client = await new Memco().connect()

  // Held as an id rather than bound in a scope, which is the other half of
  // what search_and_rate.ts shows: every call below names it explicitly.
  const session = await client.memory.startSession(DOMAIN)

  // Writes are accepted asynchronously, so the result addresses the operation
  // rather than the memory it will become.
  const created = await client.memory.createMemory({
    query: 'how do I authenticate against the Memco memory API',
    title: 'Retries must not be applied to memory writes',
    content:
      'A write is accepted asynchronously and its operation id identifies ' +
      'that one acceptance, so retrying a write that appears to fail can ' +
      'record it twice. Retry the read operations instead, and use the ' +
      'returned operation id to undo a write you did not mean to make.',
    sessionId: session.sessionId,
    tags: [
      { type: 'language', value: 'typescript' },
      { type: 'task', value: 'implementation' }
    ]
  })
  console.log(`created: ${created.operationId}`)

  // A null operation id means the write was accepted but cannot be undone:
  // the content is worth more than the ability to revert it.
  if (created.operationId === null) {
    console.log('  (accepted, but not revertible)')
  }

  // Enrichment adds to a memory a search returned, so the addition lands
  // alongside the existing insights instead of becoming a rival memory.
  // NEW_MEMORY is the exported spelling of the literal "new", which opens a
  // standalone memory instead — the service reads it case-sensitively, so the
  // constant is worth preferring over typing the string.
  const enriched = await client.memory.enrichMemory({
    memoryIdx: NEW_MEMORY,
    sessionId: session.sessionId,
    title: "Connecting is what teaches a client the service's limits",
    content:
      'connect() probes health and then calls listDomains. The second call ' +
      'carries the credential, so a bad token fails there, and it reports ' +
      'the caps the service enforces — which is why an oversized field is ' +
      'refused locally rather than after a round trip.'
  })
  console.log(`enriched: ${enriched.operationId}`)

  // The enrichment is left in place, so each run adds one memory to the
  // domain. Undoing it is the same call with enriched.operationId; only the
  // create is reverted below, so there is one revert to read rather than two.

  // Reverting reports what it actually removed. Not-found, expired and
  // refused are outcomes, not errors: they describe caller-visible state.
  if (created.operationId) {
    const reverted = await client.memory.revertMemory(created.operationId)
    console.log(`revert: ${RevertOutcome[reverted.outcome]}`)
    if (reverted.outcome === RevertOutcome.EXPIRED) {
      console.log('  outside the revert window')
    } else if (reverted.outcome === RevertOutcome.NOT_FOUND) {
      console.log('  ingestion may still be running; try again shortly')
    }
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
