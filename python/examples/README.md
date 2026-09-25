# Examples

Runnable programs, smallest first. Each is self-contained and reads its
configuration from the environment:

```bash
export MEMCO_API_TOKEN=...

uv run --with memcoai python examples/quickstart.py
```

The three examples that act for your own users need an API client's
credentials instead of a token:

```bash
export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...

uv run --with memcoai python examples/map_your_users.py
```

Don't export both kinds in one shell for the token examples: when both are set,
the client credentials win, and they read no memory of their own.

Against a local development server, which serves plaintext, add
`MEMCO_API_TLS=false` and point `MEMCO_API_HOST` at it, such as
`localhost:50052`.

Or run all of them in one go with `make run-examples` (from the repository
root, or `make -C python run-examples`) — it needs both kinds, plus whatever
`langchain_agent.py` additionally needs, below, and hands the client
credentials only to the examples that use them.

`langchain_agent.py` needs three packages the SDK does not install — LangChain,
a provider package for the model you run, and `ddgs` for its web-search tool —
plus a model credential:

```bash
pip install langchain langchain-google-genai ddgs
```

Its module docstring gives the full command. The memory tools it uses come
from `memcoai.agent`; the web-search tool is a thin wrapper of its own.

| Example | Shows |
|---|---|
| [`quickstart.py`](quickstart.py) | Connect, list domains, search |
| [`quickstart_async.py`](quickstart_async.py) | The same, on `AsyncClient` |
| [`search_and_rate.py`](search_and_rate.py) | The full read loop: session, search, rate what came back |
| [`contribute.py`](contribute.py) | Write knowledge back, and undo it |
| [`import_memories.py`](import_memories.py) | Contribute many memories at once, and read what became of each |
| [`handling_errors.py`](handling_errors.py) | Every failure mode, and what to do about each |
| [`concurrent_searches_async.py`](concurrent_searches_async.py) | Many searches on one connection |
| [`map_your_users.py`](map_your_users.py) | Map a consulting company into Memco: a company network with a project network under it per client, engineers placed across them, a refused and a forced move between projects, a key for an engineer's agent |
| [`act_as_your_user.py`](act_as_your_user.py) | Search and write as one of your users, in a session that carries their key |
| [`act_as_many_users_async.py`](act_as_many_users_async.py) | Sessions for several users open at once on one connection, each under its own key |
| [`langchain_agent.py`](langchain_agent.py) | Wiring `memcoai.agent` and a web-search tool into a LangChain agent: watch a memory miss fall back to the web and get written back |
