/**
 * Every command the service publishes, in one story, against the real server.
 *
 * The order is not a stylistic choice. A write is accepted asynchronously, so
 * `createMemory` hands back an operation id rather than a memory, and the
 * memory it will become is not addressable until ingestion has run. Reverting
 * before then reports NOT_FOUND — the contract says so outright: "either it
 * never existed or its ingestion has not completed yet". So the memory has to
 * be found by searching for it before it can be rated, fetched, enriched or
 * removed.
 *
 * The content this writes is real prose about this SDK rather than filler. A
 * write is evaluated on the way in and can be rejected downstream by the
 * service's quality gate, and a rejected memory never becomes searchable —
 * which would surface here as "search never found it", indistinguishable from
 * a broken search path.
 *
 */

import assert from 'node:assert/strict'
import { randomBytes } from 'node:crypto'
import { test } from 'node:test'
import { setTimeout as sleep } from 'node:timers/promises'

import { DataSource, Memco, RevertOutcome } from '../src/index.js'
import type { Insight, Memory } from '../src/index.js'

const TOKEN_ENV = 'MEMCO_API_TOKEN'

// A write is accepted asynchronously and only becomes searchable once ingestion
// has run, so every assertion about a memory existing — or having stopped
// existing — is a poll rather than a single call.
//
// The interval is 15s rather than something tighter because the service rate
// limits search, and the ingestion poll is the one that runs longest: at 5s a
// single domain could spend 36 searches waiting. Slower polling costs only
// resolution on when the memory appeared, which nothing here asserts on.
const INGEST_TIMEOUT_MS = 180_000
const REMOVAL_TIMEOUT_MS = 60_000
const POLL_INTERVAL_MS = 15_000

/**
 * Run `body` against a live client, closing it whatever happens.
 *
 * The client is built before the try and connected inside it, because
 * `connect()` deliberately leaves the channel open when it fails — that is what
 * makes retrying it cheap — so the caller owns the close either way. A leaked
 * channel is not a tidiness problem here: an open handle keeps `node --test`
 * from ever finalising, so a bad token would hang the job until its timeout
 * instead of failing in seconds.
 */
async function withClient<T>(body: (client: Memco) => Promise<T>): Promise<T> {
  const client = new Memco()
  try {
    await client.connect()
    return await body(client)
  } finally {
    await client.close()
  }
}

/** A token unique to this run, this domain and this SDK. */
function nonceFor(domain: string): string {
  const run = process.env.GITHUB_RUN_ID
  const attempt = process.env.GITHUB_RUN_ATTEMPT ?? '1'
  const tail = run ? `${run}-${attempt}` : randomBytes(4).toString('hex')
  return `nodesys-${domain}-${tail}`
}

/**
 * The memory this run writes: true, substantive, and free of identifiers.
 *
 * The run marker lives in the query and nowhere else. The query becomes the
 * memory's intent, which comes back on every search result, so the test can
 * still recognise its own memory — while the insight the service evaluates is
 * pure prose.
 *
 * Two quality-gate rules have already rejected versions of this, and a
 * rejection is invisible from here: the write is accepted, the memory never
 * becomes searchable, and the failure surfaces as a search that never finds it.
 * Content dominated by identifiers is refused, which is why no marker appears in
 * the title or the body. So is anything the gate reads as a report of work
 * rather than durable knowledge — three drafts were refused for it, ending
 * respectively "if you are reading it, that run did not finish cleaning up",
 * "this note is written and then removed again by the system test", and a bare
 * "added and then removed again by the system test".
 *
 * So the body says nothing about itself at all. Every sentence is a fact about
 * the SDK; the fact that a test wrote it lives in the query, which the gate does
 * not evaluate.
 *
 * Do not conclude from one approval that a form of words is safe. The gate is an
 * LLM sampled at a non-zero temperature, so the same shape has been both
 * approved and refused; only removing the self-reference makes it reliable.
 *
 * The subject is deliberately specific to the Node.js SDK, so it can never be
 * read as the same knowledge as what the Python suite writes.
 */
