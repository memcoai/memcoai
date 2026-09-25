# Examples

Runnable programs, smallest first. Each reads its configuration from the
environment:

```bash
export MEMCO_API_TOKEN=...

cd go/examples
go run ./quickstart
```

The three examples that act for your own users need an API client's
credentials instead of a token:

```bash
export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...

cd go/examples
go run ./map_your_users
```

Don't export both kinds in one shell for the token examples: when both are set,
the client credentials win, and they read no memory of their own.

Against a local development server, which serves plaintext, add
`MEMCO_API_TLS=false` and point `MEMCO_API_HOST` at it, such as
`localhost:50052`. Everything these examples create is named `goex-…` and
removed again when they finish, even after a Ctrl-C between calls. A Ctrl-C
during a create can leave what the service created before it saw the
interrupt; find it by its `goex-` name.

Or run all of them in one go with `make -C go run-examples`. It needs both
kinds, plus what `anthropic_agent` additionally needs (below), and hands the
client credentials only to the examples that use them.

This directory is a Go module of its own, so the SDK module never depends on
what the examples use. Its `go.mod` points `github.com/memcoai/memcoai/go` at
the parent directory with a `replace` line, so the examples run against the
working tree rather than a released version. In your own code, drop that line
and `go get github.com/memcoai/memcoai/go/memcoai`; nothing else changes.

| Example                                              | Shows                                                                                                                                                                                             |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`quickstart`](quickstart/main.go)                   | Connect, list domains, search                                                                                                                                                                     |
| [`search_and_rate`](search_and_rate/main.go)         | The full read loop: session, search, rate what came back                                                                                                                                          |
| [`contribute`](contribute/main.go)                   | Write knowledge back, and undo it                                                                                                                                                                 |
| [`import_memories`](import_memories/main.go)         | Contribute many memories at once, and read what became of each                                                                                                                                    |
| [`handling_errors`](handling_errors/main.go)         | Every failure mode, and what to do about each                                                                                                                                                     |
| [`concurrent_searches`](concurrent_searches/main.go) | Many searches from many goroutines on one client                                                                                                                                                  |
| [`map_your_users`](map_your_users/main.go)           | Map a consulting company into Memco: a company network, a project network under it per client, engineers across them, a refused and a forced move between projects, a key for an engineer's agent |
| [`act_as_your_user`](act_as_your_user/main.go)       | Search and write as one of your users, in a session that carries their key                                                                                                                        |
| [`act_as_many_users`](act_as_many_users/main.go)     | Sessions for several users open at once on one client, each under its own key                                                                                                                     |
| [`anthropic_agent`](anthropic_agent/main.go)         | A Claude agent with the memory tools and web search: watch a memory miss fall back to the web and get written back                                                                                |

Each program stops cleanly on Ctrl-C: `main` cancels the context it passes to
`run`, and every call the SDK makes honours it.

`anthropic_agent` uses the
[Anthropic Go SDK](https://github.com/anthropics/anthropic-sdk-go), which this
module already requires, and needs a credential for it:

```bash
export ANTHROPIC_API_KEY=...
go run ./anthropic_agent
```

It runs `claude-opus-5` unless `MEMCO_EXAMPLE_MODEL` names another model, which
must support the `web_search_20260209` tool and server-side fallbacks (Claude
Opus 5 and 4.6 through 4.8, Claude Sonnet 5 and 4.6). Its web search is
Anthropic's server-side tool, so there is nothing else to install. A run stops
after 20 requests and 5 searches, so a model that never finishes costs a known
amount. It also opts into server-side refusal fallbacks: a request the model
declines is re-run on the model Anthropic recommends for that refusal. Running
it for real writes a new memory to whatever domain and credential you point it
at.

Every example has a `main_test.go` that runs it against an in-process fake of
the service (and, for `anthropic_agent`, a fake Messages API), so `make -C go
test` keeps them working without a credential. `examples_test.go` checks that
each one is a documented `main` package whose work is in `run`, and that none
declares `init`.
