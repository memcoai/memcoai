/**
 * The example's wiring, exercised through a real agent run.
 *
 * `src/agent.ts` decides what a model is told, what it may send, and how the
 * tools reach LangChain; `tests/agent.test.ts` covers all of that against the
 * toolset directly. What is left to this file is what only a real run shows:
 * that a tool result comes back as a tool message, that an error the SDK
 * throws escapes the run instead of being swallowed into the model's context,
 * that the web-search tool's own failures reach the model as text instead of
 * crashing it, and that a memory miss can fall back to the web and get
 * written back — all through the same agent loop the example builds.
 *
 * The second is the one the example has to arrange deliberately: LangChain JS
 * turns every tool exception into a tool message by default, so the example
 * switches that off with `toolErrorMiddleware` and the run below carries the
 * same wiring. A version that hands auth failures to the model anyway fails the
 * build, rather than shipping an agent that spends a task hammering a dead
 * credential.
 *
 * No example in this directory exports anything, so the tests that pin the
 * example's own wiring read its source rather than importing it — which is
 * what `tests/examples.test.ts` already does for the entry-point guard.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { status } from '@grpc/grpc-js'
import type { ClientTool } from '@langchain/core/tools'
import { search as ddgSearch } from 'ddg-search'
import {
  AIMessage,
  createAgent,
  fakeModel,
  initChatModel,
  tool,
  toolErrorMiddleware
} from 'langchain'

import { Memco } from '../src/client.js'
import { MemcoAuthenticationError } from '../src/errors.js'
import { withHarness, type Harness } from './fakeServer.js'

// Three levels up from the compiled `<outDir>/tests/`, for the reason
// tests/examples.test.ts sets out at the same walk.
const here = fileURLToPath(new URL('.', import.meta.url))
const SOURCE = readFileSync(
  join(here, '..', '..', '..', 'examples', 'langchain_agent.ts'),
  'utf8'
)

const QUERY = 'grpcio 1.78.1 Cloud Run Functions bug'

/** The same User-Agent rewrite `runOnce` applies — see its module comment. */
const modernUserAgentFetch: typeof fetch = async (input, init) => {
  const headers = new Headers(init?.headers)
  headers.set(
    'User-Agent',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
  )
  return fetch(input, { ...init, headers })
}

/** Build the same failure-tolerant web-search tool `runOnce` builds. */
function webSearchTool(): ClientTool {
  return tool(
    async ({ query }: { query: string }): Promise<string> => {
      try {
        const { results } = await ddgSearch(query, {
          maxPages: 1,
          maxResults: 5,
          region: '',
          time: '',
          fetchImpl: modernUserAgentFetch
        })
        return (
          results.map(r => `${r.title}: ${r.description}`).join('\n') ||
          'no results'
        )
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        return `web search failed: ${message}`
      }
    },
    {
      name: 'web_search',
      description: 'Search the web with DuckDuckGo.',
      schema: {
        type: 'object',
        properties: { query: { type: 'string' } },
        required: ['query']
      }
    }
  )
}

/**
 * Run one agent turn through a scripted sequence of tool calls, then stop.
 *
 * @param calls One tool call the model should make per entry, in order.
 * @param arrange Anything to stage on the fake before the tool calls are made.
 * @returns The content of every tool message the run produced.
 */
async function told(
  calls: Array<{ name: string; args: Record<string, unknown> }>,
  arrange: (harness: Harness) => void = () => {}
): Promise<string[]> {
  let said: string[] = []
  await withHarness(async harness => {
    const memco = new Memco({
      token: 'test-token',
      host: harness.address,
      tls: false
    })
    try {
      await memco.connect()
      const session = await memco.memory.withSession('coding')
      // Arranged after the session is open, so a staged failure hits the tool
      // call rather than the connection check or the session itself.
      arrange(harness)
      // LangChain JS ships no scriptable fake chat model, so `fakeModel()` is
      // the stand-in: a queue of scripted replies, and a bindTools that takes
      // the definitions and shares the same queue.
      const model = calls
        .reduce((built, call) => built.respondWithTools([call]), fakeModel())
        .respond(new AIMessage('done'))
      const tools = (await session.tools().toLangChain()) as ClientTool[]
      tools.push(webSearchTool())
      const runnable = createAgent({
        model,
        tools,
        // The example's own wiring, and the whole reason this file runs a real
        // agent. Without it LangChain converts every tool exception into a tool
        // message, and the credential test below would pass a dead token back
        // to the model instead of ending the run.
        middleware: [toolErrorMiddleware({ onError: () => {} })]
      })
      const state = await runnable.invoke({
        messages: [{ role: 'user', content: 'go' }]
      })
      said = state.messages
        .filter(message => message.getType() === 'tool')
        .map(message => message.text)
    } finally {
      await memco.close()
    }
  })
  return said
}

test('a result reaches the model as a tool message', async () => {
  assert.deepEqual(
    await told([{ name: 'memco_search', args: { query: 'how does X work' } }]),
    ['0 memories']
  )
})

test('a malformed request comes back to the agent', async () => {
  assert.deepEqual(
    await told([{ name: 'memco_search', args: { query: '' } }]),
    ['invalid request: query must not be empty']
  )
})

test('a missing argument comes back to the agent', async () => {
  // LangChain enforces `required` on the JSON Schema and refuses the call
  // before the tool body runs, so what the model reads is LangChain's sentence
  // rather than ours. The behaviour is what this pins: a missing argument is a
  // correction the model can act on, not an exception that ends the run — and
  // it stays one whether LangChain or the SDK's own check catches it.
  //
  // If this ever fails because LangChain stopped checking, the SDK's own check
  // is the backstop and tests/agent.test.ts already pins its wording; the
  // expected string here becomes `missing required argument(s): query`.
  const said = await told([
    { name: 'memco_search', args: { tags: [{ type: 'l', value: 'python' }] } }
  ])
  assert.equal(said.length, 1)
  assert.match(said[0], /did not match expected schema/)
})

