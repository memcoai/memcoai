# Examples

Runnable programs, smallest first. Each is self-contained and reads its
configuration from the environment:

```bash
export MEMCO_API_TOKEN=...

uv run --with memco python examples/quickstart.py
```

`langchain_agent.py` needs three packages the SDK does not install — LangChain,
a provider package for the model you run, and `ddgs` for its web-search tool —
plus a model credential:

```bash
pip install langchain langchain-google-genai ddgs
```

Its module docstring gives the full command. The memory tools it uses come
from `memco.agent`; the web-search tool is a thin wrapper of its own.

| Example | Shows |
|---|---|
| [`quickstart.py`](quickstart.py) | Connect, list domains, search |
| [`quickstart_async.py`](quickstart_async.py) | The same, on `AsyncClient` |
| [`search_and_rate.py`](search_and_rate.py) | The full read loop: session, search, rate what came back |
| [`contribute.py`](contribute.py) | Write knowledge back, and undo it |
| [`import_memories.py`](import_memories.py) | Contribute many memories at once, and read what became of each |
| [`handling_errors.py`](handling_errors.py) | Every failure mode, and what to do about each |
| [`concurrent_searches_async.py`](concurrent_searches_async.py) | Many searches on one connection |
| [`langchain_agent.py`](langchain_agent.py) | Wiring `memco.agent` and a web-search tool into a LangChain agent: watch a memory miss fall back to the web and get written back |
