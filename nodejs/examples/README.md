# Examples

Runnable programs, smallest first. Each is self-contained and reads its
configuration from the environment:

```bash
export MEMCO_API_TOKEN=...

npm run build:test
node build/js/examples/quickstart.js
```

The three examples that act for your own users need an API client's
credentials instead of a token:

```bash
export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...

npm run build:test
node build/js/examples/map_your_users.js
```

Don't export both kinds in one shell for the token examples: when both are set,
the client credentials win, and they read no memory of their own.

Against a local development server, which serves plaintext, add
`MEMCO_API_TLS=false` and point `MEMCO_API_HOST` at it, such as
`localhost:50052`.

Or run all of them in one go with `make run-examples` (from the repository
root, or `make -C nodejs run-examples`) — it needs both kinds, plus whatever
`langchain_agent.ts` additionally needs, below, and hands the client
credentials only to the examples that use them.

They are compiled rather than run from source. `node examples/quickstart.ts`
does not work twice over: Node resolves the `../src/index.js` import literally,
and there is no such file until something builds it, and its type stripping
refuses what the import leads to anyway — both `src/types.ts` and the generated
client declare `export enum`, which is not erasable syntax. `npm run build:test`
is the compile the test suite already does, so the examples are built by the
time anything else here has passed.

Each one imports `../src/index.js`, so it runs against the working tree rather
than against whatever version happens to be installed. In your own code the
import is `@memco/memcoai`; nothing else changes.

| Example                                            | Shows                                                                                                                                                                                                        |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`quickstart.ts`](quickstart.ts)                   | Connect, list domains, search                                                                                                                                                                                |
| [`search_and_rate.ts`](search_and_rate.ts)         | The full read loop: session, search, rate what came back                                                                                                                                                     |
| [`contribute.ts`](contribute.ts)                   | Write knowledge back, and undo it                                                                                                                                                                            |
| [`import_memories.ts`](import_memories.ts)         | Contribute many memories at once, and read what became of each                                                                                                                                               |
| [`handling_errors.ts`](handling_errors.ts)         | Every failure mode, and what to do about each                                                                                                                                                                |
| [`concurrent_searches.ts`](concurrent_searches.ts) | Many searches on one connection                                                                                                                                                                              |
| [`map_your_users.ts`](map_your_users.ts)           | Map a consulting company into Memco: a company network with a project network under it per client, engineers placed across them, a refused and a forced move between projects, a key for an engineer's agent |
| [`act_as_your_user.ts`](act_as_your_user.ts)       | Search and write as one of your users, in a session that carries their key                                                                                                                                   |
| [`act_as_many_users.ts`](act_as_many_users.ts)     | Sessions for several users open at once on one connection, each under its own key                                                                                                                            |
| [`langchain_agent.ts`](langchain_agent.ts)         | Wiring the memory tools and a web-search tool into a LangChain agent: watch a memory miss fall back to the web and get written back                                                                          |

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