function probe(nonce: string): {
  query: string
  title: string
  content: string
} {
  return {
    query:
      'Why does the Memco Node.js SDK compile its test suite to JavaScript ' +
      `before running it? (system test ${nonce})`,
    title: 'Node.js SDK test build',
    content:
      "Node's type stripping cannot run the Memco Node.js SDK's suite over the " +
      'TypeScript sources. The generated gRPC client comes from ts-proto, which emits ' +
      '`export enum DataSource` — not erasable syntax — so `node --test` over the .ts ' +
      'files fails before a single test runs.\n\n' +
      'tsconfig.test.json therefore compiles everything to build/js, and the suite is ' +
      'invoked with an explicit quoted glob so the shell leaves it for Node to expand. ' +
      'A bare directory argument is not equivalent: on Node 24 it is loaded as a module ' +
      'and fails with MODULE_NOT_FOUND.'
  }
}

/**
 * The insight the enrich step adds to the memory above.
 *
 * A different fact about the same subject, on purpose. An enrichment close
 * enough to the insight it joins can be endorsed as a duplicate rather than
 * added as new, and it would then never appear as a second insight to find.
 *
 * Its title is what tells it apart from the insight it joins, so neither needs
 * a marker of its own.
 */
function addition(): { title: string; content: string } {
  return {
    title: 'Node.js SDK npm lifecycle hooks',
    content:
      "The SDK's .npmrc sets `ignore-scripts=true`, so npm never fires a `prebuild` or " +
      '`posttest` lifecycle hook. Any multi-step operation has to chain its steps with ' +
      '`&&` inside a single script instead; a step written as a lifecycle hook would be ' +
      'skipped in silence behind a green build.'
  }
}

/** The insight of this memory with exactly this title, if any. */
function insightTitled(memory: Memory, title: string): Insight | undefined {
  return memory.insights.find(insight => insight.title === title)
}

/**
 * Whether this memory was written by this run.
 *
 * The marker is in the intent because it cannot be in the insight: the query
 * passed to createMemory becomes the memory's intent, and intents come back on
 * every search result.
 */
function isOurs(memory: Memory, nonce: string): boolean {
  return memory.intents.some(intent => intent.includes(nonce))
}

/**
 * A memory with its insights, fetching them if the search withheld them.
 *
 * Within one session a memory a previous search already delivered comes back as
 * a bare reference carrying no insights. The polling below re-searches in one
 * session, so without this a memory delivered once — before its insight was
 * attached, say — would never match again and the poll would time out blaming
 * the quality gate.
 */
async function resolved(client: Memco, memory: Memory): Promise<Memory> {
  if (memory.insights.length > 0 || !memory.reference) return memory
  return client.memory.getMemory(memory.idx)
}

/**
 * Poll the search until our own memory comes back, or give up loudly.
 *
 * Every returned memory is examined rather than just the first: the probe is
 * brand new and competing with whatever else the domain holds, so its rank is
 * not something the test may assume.
 */
async function searchUntilFound(
  client: Memco,
  sessionId: string,
  query: string,
  nonce: string,
  title: string
): Promise<[Memory, Insight]> {
  const deadline = Date.now() + INGEST_TIMEOUT_MS
  let seen = 0
  while (Date.now() < deadline) {
    const result = await client.memory.search(query, { sessionId })
    seen = result.memories.length
    for (const candidate of result.memories) {
      const memory = await resolved(client, candidate)
      if (!isOurs(memory, nonce)) continue
      const insight = insightTitled(memory, title)
      if (insight !== undefined) return [memory, insight]
    }
    console.log(
      `  waiting for ingestion; search returned ${seen} memories, none ours`
    )
    await sleep(POLL_INTERVAL_MS)
  }
  return assert.fail(
    `the memory never became searchable within ${INGEST_TIMEOUT_MS / 1000}s ` +
      `(last search returned ${seen} memories, none whose intent names ${nonce}). ` +
      'Three things can cause this. The write may have been rejected downstream by ' +
      'the quality gate, in which case it never becomes searchable at all. Ingestion ' +
      'may simply be slower than the budget. Or the service may not carry a newly ' +
      "created memory's own query in its intents, which is the assumption this suite " +
      'rests on to recognise its own memory without an identifier in the insight.'
  )
}

