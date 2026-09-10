/**
 * Wire a LangChain agent to Memco shared memory and a web-search tool.
 *
 * The agent runs the same task twice. The first run has nothing in memory to
 * go on, so it searches the web; if it saves what it finds, the second run
 * can just search memory instead. Both runs report the tokens and time
 * spent, so you can see the difference memory makes.
 *
 * Run it with:
 *
 *     export MEMCO_API_TOKEN=...
 *     export GOOGLE_API_KEY=...
 *
 *     npm install langchain ddg-search @langchain/google-genai
 *     npm run build:test && node build/js/examples/langchain_agent.js
 *
 * Any provider LangChain speaks works — set `MEMCO_EXAMPLE_MODEL` to
 * `<provider>:<model>` for the one you run, such as `anthropic:claude-opus-5`,
 * and install that provider's package instead.
 *
 * Running it for real writes a new memory to whatever domain and credential
 * you point it at.
 */

import { setTimeout as sleep } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

import type { ClientTool } from '@langchain/core/tools'
import { search as ddgSearch } from 'ddg-search'
import { AIMessage, createAgent, tool, toolErrorMiddleware } from 'langchain'

import { Memco, briefing, type DomainEntry } from '../src/index.js'

const DOMAIN = 'coding'

// The provider is spelled `google-genai`, with a hyphen — `google_genai` is
// not in LangChain JS's provider table.
const MODEL =
  process.env.MEMCO_EXAMPLE_MODEL ?? 'google-genai:gemini-3.1-pro-preview'

const IDENTITY =
  'You are an engineering assistant for the team that builds the Memco SDKs.'

const TASK =
  'One of our services runs as a Cloud Run Function and calls several Google ' +
  'Cloud APIs — Secrets Manager, Pub/Sub, BigQuery, and Workflows — over gRPC. ' +
  'Since upgrading grpcio to 1.78.1, those calls started failing. Find out ' +
  "what's going on and report it."

/** Tokens spent on research, and how long the run took, in seconds. */
interface RunReport {
  tokens: number
  seconds: number
}

/**
 * Run one full agent turn on {@link TASK}, printing its trace and answer.
 *
 * @param client A connected client.
 * @param entry The domain to run in, from `listDomains`.
 * @returns Tokens spent researching, and how long the run took in seconds.
 */
async function runOnce(client: Memco, entry: DomainEntry): Promise<RunReport> {
  const started = performance.now()
  await using session = await client.memory.withSession(DOMAIN)
  console.log(`session ${session.id} in ${entry.slug}\n`)

  // ddg-search hardcodes a 2013-era User-Agent that DuckDuckGo's anti-bot
  // check tends to reject; this rewrites it to something current.
  const modernUserAgentFetch: typeof fetch = async (input, init) => {
    const headers = new Headers(init?.headers)
    headers.set(
      'User-Agent',
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    )
    return fetch(input, { ...init, headers })
  }

  const webSearch = tool(
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

  const tools = (await session.tools().toLangChain()) as ClientTool[]
  tools.push(webSearch)

  const runnable = createAgent({
    model: MODEL,
    tools,
    systemPrompt: [IDENTITY, briefing(entry, session.instructions)].join(
      '\n\n'
    ),
    // LangChain JS turns every tool exception into a message by default; this
    // makes an unrecoverable error (like a bad credential) end the run instead.
    middleware: [toolErrorMiddleware({ onError: () => {} })]
  })

  let tokens = 0
  let lastText = ''
  for await (const step of await runnable.stream(
    { messages: [{ role: 'user', content: TASK }] },
    { streamMode: 'values' }
  )) {
    const message = step.messages.at(-1)
    if (message === undefined) continue
    lastText = message.text
    // Only an assistant turn carries tool calls, so the class is the test.
    if (!(message instanceof AIMessage)) continue
    const calls = message.tool_calls ?? []
    for (const call of calls) {
      console.log(`  -> ${call.name}(${JSON.stringify(call.args)})`)
    }
    // Skip the final answer's tokens: it's the same in both runs.
    if (calls.length > 0) {
      tokens += message.usage_metadata?.total_tokens ?? 0
    }
  }

  const seconds = (performance.now() - started) / 1000
  console.log(`\n${lastText}\n`)
  return { tokens, seconds }
}

/** Run the example twice, to show what shared memory saves the second time. */
async function main(): Promise<void> {
  await using client = await new Memco().connect()

  const { domains } = await client.memory.listDomains()
  const entry = domains.find(one => one.slug === DOMAIN)
  if (entry === undefined) {
    const available = domains.map(one => one.slug).join(', ')
    console.log(
      `no domain '${DOMAIN}' for this credential; available: ${available}`
    )
    return
  }

  console.log('=== first run: nothing in memory yet ===\n')
  const cold = await runOnce(client, entry)

  console.log('=== waiting for the write to become searchable ===\n')
  await sleep(10_000)

  console.log("=== second run: the first run's finding is in memory now ===\n")
  const warm = await runOnce(client, entry)

  console.log('=== summary ===')
  console.log(
    `cold run: ${cold.tokens} tokens, ${cold.seconds.toFixed(1)}s (nothing in memory yet)`
  )
  console.log(
    `warm run: ${warm.tokens} tokens, ${warm.seconds.toFixed(1)}s (memory answered it)`
  )
  if (cold.tokens > 0) {
    const savedTokens = cold.tokens - warm.tokens
    const percent = Math.round((savedTokens / cold.tokens) * 100)
    console.log(`tokens saved: ${savedTokens} (${percent}%)`)
  }
  if (cold.seconds > 0) {
    const savedSeconds = cold.seconds - warm.seconds
    const percent = Math.round((savedSeconds / cold.seconds) * 100)
    console.log(`time saved: ${savedSeconds.toFixed(1)}s (${percent}%)`)
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
