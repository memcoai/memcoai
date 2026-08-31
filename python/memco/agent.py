"""The memory operations as agent tools: descriptions, schemas, rendered results.

Handing these operations to an LLM takes three things beyond the calls
themselves — text telling a model what a tool does and what to pass it, a JSON
Schema for its arguments, and results rendered as text a model can read. None of
that is specific to an agent framework, and all of it is the SDK's to get right:
the descriptions are the service's copy, the schemas are this package's own
types, and a caller who writes their own rendering has to keep it in step with
every field the contract adds.

So it lives here, reached from the session the tools are bound to, and wiring
it into a framework is the only work left::

    from memco import Memco

    with Memco() as client:
        with client.memory.with_session("coding") as session:
            for tool in session.tools():
                tool.name, tool.description, tool.parameters, tool.call

Each tool is bound to the session it was built from, so nothing a model sends
can change which session a call is recorded under. What a model sends is
untrusted: arguments are validated against the same types the schema was built
from, and a bad one is reported the way a bad request is.

``examples/langchain_agent.py`` is the whole of the LangChain wiring.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import types as _types
import typing
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

import grpc

from ._validate import NEW_MEMORY
from .errors import MemcoConfigError, MemcoInvalidRequestError, MemcoNotFoundError
from .operations import AsyncSessionScope, SessionScope
from .types import (
    DomainEntry,
    FeedbackResult,
    Instructions,
    Memory,
    RevertResult,
    SearchResult,
    WriteResult,
)

__all__ = [
    "AGENT_RECOVERABLE",
    "AsyncTool",
    "AsyncToolset",
    "Tool",
    "Toolset",
    "briefing",
    "render",
]

AGENT_RECOVERABLE: tuple[type[MemcoInvalidRequestError], type[MemcoNotFoundError]] = (
    MemcoInvalidRequestError,
    MemcoNotFoundError,
)
"""The failures a model can act on, and the only ones a tool reports as text.

A malformed request it can correct, and a handle that resolves to nothing it can
look for elsewhere. Everything else — a rejected or unscoped credential, a
timeout, an unreachable service, a spent quota — is raised, because no wording a
model reads will fix it and letting it read the failure only invites it to try
again.
"""

_AnyTool = TypeVar("_AnyTool", "Tool", "AsyncTool")

_PREFIX = "memco_"
"""Prepended to each operation's name, so the tools are recognisable as a set."""

_OPERATIONS = (
    "search",
    "get_memory",
    "create_memory",
    "enrich_memory",
    "share_feedback",
    "revert_memory",
)
"""The operations offered to a model, in the order a task uses them.

Written out rather than read off the scope. Everything a tool exposes crosses
into an untrusted model's reach, and "every public method" would put a helper
added to the scope there without anyone deciding to. ``tests/test_agent.py``
asserts this covers the scope, so an operation added and not offered is a
failure rather than an omission.
"""

_ANSWERED = ("list_domains", "start_session")
"""Operations the scope has already answered, named in the copy but not offered.

The service's tool copy is written for a caller who picks a domain and opens a
session per call, so it points at both — and it is right to, on the surface it
was written for. Bound to a session neither is a tool here, so a model following
that sentence would reach for something that does not exist. :func:`briefing`
says so instead, which is cheaper than forking the service's words.
"""

_BOUND = frozenset({"self", "timeout", "source"})
"""Parameters the caller supplies, never the model.

``source`` is deliberately among them. It records who produced the content, and
a model offered the choice could claim the content came from a person — the same
misattribution the contract already refuses for the operator source. The SDK's
default says agent, which is what is true here.
"""

_SCALARS: dict[Any, dict[str, Any]] = {
    str: {"type": "string"},
    bool: {"type": "boolean"},
}

_SCALAR_NAMES = {str: "a string", bool: "true or false"}
"""How each scalar is named to the model that got it wrong."""


@dataclasses.dataclass(frozen=True, slots=True)
class Tool:
    """One memory operation, ready to hand to an agent framework.

    Attributes:
        name: The name a model calls, such as ``memco_search``.
        description: What the operation is for, as a model should read it.
        parameters: A JSON Schema object describing the arguments, with a
            description on each. Every framework this SDK has been used with
            takes one of these directly.
        call: Runs the operation and renders the result as text. Takes the
            arguments the schema describes, by keyword.

    Example:
        >>> for tool in session.tools():
        ...     print(tool.name, tool.parameters["required"])
    """

    name: str
    description: str
    parameters: dict[str, Any]
    call: Callable[..., str]


