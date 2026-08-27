# Examples

Runnable programs, smallest first. Each is self-contained and reads its
configuration from the environment:

```bash
export MEMCO_API_TOKEN=...

uv run --with memco python examples/quickstart.py
```

`langchain_agent.py` needs two more packages and a model credential; its
module docstring gives the command, and [`gemini.md`](gemini.md) covers getting
a Google key. The tools it uses come from `memco.agent`, so the file itself is
the framework wiring and nothing more.

| Example | Shows |
|---|---|
| [`quickstart.py`](quickstart.py) | Connect, list domains, search |
| [`quickstart_async.py`](quickstart_async.py) | The same, on `AsyncClient` |
| [`search_and_rate.py`](search_and_rate.py) | The full read loop: session, search, rate what came back |
| [`contribute.py`](contribute.py) | Write knowledge back, and undo it |
| [`handling_errors.py`](handling_errors.py) | Every failure mode, and what to do about each |
| [`concurrent_searches_async.py`](concurrent_searches_async.py) | Many searches on one connection |
| [`langchain_agent.py`](langchain_agent.py) | Wiring `memco.agent` into a LangChain agent |

Getting a model credential is covered separately in [`gemini.md`](gemini.md).

Go and Node keep the same filenames under `go/examples/` and `nodejs/examples/`,
so a reader can compare one language against another line by line.
