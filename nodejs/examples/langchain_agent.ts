/**
 * Give a LangChain agent the team's shared memory.
 *
 * The SDK supplies all of it: what each tool does, the JSON Schema for its
 * arguments, the results rendered as text, the guidance the service publishes
 * about the domain, the rule about which failures a model may see, and the
 * handover to LangChain itself. The same toolset also speaks Anthropic's and
 * OpenAI's tool formats, so one session drives an agent on any of them.
 *
 * What is left here is the shape of the run, and three things about it are
 * deliberate — none of them the framework's default.
 *
 * The session is opened once, in code, before the agent runs, and bound with
 * `withSession`. The agent never sees a session id and cannot omit or invent
 * one, so every call it makes is recorded as part of the same series.
 *
 * Domain guidance is supplied rather than exposed. `listDomains` is called here
 * and `briefing` renders its answer into the system prompt, because a tool the
 * model may forget to call is a tool that does not steer it.
 *
 * Validation errors go back to the agent and everything else is thrown. A
 * malformed request is something a model can fix on the next turn; a rejected
 * credential is not, and letting it read that failure only invites it to keep
 * trying. `AGENT_RECOVERABLE` is where that line is drawn, and keeping it
 * drawn takes a line of wiring here, because LangChain JS hands the model
 * every tool exception by default.
 *
 * LangChain is not a dependency of this SDK, and neither is any provider
 * package. `@memcoai/memco` installs its gRPC runtime and nothing else, and
 * `toLangChain()` imports LangChain only when it is called, so both have to be
 * installed explicitly before this file will run:
 *
 *     npm install langchain @langchain/google-genai
 *
 * Run it with:
 *
 *     export MEMCO_API_TOKEN=...
 *     export GOOGLE_API_KEY=...           # see gemini.md, next to this file
 *
 *     npm run build:test && node build/js/examples/langchain_agent.js
 *
 * Any provider LangChain speaks works — set `MEMCO_EXAMPLE_MODEL` to
 * `<provider>:<model>` for the one you run, such as `anthropic:claude-opus-5`,
 * and install that provider's package instead.
 */

import { fileURLToPath } from 'node:url'

import { AIMessage, createAgent, toolErrorMiddleware } from 'langchain'

import { Memco, briefing } from '../src/index.js'

const DOMAIN = 'coding'

// Provider-agnostic: any "<provider>:<model>" LangChain understands, given the
// matching provider package. Gemini Flash is the default because it is
// generally available, cheap enough to run the example repeatedly, and calls
// tools well.
//
// The provider is spelled `google-genai`, with a hyphen: the underscored
// `google_genai` is not in LangChain JS's provider table at all. The prefix is
// not optional either — a bare `gemini-3.7-flash` infers `google-vertexai`
// here, which is the other credential route and a different package.
const MODEL = process.env.MEMCO_EXAMPLE_MODEL ?? 'google-genai:gemini-3.7-flash'

const TASK =
  'Find out how a client should authenticate against the Memco memory API. ' +
  'Rate each result you were given. If shared memory did not answer it, say ' +
  'so plainly rather than guessing.'

/** Run the example. */
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

  // Opened here, outside the agent loop, and bound to every call the tools
  // make. Nothing the model sends can change or drop it.
  await using session = await client.memory.withSession(DOMAIN)
  console.log(`session ${session.sessionId} in ${entry.slug}\n`)

  const runnable = createAgent({
    model: MODEL,
    tools: await session.tools().toLangChain(),
    systemPrompt: briefing(entry, session.instructions),
    // Left out, a rejected credential would reach the model as a tool message
    // and it would keep calling: LangChain turns every tool exception into one
    // by default. A tool from `toLangChain()` has already decided what a model
    // may see — the AGENT_RECOVERABLE failures come back as ordinary text, and
    // nothing else does — so all this has to say is "throw what the tool
    // threw", which is what an `onError` returning nothing means.
    middleware: [toolErrorMiddleware({ onError: () => {} })]
  })
  const result = await runnable.invoke({
    messages: [{ role: 'user', content: TASK }]
  })

  for (const message of result.messages) {
    // Only an assistant turn carries tool calls, so the class is the test.
    // LangChain gives its message classes a `Symbol.hasInstance`, so this asks
    // about the shape rather than about which copy built it.
    if (!(message instanceof AIMessage)) continue
    for (const call of message.tool_calls ?? []) {
      console.log(`  -> ${call.name}(${JSON.stringify(call.args)})`)
    }
  }
  console.log(`\n${result.messages.at(-1)?.text}`)
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