@dataclasses.dataclass(frozen=True, slots=True)
class AsyncTool:
    """One memory operation on an asyncio client. Mirrors :class:`Tool`.

    Attributes:
        name: The name a model calls, such as ``memco_search``.
        description: What the operation is for, as a model should read it.
        parameters: A JSON Schema object describing the arguments.
        call: Runs the operation and renders the result as text, awaitably.

    Example:
        >>> for tool in await session.tools():
        ...     print(tool.name, tool.parameters["required"])
    """

    name: str
    description: str
    parameters: dict[str, Any]
    call: Callable[..., Awaitable[str]]


def _anthropic(tool: Tool | AsyncTool) -> dict[str, Any]:
    """Describe one tool the way the Anthropic Messages API takes it.

    Args:
        tool: The tool to describe.

    Returns:
        A tool definition, for the ``tools`` argument of a message request.
    """
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


def _openai(tool: Tool | AsyncTool) -> dict[str, Any]:
    """Describe one tool the way the OpenAI function-calling API takes it.

    Args:
        tool: The tool to describe.

    Returns:
        A tool definition, for the ``tools`` argument of a completion request.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _structured_tool() -> type[Any]:
    """Import LangChain's tool class, saying what to install when it is absent.

    Returns:
        ``langchain_core.tools.StructuredTool``.

    Raises:
        ImportError: If LangChain is not installed.
    """
    try:
        # Imported here on purpose: LangChain is not a dependency of this SDK,
        # and a caller using another framework must not be made to install it.
        from langchain_core.tools import StructuredTool  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - needs LangChain absent
        raise ImportError("to_langchain() needs LangChain: pip install langchain") from exc
    return StructuredTool


def _langchain(tool: Tool) -> Any:  # noqa: ANN401 - LangChain is not a dependency to name
    """Build one LangChain tool.

    Args:
        tool: The tool to hand over.

    Returns:
        A ``langchain_core.tools.StructuredTool``.
    """
    return _structured_tool().from_function(
        func=tool.call,
        name=tool.name,
        description=tool.description,
        args_schema=tool.parameters,
    )


def _langchain_async(tool: AsyncTool) -> Any:  # noqa: ANN401 - as above
    """Build one LangChain tool bound as a coroutine.

    Args:
        tool: The tool to hand over.

    Returns:
        A ``langchain_core.tools.StructuredTool``, reached with ``ainvoke``.
    """
    return _structured_tool().from_function(
        coroutine=tool.call,
        name=tool.name,
        description=tool.description,
        args_schema=tool.parameters,
    )


def _sent(arguments: str | Mapping[str, object]) -> dict[str, object]:
    """Normalise the arguments a framework handed back from the model.

    OpenAI delivers ``tool_call.function.arguments`` as a **JSON string**, so a
    caller following :meth:`Toolset.to_openai` passes one here; Anthropic
    delivers an object. Both are accepted, and anything else is reported rather
    than splatted, because ``**"…"`` is a TypeError that ends the run.

    Args:
        arguments: What the model sent, as an object or as JSON text.

    Returns:
        The arguments as a mapping of string keys.

    Raises:
        MemcoInvalidRequestError: If the text is not JSON, if it does not
            describe an object, or if a key is not a string.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise MemcoInvalidRequestError(
                grpc.StatusCode.INVALID_ARGUMENT, f"arguments are not valid JSON: {exc}"
            ) from exc
    if not isinstance(arguments, Mapping):
        raise MemcoInvalidRequestError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"arguments must be an object, not {type(arguments).__name__}",
        )
    unnamed = [key for key in arguments if not isinstance(key, str)]
    if unnamed:
        raise MemcoInvalidRequestError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"argument names must be strings: {', '.join(repr(key) for key in unnamed)}",
        )
    return dict(arguments)


def _dispatch(by_name: dict[str, _AnyTool], name: str) -> _AnyTool:
    """Find the tool a model asked for.

    Args:
        by_name: The toolset, by name.
        name: The name the model used, which is untrusted.

    Returns:
        The tool.

    Raises:
        MemcoInvalidRequestError: If no tool goes by that name. A model that
            invented one can pick again, so this is reported the way any other
            correctable mistake is.
    """
    tool = by_name.get(name)
    if tool is None:
        raise MemcoInvalidRequestError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"no tool named {name!r}; there is {', '.join(sorted(by_name))}",
        )
    return tool