test('a rejected credential ends the run', async () => {
  // A model cannot fix a credential, and letting it read the failure only
  // invites it to keep trying against a dead one.
  //
  // The run above builds its own agent, so nothing it asserts reaches the
  // example. This does: the middleware is what makes the example behave the
  // way the rest of this test describes, and without this line it could be
  // deleted from the example with the whole suite still green.
  assert.match(
    SOURCE,
    /middleware: \[toolErrorMiddleware\(\{\s*onError: \(\) => \{\}\s*\}\)\]/
  )
  await assert.rejects(
    told(
      [{ name: 'memco_search', args: { query: 'how does X work' } }],
      harness => {
        harness.memory.error = {
          code: status.UNAUTHENTICATED,
          details: 'credential rejected'
        }
      }
    ),
    MemcoAuthenticationError
  )
})

test('a memory miss falls back to the web and writes a memory back', async t => {
  // Keeps the web search hermetic, the same way Harness keeps the memco calls
  // hermetic: no test in this suite may make a real network call. ddg-search
  // resolves `fetch` at call time (a default parameter, not a value captured
  // at import time), so mocking the global here reaches it; the HTML mirrors
  // the minimum shape parsePage() looks for (a `.result.web-result` with
  // `.result__a` and `.result__snippet`).
  t.mock.method(globalThis, 'fetch', async () => {
    const html =
      '<div class="result web-result">' +
      '<a class="result__a" href="https://github.com/grpc/grpc/issues/41725">' +
      'grpc/grpc#41725</a>' +
      '<div class="result__snippet">fixed in 1.79.0</div>' +
      '</div>'
    return new Response(html, { status: 200 })
  })

  const said = await told([
    { name: 'memco_search', args: { query: QUERY } },
    { name: 'web_search', args: { query: QUERY } },
    {
      name: 'memco_create_memory',
      args: {
        query: QUERY,
        title: 'grpcio 1.78.1 Cloud Run Functions bug',
        content: 'tracked as grpc/grpc#41725; fixed in 1.79.0'
      }
    }
  ])

  assert.equal(said[0], '0 memories')
  assert.match(said[1], /grpc\/grpc#41725/)
  assert.match(said[2], /accepted as operation create-a/)
})

test('a web search failure reaches the model as text instead of crashing', async t => {
  // A raw exception from the underlying fetch, exactly like a real DNS or
  // rate-limit failure — the wrapping in webSearchTool() (and runOnce's copy
  // of it) must turn this into a tool message rather than letting it
  // propagate and end the run.
  t.mock.method(globalThis, 'fetch', async () => {
    throw new Error('DNS error: no records found')
  })

  const said = await told([{ name: 'web_search', args: { query: QUERY } }])

  assert.deepEqual(said, ['web search failed: DNS error: no records found'])
})

test('the example is only the shape of the run', () => {
  // Everything reusable for talking to LangChain lives in src/agent.ts, so
  // the example declares no wiring helpers of its own. runOnce is the
  // exception: it is this example's own two-runs-and-compare orchestration,
  // not framework plumbing, so it has nowhere else to live. Read off the
  // source, because no example in this directory exports anything to read
  // off the module — and read every top-level binding rather than the
  // callable-looking ones, so that which keyword a helper arrives under
  // cannot decide whether it is noticed.
  const bindings =
    /^(?:export )?(?:async )?(?:function\*?|class|const|let|var) (\w+)/gm
  const declared = [...SOURCE.matchAll(bindings)].map(found => found[1])
  assert.deepEqual(declared, [
    'DOMAIN',
    'MODEL',
    'IDENTITY',
    'TASK',
    'runOnce',
    'main'
  ])
})

test('the example names a model LangChain can resolve', async () => {
  // The default is a provider string, not a bare model name. Dropping the
  // prefix does not fail loudly: `gemini-3.1-pro-preview` on its own infers
  // `google-vertexai`, so the example would quietly ask for the other
  // credential route, and say so only at run time with GOOGLE_API_KEY already
  // exported. The environment variable is named in full because the module
  // comment documents it as the override; the whitespace is loose because a
  // longer model id takes the declaration over 80 columns and prettier then
  // breaks it across two or three lines, which is not a regression to report.
  const declared =
    /const MODEL =\s+process\.env\.MEMCO_EXAMPLE_MODEL\s+\?\?\s+'(.+)'/
  const found = declared.exec(SOURCE)
  assert.ok(found, 'MODEL is no longer an environment read with a default')
  const [provider, ...rest] = found[1].split(':')
  assert.equal(provider, 'google-genai')
  assert.ok(rest.join(':'))

  // And it really is one this LangChain resolves. The prefix is `google-genai`
  // and not `google_genai`, because LangChain implementations do not all spell
  // their provider tables the same way, so this measures which spelling this
  // one accepts rather than assuming. An unrecognised prefix is not reported as
  // such: LangChain falls back to inferring a provider from the whole string,
  // and reports failing that. Ruling that one message out is therefore the
  // check, and it holds whether or not the provider package itself is
  // installed.
  const failure = await initChatModel(found[1]).then(
    () => null,
    (error: Error) => error
  )
  assert.doesNotMatch(failure?.message ?? '', /Unable to infer model provider/)
})
