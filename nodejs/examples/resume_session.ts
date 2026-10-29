/**
 * Resume a session by its id, so one task's searches stay one series across
 * turns — or across processes.
 *
 * An agent that handles a conversation one turn at a time, possibly in a
 * different process each time, opens the session on the first turn and keeps
 * only its id. Every later turn resumes it: nothing new is started, the
 * service reports the domain the session runs in, and an id that is unknown,
 * or not this caller's, is refused there rather than at the first search.
 *
 * Run it with:
 *
 *     export MEMCO_API_TOKEN=...
 *     npm run build:test && node build/js/examples/resume_session.js
 */

import { fileURLToPath } from 'node:url'

import { Memco, MemcoNotFoundError } from '../src/index.js'

const DOMAIN = 'coding'

/**
 * The first turn: open a session, search in it, and keep only its id.
 *
 * @param client A connected client.
 * @returns The session id, which is all a later turn needs.
 */
async function firstTurn(client: Memco): Promise<string> {
  // Keep the id wherever the conversation's own state lives. A session opened
  // with an externalId would end its key on leaving the block; the session
  // itself stays usable by id.
  await using session = await client.memory.withSession(DOMAIN)
  await session.search('how should a client retry a call the service refused')
  console.log(`first turn: session ${session.id} in ${session.domain?.slug}`)
  return session.id
}

/** Run the example. */
async function main(): Promise<void> {
  await using client = await new Memco().connect()

  const sessionId = await firstTurn(client)

  // A later turn. Resuming takes no domain: the session already has one, and
  // the service reports it.
  await using session = await client.memory.resumeSession(sessionId)
  console.log(`later turn: resumed ${session.id} in ${session.domain?.slug}`)
  const result = await session.search('which status codes are worth retrying')
  console.log(`  same series: ${result.sessionId === sessionId}`)

  // What has happened under the session so far. The service keeps the counts
  // asynchronously, so they can trail the search just made.
  const info = await session.getSession()
  console.log(
    `  started ${info.createdAt?.toISOString() ?? 'at an unknown time'}` +
      ` from ${info.source || 'an unnamed client'}:` +
      ` ${info.searches} searches, ${info.feedbacks} ratings,` +
      ` ${info.memoriesCreated} memories created`
  )

  // An id the service does not know, or another caller's, is refused as not
  // found — the two are answered alike, so ids cannot be probed.
  try {
    await client.memory.resumeSession('session-that-does-not-exist')
  } catch (error) {
    if (!(error instanceof MemcoNotFoundError)) throw error
    console.log('an unknown id is refused as not found')
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