class Toolset(tuple[Tool, ...]):
    """Every memory operation as a tool, in the shape a framework wants it.

    A tuple of :class:`Tool`, so it iterates and indexes like one. The
    ``to_*`` methods hand the same tools to a particular framework, and
    :meth:`call` runs the one a model named — which is what the definition-only
    shapes need, since there the host dispatches rather than the framework.

    Example:
        >>> toolset = session.tools()
        >>> runnable = create_agent(model, tools=toolset.to_langchain())
        >>> # or, driving the loop yourself:
        >>> response = anthropic.messages.create(tools=toolset.to_anthropic(), ...)
        >>> toolset.call(block.name, block.input)
    """

    def to_langchain(self) -> list[Any]:
        """Hand these tools to LangChain.

        LangChain takes a JSON Schema directly, so nothing is restated.

        Returns:
            One ``langchain_core.tools.StructuredTool`` per operation, ready
            for ``create_agent(tools=...)``.

        Raises:
            ImportError: If LangChain is not installed.

        Example:
            >>> create_agent(model, tools=session.tools().to_langchain())
        """
        return [_langchain(tool) for tool in self]

    def to_anthropic(self) -> list[dict[str, Any]]:
        """Describe these tools for the Anthropic Messages API.

        Definitions only — pair them with :meth:`call` to run what the model
        asks for.

        Returns:
            One tool definition per operation.

        Example:
            >>> client.messages.create(tools=session.tools().to_anthropic(), ...)
        """
        return [_anthropic(tool) for tool in self]

    def to_openai(self) -> list[dict[str, Any]]:
        """Describe these tools for the OpenAI Chat Completions API.

        Definitions only — pair them with :meth:`call` to run what the model
        asks for, which also takes the JSON string that API hands back.
        Anything speaking the same shape takes these too; the Responses API
        wants a flatter one and is not what this emits.

        Returns:
            One tool definition per operation.

        Example:
            >>> client.chat.completions.create(tools=session.tools().to_openai(), ...)
        """
        return [_openai(tool) for tool in self]

    def call(self, name: str, arguments: str | Mapping[str, object]) -> str:
        """Run the tool a model named, and render what it returned.

        Args:
            name: The tool name the model used, which is untrusted.
            arguments: The arguments it sent, which are untrusted. An object, or
                the JSON text OpenAI hands back.

        Returns:
            The result as text, or what the model got wrong. Nothing a model can
            send reaches the caller as an exception.

        Example:
            >>> toolset.call("memco_search", {"query": "how does X work"})
            >>> toolset.call(call.function.name, call.function.arguments)  # OpenAI
        """
        try:
            tool = _dispatch({one.name: one for one in self}, name)
            return tool.call(**_sent(arguments))
        except AGENT_RECOVERABLE as exc:
            return _reported(exc)


class AsyncToolset(tuple[AsyncTool, ...]):
    """Every memory operation as an awaitable tool. Mirrors :class:`Toolset`.

    Example:
        >>> toolset = session.tools()
        >>> runnable = create_agent(model, tools=toolset.to_langchain())
    """

    def to_langchain(self) -> list[Any]:
        """Hand these tools to LangChain, bound as coroutines.

        Returns:
            One ``langchain_core.tools.StructuredTool`` per operation, each
            reached with ``ainvoke``.

        Raises:
            ImportError: If LangChain is not installed.

        Example:
            >>> create_agent(model, tools=session.tools().to_langchain())
        """
        return [_langchain_async(tool) for tool in self]

    def to_anthropic(self) -> list[dict[str, Any]]:
        """Describe these tools for the Anthropic Messages API.

        Returns:
            One tool definition per operation, identical to the synchronous
            ones: a definition says nothing about how the host calls it.

        Example:
            >>> await client.messages.create(tools=toolset.to_anthropic(), ...)
        """
        return [_anthropic(tool) for tool in self]

    def to_openai(self) -> list[dict[str, Any]]:
        """Describe these tools for the OpenAI function-calling API.

        Returns:
            One tool definition per operation.

        Example:
            >>> await client.chat.completions.create(tools=toolset.to_openai(), ...)
        """
        return [_openai(tool) for tool in self]

    async def call(self, name: str, arguments: str | Mapping[str, object]) -> str:
        """Run the tool a model named, and render what it returned.

        Args:
            name: The tool name the model used, which is untrusted.
            arguments: The arguments it sent, which are untrusted. An object, or
                the JSON text OpenAI hands back.

        Returns:
            The result as text, or what the model got wrong. Nothing a model can
            send reaches the caller as an exception.

        Example:
            >>> await toolset.call("memco_search", {"query": "how does X work"})
        """
        try:
            tool = _dispatch({one.name: one for one in self}, name)
            return await tool.call(**_sent(arguments))
        except AGENT_RECOVERABLE as exc:
            return _reported(exc)


