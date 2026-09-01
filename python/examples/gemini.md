# Running the agent example on Gemini

[`langchain_agent.py`](langchain_agent.py) defaults to Gemini. This is how to
get it a credential from a Google Cloud project you administer.

There are two routes. **Take the first one** unless you have a reason not to.

| | Credential | Good for |
|---|---|---|
| **API key** | `GOOGLE_API_KEY` | running the example, local development, CI |
| **Application Default Credentials** | no key at all | production, anything on GCP compute |

---

## Route 1 — an API key

### Before you start: standard keys stop working in September 2026

Google issues two kinds of Gemini API key, and the distinction is about to stop
being cosmetic:

- an **auth key** is bound to a Google Cloud **service account**, so calls carry
  an identity, IAM applies, and a leaked key is enforced against quickly;
- a **standard key** only says which project to bill. Nothing identifies the
  caller.

**From September 2026 the Gemini API rejects standard keys.** Any key you create
now in AI Studio is an auth key by default, so a key made today is fine — but if
your project has older keys in use anywhere, they need replacing rather than
just leaving alone. That deadline is days away at the time of writing; confirm
the current state before you rely on this paragraph.

### Create the key

As a project admin:

1. Open **<https://aistudio.google.com/apikey>**.
2. If your Google Cloud project is not listed, import it — AI Studio only shows
   projects it has been pointed at. Existing Cloud accounts have to do this
   once; it does not create a new project.
3. **Create API key**, and choose your project rather than accepting a
   generated one. Keys belong to a project, and that is what ties usage,
   billing and IAM to something you already control.
4. Copy it now. It is not shown again.

If your organisation restricts AI Studio, the same key can be created from the
Cloud console under **APIs & Services → Credentials → Create credentials → API
key**, in the project with the Gemini API enabled.

### Restrict it

An unrestricted key is a bearer token for every API in the project. In AI Studio
use **Add restrictions → Restrict to Gemini API only**; in the Cloud console,
the key's **API restrictions** tab does the same, and **Application
restrictions** can additionally pin it to an IP range for a CI runner.

Do this before the key leaves the console, not after.

### Give it to the example

```bash
export GOOGLE_API_KEY='...'
export MEMCO_API_TOKEN='...'

uv run --with memco --with langchain --with langchain-google-genai \
    python examples/langchain_agent.py
```

The SDK also reads `GEMINI_API_KEY`. If both are set, **`GOOGLE_API_KEY` wins** —
worth knowing if a stale `GEMINI_API_KEY` is sitting in your shell profile and
you cannot work out which key is being charged.

Never commit the key or paste it into a prompt. If one leaks, delete it in the
console first and create a replacement second; rotating in that order means the
window where the leaked key still works is as short as you can make it.

---

## Route 2 — Application Default Credentials, no key

For anything running on Google Cloud, prefer ADC: the credential is short-lived,
issued to the workload's own service account, and there is nothing to leak.
Vertex AI is now called the **Gemini Enterprise Agent Platform**, which is the
name to search the console and the docs for.

```bash
gcloud auth application-default login          # or attach a service account

export GOOGLE_GENAI_USE_ENTERPRISE=true
export GOOGLE_CLOUD_PROJECT='your-project-id'
export GOOGLE_CLOUD_LOCATION='us-central1'
export MEMCO_API_TOKEN='...'

uv run --with memco --with langchain --with langchain-google-genai \
    python examples/langchain_agent.py
```

No `GOOGLE_API_KEY` is needed, and setting one alongside these does nothing.

`GOOGLE_GENAI_USE_ENTERPRISE` is the current name. The SDK still reads the older
`GOOGLE_GENAI_USE_VERTEXAI`, and warns if the two are set to conflicting values
— the enterprise one wins. Set one, not both.

The service account needs the **Vertex AI User** role (`roles/aiplatform.user`)
on the project, and the API enabled:

```bash
gcloud services enable aiplatform.googleapis.com --project='your-project-id'
```

---

## Choosing a model

The example defaults to `gemini-3.7-flash`: generally available, quick, cheap
enough to run repeatedly, and reliable at calling tools, which is the whole
point of this example. Override it without touching the file:

```bash
export MEMCO_EXAMPLE_MODEL='google_genai:gemini-3.1-pro-preview'
```

The Pro models are stronger at multi-step reasoning, so a task that has the
agent search, judge what came back and then write something back to memory will
go better on one — but the 3.1 Pro line is **preview**, which means it can change
under you. Flash is the right default for an example; pick Pro deliberately.

`google_genai` is the provider prefix LangChain resolves to
`ChatGoogleGenerativeAI`; the same package serves both routes above.

---

## Not using Gemini?

Nothing about the example is Gemini-specific — it reads
`MEMCO_EXAMPLE_MODEL` and hands the tools to whatever LangChain builds:

```bash
export MEMCO_EXAMPLE_MODEL='anthropic:claude-opus-5'
export ANTHROPIC_API_KEY='...'

uv run --with memco --with langchain --with langchain-anthropic \
    python examples/langchain_agent.py
```

The Memco tools carry a JSON Schema and plain-text results, so they work the
same on any of them. `memco.agent` also speaks the Anthropic and OpenAI tool
formats directly if you would rather drive the loop yourself than use LangChain.

---

## What was verified, and when

Checked against `google-genai` 2.20.0 and `langchain-google-genai` 4.3.6 on
2026-08-27: the environment variables above are the ones the SDK actually reads,
`GOOGLE_API_KEY` does take precedence over `GEMINI_API_KEY`, and
`google_genai:gemini-3.7-flash` resolves to a tool-calling chat model.

The console steps and the September 2026 key deadline come from Google's own
documentation rather than from a run — console layouts move, so treat them as a
map rather than a script.