/**
 * Poll a fresh search until the memory carries an insight with this title.
 *
 * Deliberately not GetMemory on the idx the earlier search returned. An idx is
 * bound to the search that issued it and resolves to that snapshot, so an
 * insight added afterwards never appears through it — polling it can only time
 * out. A search opened without a session returns the memory as it now stands.
 *
 * A new session each time, too: within one session a memory already returned
 * comes back as a bare reference carrying no insights, so the second poll
 * onwards would have nothing to look at.
 */
async function searchUntilTitled(
  client: Memco,
  query: string,
  domain: string,
  nonce: string,
  title: string
): Promise<Insight> {
  const deadline = Date.now() + INGEST_TIMEOUT_MS
  while (Date.now() < deadline) {
    for (const candidate of (await client.memory.search(query, { domain }))
      .memories) {
      if (!isOurs(candidate, nonce)) continue
      const found = insightTitled(candidate, title)
      if (found !== undefined) return found
    }
    console.log('  waiting for the enrichment to be ingested')
    await sleep(POLL_INTERVAL_MS)
  }
  return assert.fail(
    `the enrichment never appeared within ${INGEST_TIMEOUT_MS / 1000}s. Either ingestion ` +
      'is slower than the budget, or the service endorsed the addition as a duplicate of ' +
      'an insight the memory already held instead of adding it as a new one.'
  )
}

/** Poll a fresh search until this run's memory is no longer returned. */
async function searchUntilAbsent(
  client: Memco,
  query: string,
  domain: string,
  nonce: string
): Promise<void> {
  const deadline = Date.now() + REMOVAL_TIMEOUT_MS
  while (Date.now() < deadline) {
    const found = (
      await client.memory.search(query, { domain })
    ).memories.filter(m => isOurs(m, nonce))
    if (found.length === 0) return
    console.log('  waiting for the removal to take effect')
    await sleep(POLL_INTERVAL_MS)
  }
  assert.fail(
    `the memory was still returned by search ${REMOVAL_TIMEOUT_MS / 1000}s after a revert ` +
      'reported MEMORY_REMOVED'
  )
}

/** Poll GetMemory until it reports the memory is gone. */
async function getUntilGone(client: Memco, idx: string): Promise<void> {
  const deadline = Date.now() + REMOVAL_TIMEOUT_MS
  while (Date.now() < deadline) {
    try {
      await client.memory.getMemory(idx)
    } catch (error) {
      // Discriminated by name, not instanceof: the package ships dual ESM and
      // CJS builds, so the class identity is not stable across them.
      if (error instanceof Error && error.name === 'MemcoNotFoundError') return
      throw error
    }
    console.log('  waiting for the removal to take effect')
    await sleep(POLL_INTERVAL_MS)
  }
  assert.fail(
    `${idx} was still retrievable ${REMOVAL_TIMEOUT_MS / 1000}s after a revert ` +
      'reported MEMORY_REMOVED'
  )
}