def _tools(session: SessionScope) -> Toolset:
    """Build every memory operation as a tool bound to one open session.

    Args:
        session: The open session every call through these tools is recorded
            under, as returned by ``client.memory.with_session(...)``.

    Returns:
        One tool per operation the session scope carries.

    Raises:
        TypeError: If given the other surface's scope.
        MemcoConfigError: If this package's docstrings are unavailable, which is
            what running Python with ``-OO`` does. Every description is derived
            from them, and tools carrying none steer a model by nothing.

    Example:
        >>> with client.memory.with_session("coding") as session:
        ...     [tool.name for tool in session.tools()]
    """
    if not isinstance(session, SessionScope):
        raise TypeError(f"tools() takes a SessionScope; {type(session).__name__} has its own")
    return Toolset(
        Tool(
            name=_PREFIX + name,
            description=description,
            parameters=parameters,
            call=_sync_call(name, getattr(session, name), signature, parameters["required"]),
        )
        for name, description, parameters, signature in _specifications()
    )


def _async_tools(session: AsyncSessionScope) -> AsyncToolset:
    """Build every memory operation as an awaitable tool bound to one session.

    Args:
        session: The open session every call through these tools is recorded
            under, as returned by ``client.memory.with_session(...)``.

    Returns:
        One tool per operation the session scope carries.

    Raises:
        TypeError: If given the other surface's scope.
        MemcoConfigError: If this package's docstrings are unavailable, which is
            what running Python with ``-OO`` does. Every description is derived
            from them, and tools carrying none steer a model by nothing.

    Example:
        >>> async with client.memory.with_session("coding") as session:
        ...     [tool.name for tool in session.tools()]
    """
    if not isinstance(session, AsyncSessionScope):
        raise TypeError(f"tools() takes an AsyncSessionScope; {type(session).__name__} has its own")
    return AsyncToolset(
        AsyncTool(
            name=_PREFIX + name,
            description=description,
            parameters=parameters,
            call=_async_call(name, getattr(session, name), signature, parameters["required"]),
        )
        for name, description, parameters, signature in _specifications()
    )


# -- rendering ------------------------------------------------------------


def render(value: SearchResult | Memory | WriteResult | FeedbackResult | RevertResult) -> str:
    """Render an operation's result as text a model can read.

    Every result carries :class:`~memco.types.Instructions` — the service's own
    guidance on what to do with what came back — and that is included, because a
    model that never sees it is left to guess.

    Args:
        value: Any result a memory operation returns.

    Returns:
        The result as text.

    Raises:
        TypeError: If the value is not a result type this renders.

    Example:
        >>> print(agent.render(session.search("how does X work")))
    """
    renderer = _RENDERERS.get(type(value))
    if renderer is None:
        raise TypeError(f"no rendering for {type(value).__name__}")
    return renderer(value)


def briefing(domain: DomainEntry, instructions: Instructions) -> str:
    """Render what the service says about a domain, for a model to be told.

    Every word of this comes from :meth:`~memco.operations.MemoryOperations.list_domains`
    and from opening the session — what the domain holds, when to draw on it,
    what belongs in it, and the tag vocabulary it uses. Supply it up front
    rather than leaving a model to ask: guidance behind a tool only steers the
    models that reach for it.

    Args:
        domain: The domain the session was opened in, from ``list_domains``.
        instructions: What the service said when the session was opened, as
            carried by the scope's ``instructions``.

    Returns:
        The guidance as text, ready to be a system prompt or part of one.

    Example:
        >>> entry = next(d for d in client.memory.list_domains().domains
        ...              if d.slug == "coding")
        >>> with client.memory.with_session("coding") as session:
        ...     print(agent.briefing(entry, session.instructions))
    """
    parts = [
        (
            f"You share a persistent memory with your team, in the {domain.slug!r} domain "
            f"({domain.title}). Search it before working anything out from scratch, and "
            f"save what you learn so nobody has to learn it twice."
        ),
        domain.summary,
        f"When to search: {domain.when_to_search}" if domain.when_to_search else "",
        f"When to save: {domain.when_to_save}" if domain.when_to_save else "",
        f"What not to save: {domain.what_not_to_save}" if domain.what_not_to_save else "",
        domain.tags_description,
    ]
    if domain.filter_tag_types:
        parts.append(
            "These tag types narrow the results rather than boosting them, so a wrong "
            f"one returns nothing: {', '.join(domain.filter_tag_types)}."
        )
    if domain.version_tag_types:
        parts.append(
            f"These carry a version, and one on any other type is dropped: "
            f"{', '.join(domain.version_tag_types)}."
        )
    if domain.max_tags_per_query:
        parts.append(f"At most {domain.max_tags_per_query} tags per call.")
    parts.append(
        f"The domain and the session are already chosen, so {' and '.join(_ANSWERED)} are "
        "not among your tools: where a tool's description names one, what it would have "
        "told you is above."
    )
    parts.append(
        f"Pass {NEW_MEMORY!r} as memory_idx to {_PREFIX}enrich_memory to open a new memory."
    )
    parts.append(instructions.content)
    return _join(*parts)


