/**
 * The full read loop: open a session, search under it, rate what came back.
 *
 * Rating is the part people skip, and it is the only signal the service gets
 * about whether a result actually answered the question. Searches made under
 * one session are recorded as a series, which is what makes them rateable
 * afterwards.
 *
 * Run it with:
 *
 *     export MEMCO_API_TOKEN=...
 *     npm run build:test && node build/js/examples/search_and_rate.js
 */

import { fileURLToPath } from 'node:url'

import { Memco, type FeedbackRating } from '../src/index.js'

const DOMAIN = 'coding'

/** Run the example. */
async function main(): Promise<void> {
  await using client = await new Memco().connect()

  // A session ties related searches together. The scope carries its id into
  // every call made through it, so nothing later can drop the handle — which
  // is the whole reason to prefer it over holding the id yourself. Use one
  // scope for every search made for the same task, and for the ratings
  // afterwards.
  await using session = await client.memory.withSession(DOMAIN)
  console.log(`session ${session.sessionId}`)

  const result = await session.search(
    'how does gRPC health checking interact with an auth interceptor',
    {
      // Which tag types narrow results and which merely boost them is
      // per-domain; listDomains() describes them. A wrong filtering tag
      // returns nothing at all, so start without tags if unsure.
      tags: [{ type: 'language', value: 'typescript', version: '5.9' }]
    }
  )

  if (result.notice) {
    console.log(`notice: ${result.notice}`)
  }
  if (result.memories.length === 0) {
    console.log('nothing matched; try a broader query')
    return
  }

  const ratings: FeedbackRating[] = []
  for (const memory of result.memories) {
    for (const insight of memory.insights) {
      console.log(`\n${insight.title}  (updated ${insight.updated})`)
      console.log(
        `  endorsed ${insight.endorsed} / disputed ${insight.disputed}`
      )
      console.log(`  ${insight.content.slice(0, 200)}...`)

      // Handles are opaque and must be copied exactly from a result; they
      // cannot be constructed by hand.
      ratings.push({
        idx: insight.idx,
        relevant: true,
        correct: true,
        comment: 'answered the question directly'
      })
    }
  }

  if (ratings.length === 0) {
    // Possible even with memories in hand: one that only references an
    // earlier result in this session carries no insights of its own, and
    // shareFeedback refuses an empty batch before sending it.
    console.log('nothing to rate')
    return
  }

  const recorded = await session.shareFeedback({ feedback: ratings })
  console.log(`\nrecorded ${recorded.entries.length} rating(s)`)
  for (const entry of recorded.entries) {
    if (entry.advice) {
      console.log(`  ${entry.idx}: ${entry.advice}`)
    }
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
