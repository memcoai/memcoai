# Examples

Runnable programs, smallest first. Each is self-contained and reads its
configuration from the environment:

```bash
export MEMCO_API_TOKEN=...

npm run build:test
node build/js/examples/quickstart.js
```

They are compiled rather than run from source. `node examples/quickstart.ts`
does not work twice over: Node resolves the `../src/index.js` import literally,
and there is no such file until something builds it, and its type stripping
refuses what the import leads to anyway — both `src/types.ts` and the generated
client declare `export enum`, which is not erasable syntax. `npm run build:test`
is the compile the test suite already does, so the examples are built by the
time anything else here has passed.

Each one imports `../src/index.js`, so it runs against the working tree rather
than against whatever version happens to be installed. In your own code the
import is `@memcoai/memcoai`; nothing else changes.

| Example                                            | Shows                                                                                                                               |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| [`quickstart.ts`](quickstart.ts)                   | Connect, list domains, search                                                                                                       |
| [`search_and_rate.ts`](search_and_rate.ts)         | The full read loop: session, search, rate what came back                                                                            |
| [`contribute.ts`](contribute.ts)                   | Write knowledge back, and undo it                                                                                                   |
| [`import_memories.ts`](import_memories.ts)         | Contribute many memories at once, and read what became of each                                                                      |
| [`handling_errors.ts`](handling_errors.ts)         | Every failure mode, and what to do about each                                                                                       |
| [`concurrent_searches.ts`](concurrent_searches.ts) | Many searches on one connection                                                                                                     |
| [`langchain_agent.ts`](langchain_agent.ts)         | Wiring the memory tools and a web-search tool into a LangChain agent: watch a memory miss fall back to the web and get written back |

Everything here is asynchronous — Node has no synchronous gRPC — so there are
no separate blocking counterparts to any of them.

`langchain_agent.ts` needs three packages the SDK does not install —
LangChain, `ddg-search` for its web-search tool, and a provider package for
the model you run — plus a model credential:

```bash
npm install langchain ddg-search @langchain/google-genai
```

Its module comment gives the full command. The memory tools it uses come from
the SDK's own agent layer; the web-search tool is a thin wrapper of its own.

`tests/examples.test.ts` imports every example in this directory on each run,
and asserts that importing one runs nothing — so an example that stopped
resolving, or lost its entry-point guard, fails the suite rather than waiting
for someone to run it.