def _join(*parts: str) -> str:
    """Join rendered sections, dropping the ones that carried nothing.

    Args:
        parts: Sections in the order a model should read them.

    Returns:
        The sections that had content.
    """
    return "\n\n".join(part for part in parts if part)


def _instructions(instructions: Instructions) -> str:
    """Render the guidance a response carried.

    Args:
        instructions: The guidance attached to one response.

    Returns:
        The guidance as text, empty when the response carried none.
    """
    return _join(
        instructions.content,
        instructions.policy,
        instructions.adding,
        instructions.rating,
        instructions.next,
    )


def _memory(memory: Memory) -> str:
    """Render one memory and its insights.

    Args:
        memory: The memory to render.

    Returns:
        The memory as indented text.
    """
    if memory.reference:
        return (
            f"{memory.idx}  already returned in this session as {memory.reference}; "
            f"fetch it again with {_PREFIX}get_memory"
        )
    heading = f"{memory.idx}  served {memory.times_served}x"
    if memory.kind:
        heading += f"  ({memory.kind})"
    lines = [heading]
    if memory.intents:
        lines.append("  retrieved before by: " + "; ".join(memory.intents))
    for insight in memory.insights:
        # endorsed and disputed are the reliability signal times_served is not,
        # so a model that never sees them cannot discount a disputed insight.
        marks = [f"served {insight.times_served}x"]
        if insight.endorsed:
            marks.append(f"endorsed {insight.endorsed}x")
        if insight.disputed:
            marks.append(f"disputed {insight.disputed}x")
        written = f"updated {insight.updated}" if insight.updated else "never updated"
        lines.append(
            f"  {insight.idx}  {insight.title}\n"
            f"    [{written}, {', '.join(marks)}]\n"
            f"    {insight.content}"
        )
    return "\n".join(lines)


def _search_result(result: SearchResult) -> str:
    """Render what a search selected.

    Args:
        result: The search result.

    Returns:
        The memories and the guidance that came with them.
    """
    found = len(result.memories)
    return _join(
        _join(f"{found} {'memory' if found == 1 else 'memories'}", result.notice or ""),
        "\n\n".join(_memory(memory) for memory in result.memories),
        _instructions(result.instructions),
    )


def _write_result(result: WriteResult) -> str:
    """Render an accepted write.

    Args:
        result: The write result.

    Returns:
        The operation id, and the guidance that came with it.
    """
    accepted = (
        f"accepted as operation {result.operation_id}"
        if result.operation_id
        else "accepted; this write cannot be undone"
    )
    return _join(accepted, _instructions(result.instructions))


def _feedback_result(result: FeedbackResult) -> str:
    """Render what ratings were recorded.

    Args:
        result: The feedback result.

    Returns:
        The ratings, and any advice they earned.
    """
    recorded = "\n".join(
        f"{entry.idx}  relevant={entry.relevant} correct={entry.correct}"
        + (f"  {entry.advice}" if entry.advice else "")
        for entry in result.entries
    )
    return _join(recorded, _instructions(result.instructions))


def _revert_result(result: RevertResult) -> str:
    """Render what a revert removed.

    Args:
        result: The revert result.

    Returns:
        The outcome, which is reported rather than raised.
    """
    return _join(
        f"{result.operation_id}: {result.outcome.name.lower().replace('_', ' ')}",
        _instructions(result.instructions),
    )


_RENDERERS: dict[type, Callable[[Any], str]] = {
    SearchResult: _search_result,
    Memory: _memory,
    WriteResult: _write_result,
    FeedbackResult: _feedback_result,
    RevertResult: _revert_result,
}


# -- descriptions ---------------------------------------------------------

_ROLE = re.compile(r":[a-z]+:`~?([^`]+)`")
_LITERAL = re.compile(r"``([^`]+)``")
_SECTION = re.compile(r"^(Args|Attributes|Returns|Raises|Example):$")
_ARGUMENT = re.compile(r"^    (\w+): (.*)$")


def _reference(target: str) -> str:
    """Name what a cross-reference points at, as a model can use it.

    A reference to an operation becomes that tool's name. Telling a model to
    "rate it with share_feedback" names a tool that does not exist, and it has
    no other way to find the one that does. Anything else keeps only its last
    component, since a model has no use for a module path.

    Args:
        target: The role's target, such as ``memco.types.DataSource.AGENT``.

    Returns:
        The target as prose.
    """
    last = target.rsplit(".", 1)[-1]
    return _PREFIX + last if last in _OPERATIONS else last


