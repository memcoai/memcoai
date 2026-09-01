# Examples

Runnable programs, smallest first. Each is self-contained and reads its
configuration from the environment:

```bash
export MEMCO_API_TOKEN=...

uv run --with memco python examples/quickstart.py
```

`langchain_agent.py` needs two packages the SDK does not install — LangChain and
a provider package for the model you run — plus a model credential:

```bash
pip install langchain langchain-google-genai
```

Its module docstring gives the full command, and [`gemini.md`](gemini.md) covers
getting a Google key. The tools it uses come from `memco.agent`, so the file
itself is the framework wiring and nothing more.

| Example | Shows |
|---|---|
| [`quickstart.py`](quickstart.py) | Connect, list domains, search |
| [`quickstart_async.py`](quickstart_async.py) | The same, on `AsyncClient` |
| [`search_and_rate.py`](search_and_rate.py) | The full read loop: session, search, rate what came back |
| [`contribute.py`](contribute.py) | Write knowledge back, and undo it |
| [`import_memories.py`](import_memories.py) | Contribute many memories at once, and read what became of each |
| [`handling_errors.py`](handling_errors.py) | Every failure mode, and what to do about each |
| [`concurrent_searches_async.py`](concurrent_searches_async.py) | Many searches on one connection |
| [`langchain_agent.py`](langchain_agent.py) | Wiring `memco.agent` into a LangChain agent |
