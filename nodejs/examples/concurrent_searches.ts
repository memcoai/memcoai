/**
 * Run many searches concurrently on one connection.
 *
 * One client holds one channel; gRPC multiplexes concurrent calls over it, so
 * there is no need for a client per task. Failures are handled per task, so
 * one bad query does not sink the batch.
 *
 * Everything in this SDK is asynchronous — there is no blocking client — so
 * concurrency is the ordinary way to run a batch rather than a variant of it.
 *
 * Run it with:
 *
 *     export MEMCO_API_TOKEN=...
 *     npm run build:test && node build/js/examples/concurrent_searches.js
 */

import { fileURLToPath } from 'node:url'

import { Memco, MemcoAPIError, type SearchResult } from '../src/index.js'

const QUERIES = [
  'how does gRPC health checking work',
  'what does a case-sensitive Bearer prefix imply for clients',
  'how should a client tell a transient failure from a permanent one',
  'when should a write be retried'
]

/**
 * Run one search, turning a failure into a reportable result.
 *
 * @param client A connected client.
 * @param query The query to run.
 * @param sessionId The session to record the search under.
 * @returns The query and a one-line summary of how it went.
 */
async function search(
  client: Memco,
  query: string,
  sessionId: string
): Promise<[string, string]> {
  let result: SearchResult
  try {
    result = await client.memory.search(query, { sessionId })
  } catch (error) {
    if (error instanceof MemcoAPIError) {
      return [query, `failed: ${error.codeName}`]
    }
    throw error
  }
  const insights = result.memories.reduce(
    (total, memory) => total + memory.insights.length,
    0
  )
  return [query, `${result.memories.length} memories, ${insights} insights`]
}

/** Run the example. */
async function main(): Promise<void> {
  await using client = await new Memco().connect()
  const session = await client.memory.startSession('coding')

  // All four run concurrently over the single channel. Promise.all rejects on
  // the first failure, which is safe here only because search() returns its
  // failures rather than throwing them.
  const results = await Promise.all(
    QUERIES.map(query => search(client, query, session.sessionId))
  )

  for (const [query, summary] of results) {
    console.log(`${summary.padEnd(32)} ${query}`)
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