def _plain(text: str) -> str:
    """Flatten Sphinx markup, which means nothing to a model.

    Args:
        text: Docstring prose, possibly carrying roles and literals.

    Returns:
        The same prose as plain text.
    """
    text = _ROLE.sub(lambda match: _reference(match.group(1)), text)
    return _LITERAL.sub(r"\1", text)


def _reflowed(lines: Sequence[str]) -> str:
    """Undo the source's column wrapping, so sentences read as sentences.

    Docstrings are wrapped to the lint's line length, which puts breaks mid
    sentence. Joining each paragraph's lines restores them.

    Args:
        lines: The lines of one docstring section.

    Returns:
        The same prose, broken only at blank lines.
    """
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            paragraphs[-1].append(line.strip())
        elif paragraphs[-1]:
            paragraphs.append([])
    return "\n\n".join(" ".join(paragraph) for paragraph in paragraphs if paragraph)


def _documented(lines: Sequence[str], section: str) -> dict[str, list[str]]:
    """Collect one docstring section's entries, by name.

    Args:
        lines: The lines of a docstring.
        section: The section to read: ``Args`` for a method's parameters,
            ``Attributes`` for a dataclass's fields.

    Returns:
        Each documented name, and the lines describing it.
    """
    collected: dict[str, list[str]] = {}
    current: list[str] | None = None
    inside = False
    for line in lines:
        header = _SECTION.match(line)
        if header:
            inside = header.group(1) == section
            current = None
        elif inside:
            entry = _ARGUMENT.match(line)
            if entry:
                current = collected.setdefault(entry.group(1), [entry.group(2)])
            elif current is not None and line.strip():
                current.append(line.strip())
    return collected


def _describe(method: Callable[..., object], name: str) -> tuple[str, dict[str, str]]:
    """Render an agent-facing description from an operation's docstring.

    This copy belongs to the service — it is what steers a model, and on most
    surfaces a tool's description is the only text that reliably reaches one. It
    arrives with every export in ``memco/memory/tools.json``, the same copy the
    hosted MCP server publishes, and ``scripts/sync_tool_docs.py`` writes it into
    the docstrings this reads. So the manifest is the source and these
    docstrings are its checked-in rendering, which is why the description a
    model is handed is reviewable in a diff rather than assembled at import.

    Reading the docstring rather than the manifest is deliberate: it keeps the
    package free of a data file it would have to find at runtime, and it means
    the reference a developer reads and the description a model reads cannot
    say different things. ``tests/test_tool_copy.py`` holds them to that.

    Args:
        method: The session-scope method being described.
        name: Its name, for the error message.

    Returns:
        The description, and one description per documented parameter.

    Raises:
        MemcoConfigError: If the docstring is unavailable, which happens when
            Python is run with ``-OO``. An agent whose tools carry no
            description is steered by nothing, and would fail silently.
    """
    lines = (inspect.getdoc(method) or "").splitlines()

    # Everything before the first section says what the operation is for.
    # Returns, Raises and Example are for a developer reading the reference,
    # not for a model choosing a tool.
    summary: list[str] = []
    for line in lines:
        if _SECTION.match(line):
            break
        summary.append(line)

    description = _plain(_reflowed(summary))
    if not description:
        raise MemcoConfigError(
            f"no description available for {name}: this package's docstrings are missing, "
            "which is what running Python with -OO does"
        )
    return description, {
        argument: _plain(" ".join(text)) for argument, text in _documented(lines, "Args").items()
    }


# -- schemas and argument validation --------------------------------------


def _optional(annotation: object) -> tuple[object, bool]:
    """Split ``X | None`` into ``X`` and whether the ``None`` was there.

    Args:
        annotation: A resolved type annotation.

    Returns:
        The annotation without its ``None``, and whether one was removed.
    """
    if get_origin(annotation) in (typing.Union, _types.UnionType):
        present = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(present) == 1:
            return present[0], True
    return annotation, False


def _fields(cls: type) -> dict[str, tuple[type, bool]]:
    """Describe one of this package's own dataclasses, field by field.

    Derived rather than written out, so a field added to
    :class:`~memco.types.Tag` or :class:`~memco.types.FeedbackRating` reaches
    the model — and is checked on the way back in — without anyone remembering
    to update a second copy of its shape.

    Args:
        cls: The dataclass to describe.

    Returns:
        Each field's scalar type and whether it must be supplied.

    Raises:
        TypeError: If a field's type has no scalar mapping, which means the
            shape outgrew what a model can be asked to send.
    """
    hints = get_type_hints(cls)
    described: dict[str, tuple[type, bool]] = {}
    for field in dataclasses.fields(cls):
        scalar, nullable = _optional(hints[field.name])
        if not isinstance(scalar, type) or scalar not in _SCALARS:
            raise TypeError(f"{cls.__name__}.{field.name} has no schema for {scalar!r}")
        described[field.name] = (
            scalar,
            field.default is dataclasses.MISSING and not nullable,
        )
    return described