async function lifecycle(domain: string): Promise<void> {
  const nonce = nonceFor(domain)
  const fields = probe(nonce)
  const extra = addition()
  console.log(`\n[${domain}] probe ${nonce}`)

  await withClient(async client => {
    const outstanding: string[] = []
    try {
      // ListDomains is exercised by the module-level listing that produced
      // `domain` and again by connect(), so it is not called a third time here.
      const session = await client.memory.startSession(domain)
      assert.ok(session.sessionId)

      const write = await client.memory.createMemory({
        query: fields.query,
        title: fields.title,
        content: fields.content,
        sessionId: session.sessionId,
        source: DataSource.AGENT
      })
      assert.ok(
        write.operationId,
        'the write was accepted without an operation id, so it cannot be reverted; ' +
          'refusing to leave a memory behind in a live domain'
      )
      outstanding.push(write.operationId)
      console.log(`  created, operation ${write.operationId}`)

      const [memory, insight] = await searchUntilFound(
        client,
        session.sessionId,
        fields.query,
        nonce,
        fields.title
      )
      console.log(`  found as ${memory.idx}, insight ${insight.idx}`)

      const feedback = await client.memory.shareFeedback({
        sessionId: session.sessionId,
        feedback: [{ idx: insight.idx, relevant: true, correct: true }]
      })
      assert.ok(feedback.entries.some(entry => entry.idx === insight.idx))

      const fetched = await client.memory.getMemory(memory.idx)
      assert.equal(fetched.idx, memory.idx)
      assert.ok(insightTitled(fetched, fields.title))

      // An enrichment is a second write against the same memory, with an
      // operation id of its own.
      const enrichment = await client.memory.enrichMemory({
        memoryIdx: memory.idx,
        sessionId: session.sessionId,
        title: extra.title,
        content: extra.content
      })
      assert.ok(enrichment.operationId)
      outstanding.push(enrichment.operationId)
      const added = await searchUntilTitled(
        client,
        fields.query,
        domain,
        nonce,
        extra.title
      )
      console.log(`  enriched, insight ${added.idx}`)

      const undoAddition = await client.memory.revertMemory(
        enrichment.operationId
      )
      assert.equal(
        undoAddition.outcome,
        RevertOutcome.ADDITION_REMOVED,
        `reverting the enrichment reported ${RevertOutcome[undoAddition.outcome]}, ` +
          'not ADDITION_REMOVED'
      )
      // The outcome is what the service reported; this is what it did. The
      // addition is gone and the memory it joined is intact.
      const after = await searchUntilTitled(
        client,
        fields.query,
        domain,
        nonce,
        fields.title
      )
      assert.equal(
        after.title,
        fields.title,
        'reverting the addition took the original insight with it'
      )

      // Removing the original insight takes its memory with it: it is the last
      // one the memory holds.
      const undoMemory = await client.memory.revertMemory(write.operationId)
      assert.equal(
        undoMemory.outcome,
        RevertOutcome.MEMORY_REMOVED,
        `reverting the write reported ${RevertOutcome[undoMemory.outcome]}, not ` +
          'MEMORY_REMOVED. MERGED or ADDITION_REMOVED means the probe was folded into ' +
          'an existing memory. The insight carries no run marker, so two runs of this ' +
          'SDK overlapping would write identical content and collide; the CI job ' +
          'holds a per-language concurrency group to keep one in flight at a time.'
      )
      console.log('  reverted')

      await searchUntilAbsent(client, fields.query, domain, nonce)
      await getUntilGone(client, memory.idx)
      console.log('  gone')
    } finally {
      // The test reverts these itself and asserts on the outcome; this is the
      // safety net for a run that failed somewhere in between. Revert is
      // idempotent — a second one reports NOT_FOUND, a successful call — so
      // reverting everything costs an RPC and never an error.
      for (const operationId of outstanding.toReversed()) {
        try {
          await client.memory.revertMemory(operationId)
        } catch (error) {
          console.log(
            `cleanup: reverting ${operationId} failed: ${String(error)}`
          )
        }
      }
    }
  })
}

if (process.env[TOKEN_ENV]) {
  const domains = await withClient(async client => {
    const listed = await client.memory.listDomains()
    return listed.domains.map(entry => entry.slug)
  })
  assert.ok(domains.length > 0, 'the service returned no domains')

  for (const domain of domains) {
    test(`the whole lifecycle runs against the live service [${domain}]`, async () => {
      await lifecycle(domain)
    })
  }
} else {
  test(
    'the system test runs the whole lifecycle against the live service',
    { skip: `${TOKEN_ENV} is not set: the system test needs a credential` },
    () => {}
  )
}