def _object_schema(cls: type) -> dict[str, Any]:
    """Describe one of this package's own dataclasses as a JSON Schema object.

    The field descriptions come from the class's own ``Attributes:`` block,
    which is where a model learns that most tag types carry no version and
    which of relevant and correct means what.

    Args:
        cls: The dataclass to describe.

    Returns:
        A JSON Schema object.
    """
    documented = _documented((inspect.getdoc(cls) or "").splitlines(), "Attributes")
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, (scalar, needed) in _fields(cls).items():
        # Copied: a framework that normalises schemas in place would
        # otherwise reach through and corrupt every schema in the process.
        described = dict(_SCALARS[scalar])
        text = _plain(" ".join(documented.get(name, [])))
        if text:
            described["description"] = text
        properties[name] = described
        if needed:
            required.append(name)
    # additionalProperties tells a model exactly what it may send. What arrives
    # anyway is ignored rather than refused: an invented key costs nothing, a
    # rejected call costs a turn. This is a statement of the shape, not a claim
    # to satisfy any vendor's strict mode — the top-level schema makes no such
    # declaration, so neither level would qualify.
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _scalar(expected: type, value: object, where: str) -> object:
    """Check one value a model sent against the type the schema promised.

    A model that sends a number where the schema said string is making the same
    kind of mistake as one that omits a field, and has to hear about it the same
    way: unchecked, the wrong type reaches validation as an AttributeError and
    ends the run instead of the turn.

    Args:
        expected: The scalar type the schema declared.
        value: What arrived, which is untrusted.
        where: The argument or field it arrived under, for the message.

    Returns:
        The value, unchanged.

    Raises:
        MemcoInvalidRequestError: If it is not of the declared type. ``bool`` and
            ``str`` are exclusive under isinstance, so no scalar is mistaken for
            another.
    """
    if not isinstance(value, expected):
        raise MemcoInvalidRequestError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"{where} must be {_SCALAR_NAMES[expected]}, not {type(value).__name__}",
        )
    return value


def _schema(annotation: object, description: str) -> dict[str, Any]:
    """Describe one parameter as JSON Schema.

    Args:
        annotation: The parameter's resolved type annotation.
        description: What the operation's docstring says about it.

    Returns:
        A JSON Schema fragment carrying the description.

    Raises:
        TypeError: If the annotation is not one this maps, which is a new
            parameter shape needing a decision rather than a default.
    """
    inner, _ = _optional(annotation)
    if inner in _SCALARS:
        return {**_SCALARS[inner], "description": description}
    if get_origin(inner) is Sequence:
        (item,) = get_args(inner)
        items = dict(_SCALARS[item]) if item in _SCALARS else _object_schema(item)
        return {"type": "array", "items": items, "description": description}
    raise TypeError(f"no schema for parameter type {annotation!r}")


def _build(cls: type, value: object, argument: str) -> object:
    """Turn one object a model sent into the type the operation takes.

    Args:
        cls: The dataclass to build.
        value: What arrived, which is untrusted.
        argument: The parameter it arrived under, for the error message.

    Returns:
        The built instance.

    Raises:
        MemcoInvalidRequestError: If it is not an object, or a required key is
            missing. Both are things the model can correct.
    """
    if not isinstance(value, dict):
        raise MemcoInvalidRequestError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"{argument} must hold objects, not {type(value).__name__}",
        )
    described = _fields(cls)
    missing = [name for name, (_, needed) in described.items() if needed and name not in value]
    if missing:
        raise MemcoInvalidRequestError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"{argument} is missing {', '.join(missing)}",
        )
    supplied = {}
    for name, (scalar, needed) in described.items():
        if name not in value:
            continue
        item = value[name]
        # An optional field explicitly set to null is the same as leaving it out.
        if item is None and not needed:
            continue
        supplied[name] = _scalar(scalar, item, f"{argument}.{name}")
    return cls(**supplied)


def _coerce(annotation: object, value: object, argument: str) -> object:
    """Turn one argument a model sent into what the operation takes.

    Args:
        annotation: The parameter's resolved type annotation.
        value: What arrived, which is untrusted.
        argument: The parameter name, for the error message.

    Returns:
        The value, converted where the operation takes one of this package's
        own types rather than a scalar.

    Raises:
        MemcoInvalidRequestError: If the shape is wrong.
    """
    inner, _ = _optional(annotation)
    if value is None:
        return value
    if isinstance(inner, type) and inner in _SCALARS:
        return _scalar(inner, value, argument)
    if get_origin(inner) is Sequence:
        (item,) = get_args(inner)
        if not isinstance(value, list):
            raise MemcoInvalidRequestError(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"{argument} must be a list, not {type(value).__name__}",
            )
        if isinstance(item, type) and item in _SCALARS:
            return [_scalar(item, entry, f"{argument}[{at}]") for at, entry in enumerate(value)]
        return [_build(item, entry, argument) for entry in value]
    return value


def _specifications() -> tuple[tuple[str, str, dict[str, Any], dict[str, object]], ...]:
    """Describe every operation the session scope carries.

    Derived from the scope itself, so an operation added there becomes a tool
    without a second list to remember.

    Returns:
        One ``(name, description, parameters, annotations)`` per operation.
    """
    built = []
    for name in _OPERATIONS:
        method = getattr(SessionScope, name)
        description, documented = _describe(method, name)
        hints = get_type_hints(method)
        signature = inspect.signature(method)

        properties: dict[str, Any] = {}
        required: list[str] = []
        annotations: dict[str, object] = {}
        for argument, parameter in signature.parameters.items():
            if argument in _BOUND:
                continue
            annotations[argument] = hints[argument]
            properties[argument] = _schema(hints[argument], documented.get(argument, ""))
            if parameter.default is inspect.Parameter.empty:
                required.append(argument)

        built.append(
            (
                name,
                description,
                {"type": "object", "properties": properties, "required": required},
                annotations,
            )
        )
    return tuple(built)


def _arguments(
    annotations: dict[str, object], required: Sequence[str], sent: dict[str, object]
) -> dict[str, object]:
    """Convert what a model sent into what the operation takes.

    The schema says which arguments are required, and the required list is
    checked here rather than trusted: a framework handed a plain JSON Schema
    may not enforce it, and an operation called without one raises a TypeError
    that would escape as a crash instead of reaching the model as a correction.

    Args:
        annotations: The parameter annotations, by name.
        required: The arguments the operation cannot be called without.
        sent: The arguments as they arrived, which are untrusted.

    Returns:
        The arguments, converted.

    Raises:
        MemcoInvalidRequestError: If a required argument is absent, an
            argument's shape is wrong, or one names a parameter the operation
            does not take.
    """
    unknown = sorted(set(sent) - set(annotations))
    if unknown:
        raise MemcoInvalidRequestError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"unknown argument(s): {', '.join(unknown)}",
        )
    missing = [name for name in required if sent.get(name) is None]
    if missing:
        raise MemcoInvalidRequestError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"missing required argument(s): {', '.join(missing)}",
        )
    return {name: _coerce(annotations[name], value, name) for name, value in sent.items()}


def _sync_call(
    name: str,
    method: Callable[..., Any],
    annotations: dict[str, object],
    required: Sequence[str],
) -> Callable[..., str]:
    """Bind one operation as a tool body.

    Args:
        name: The operation's name, which the bound callable takes.
        method: The bound session-scope method.
        annotations: The parameter annotations, by name.
        required: The arguments the operation cannot be called without.

    Returns:
        A callable taking the arguments the schema describes and returning text.
    """

    def call(**sent: object) -> str:
        try:
            return render(method(**_arguments(annotations, required, sent)))
        except AGENT_RECOVERABLE as exc:
            return _reported(exc)

    # Frameworks that introspect the callable get the tool's name rather than
    # six identical "call"s.
    call.__name__ = _PREFIX + name
    return call


def _async_call(
    name: str,
    method: Callable[..., Awaitable[Any]],
    annotations: dict[str, object],
    required: Sequence[str],
) -> Callable[..., Awaitable[str]]:
    """Bind one operation as an awaitable tool body.

    Args:
        name: The operation's name, which the bound callable takes.
        method: The bound session-scope method.
        annotations: The parameter annotations, by name.
        required: The arguments the operation cannot be called without.

    Returns:
        A coroutine function taking the arguments the schema describes.
    """

    async def call(**sent: object) -> str:
        try:
            return render(await method(**_arguments(annotations, required, sent)))
        except AGENT_RECOVERABLE as exc:
            return _reported(exc)

    call.__name__ = _PREFIX + name
    return call


def _reported(exc: MemcoInvalidRequestError | MemcoNotFoundError) -> str:
    """Word a recoverable failure for the model that caused it.

    Args:
        exc: The failure, one of :data:`AGENT_RECOVERABLE`.

    Returns:
        What went wrong, as text.
    """
    if isinstance(exc, MemcoNotFoundError):
        return f"nothing found: {exc.message}"
    # Not "nothing was recorded": the SDK rejects some of these itself, but a
    # rejection from the service is the service's to describe, and what it did
    # or did not write is not something a client can assert on its behalf.
    return f"invalid request: {exc.message}"
