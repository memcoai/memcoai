#!/usr/bin/env python3
"""Publish one language's reference as markdown, beside the HTML.

The documentation site serves each language's built reference verbatim at
``/sdk/<language>/<version>/`` and again at ``/sdk/<language>/latest/``, so
anything written into the build output is published at both paths. This writes
three kinds of thing there, none of them in place of the HTML:

each page, as markdown
    Beside its HTML and under the same name, so ``clients.html`` has its text
    at ``clients.md``, and an agent holding a page's URL reaches the text by
    swapping the extension. These link to each other, so the markdown can be
    crawled without leaving it — with the fragment dropped where the generator
    writes cross-references its own markdown has no anchors for; see
    :attr:`Reference.markdown_anchors`.

``llms.txt``
    The index described by llmstxt.org: an H1 name, a one-line summary, and a
    link list naming every one of those markdown pages.

``llms-full.txt``
    All of them concatenated into one document, for a reader that would rather
    fetch once than crawl. Its links point at the HTML instead, because they
    carry the anchors that name one symbol on a page and only the HTML has
    anchors to land on.

Both are built from a markdown rendering of the *same* sources as the HTML —
``sphinx-build -b markdown`` for Python, ``typedoc-plugin-markdown`` for
Node — so what is published cannot describe a different API from the HTML.

Each language's page order comes from whatever that generator already treats as
the site's own table of contents — the ``toctree`` in ``index.rst``, the
``globals.md`` index TypeDoc emits — rather than from a list kept here, which
would be one more thing to update when a page is added. What is checked here is
coverage: every HTML page the build produced must be reachable from
``llms.txt``, so a page that stops being indexed fails the build rather than
quietly disappearing from what an agent can find; and every link written into
a published file must reach something.

Nothing published here is committed; each language's ``make docs`` writes all
of it. Standard library only, so it runs anywhere without installing anything.

Usage::

    python3 scripts/build_llms_txt.py python
    python3 scripts/build_llms_txt.py nodejs
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent

DOCS_SITE = "https://docs.memco.ai/sdk"
"""Where the reference is published. ``<language>/latest/`` is appended.

Not overridable, deliberately. ``python/docs/conf.py`` reads
``MEMCO_DOCS_BASEURL`` for the canonical link it puts in every page, but that
names one language's reference and this script serves both — a job-level
setting of it would have the Node.js build advertise the Python URL.
"""


@dataclass(frozen=True)
class Page:
    """One page of the reference: its markdown, and where it is served."""

    title: str
    """Heading the page carries, and the name it is listed under."""

    group: str
    """Heading in ``llms.txt`` this page is listed beneath."""

    href: str
    """The page's HTML, relative to the root of the reference."""

    source: str
    """The page's markdown, relative to the root of the markdown rendering.

    Also the key a cross-page link resolves through, so a link to this page in
    another page's markdown becomes a link to :attr:`href`.
    """


@dataclass(frozen=True)
class Reference:
    """One language's built reference, ready to assemble."""

    title: str
    summary: str
    html: Path
    markdown: Path
    pages: tuple[Page, ...]
    markdown_anchors: bool
    """Whether this generator's markdown carries the anchors its own links name.

    TypeDoc's markdown anchors are the slugs of its own headings, so a link to
    ``#connect`` lands where it says. sphinx-markdown-builder writes none at
    all while still emitting the HTML domain's ids as fragments, so a link to
    ``errors.md#memco.errors.MemcoNotFoundError`` names a file that has never
    heard of it. Where that is so, a link between published markdown pages
    keeps the page and drops the fragment: the right page and no position
    beats a position that does not exist. The reader wanting the anchor has
    the HTML, which ``llms-full.txt`` links throughout.
    """

    rubric_level: int
    """The heading level this generator pins a rubric to, or 0 for none.

    Naming it is what keeps the repair below off a real page section that
    happens to share a rubric's name: only a heading at exactly this level can
    be one of these rubrics, and Sphinx renders the sections of a document at
    levels one and two, never at three.
    """

    rubrics: frozenset[str]
    """Section names this generator emits at a heading level that does not nest.

    sphinx-markdown-builder pins a rubric — what napoleon renders an
    ``Example:`` as — at a fixed level however deep the thing it belongs to is,
    so a method's example lands *above* the method and every member after it
    reads as part of that example. TypeDoc has no such quirk and emits every
    heading at its true depth, so its set is empty: applying the repair there
    would push a section under the sibling before it, which is the same
    inversion the other way round.
    """

    unlisted: frozenset[str]
    """HTML ``llms.txt`` deliberately does not link, by name or by directory.

    Navigation and apparatus the generator emits for a browser, carrying no
    documentation of its own. Everything else must be reachable. An entry
    ending in ``/`` excuses the whole directory beneath it.
    """


# ---- reading what the generators already wrote --------------------------


def fail(message: str) -> NoReturn:
    """Stop with a message on stderr."""
    print(f"build_llms_txt: {message}", file=sys.stderr)
    raise SystemExit(1)


def shown(path: Path) -> str:
    """Name a path the way a reader of the message would recognise it.

    Relative to the repository where it is under it, and absolute where it is
    not — ``Path.relative_to`` raises otherwise, and a message that cannot be
    printed is worse than one that is long.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read(path: Path) -> str:
    """Read a UTF-8 text file, failing with the path when it is missing."""
    try:
        return path.read_text("utf-8")
    except FileNotFoundError:
        fail(f"{shown(path)} does not exist; build the documentation first")


def project_table(pyproject: Path) -> dict[str, str]:
    """Read the string values of ``[project]`` out of a pyproject.toml.

    A regex rather than ``tomllib``, which arrived in 3.11 while this SDK still
    supports 3.10 — and this script is meant to run under whatever interpreter
    is on the machine, not the one ``uv`` resolved. Scoped to the ``[project]``
    table so a key that also appears in a tool's table cannot answer for it.
    """
    text = read(pyproject)
    # `\Z` as well as the next table header: [project] is not required to have
    # anything after it, and a pattern that insists on one silently reports the
    # table as absent when it happens to be last.
    table = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if table is None:
        fail(f"{shown(pyproject)} has no [project] table")
    # Both quote styles TOML allows for a single-line string. A triple-quoted
    # value matches nothing rather than matching the empty string inside it,
    # so a key written that way is reported missing instead of read as blank.
    entries = re.findall(
        r"""^([A-Za-z0-9_-]+)\s*=\s*(?:"([^"\n]*)"(?!")|'([^'\n]*)'(?!'))\s*$""",
        table.group(1),
        re.M,
    )
    return {key: double or single for key, double, single in entries}


def toctree(index_rst: Path) -> list[str]:
    """List the documents an ``index.rst`` toctree names, in order.

    The toctree is where the reference's page order is actually decided, so
    reading it is what keeps a page added there from being missing here.
    """
    documents: list[str] = []
    # A directive's body is whatever is indented past the directive itself, so
    # the toctree's own column is what ends it — not column zero, which would
    # swallow the rest of any directive the toctree is nested inside.
    directive = -1
    option = -1
    for line in read(index_rst).splitlines():
        entry = line.strip()
        indent = len(line) - len(line.lstrip())
        if entry == ".. toctree::":
            directive, option = indent + 1, -1
            continue
        if directive < 0 or not entry:
            continue
        if indent < directive:
            directive = -1  # a dedent to the directive's own column or past it
            continue
        if entry.startswith(":"):
            option = indent  # an option, whose value may wrap onto more lines
            continue
        if option >= 0 and indent > option:
            continue
        option = -1
        if entry.startswith(".."):
            continue  # a comment, or a directive nested in this one
        # `Title <document>` names the document in the angle brackets.
        if entry.endswith(">") and "<" in entry:
            entry = entry[entry.rindex("<") + 1 : -1]
        if "*" in entry:
            # :glob: entries name a pattern Sphinx expands against the source
            # tree. Expanding it here would be a second implementation of a
            # rule that already decides what the site contains.
            fail(f"{shown(index_rst)} uses a glob toctree entry ({entry}), which is not supported")
        documents.append(entry)
    if not documents:
        fail(f"{shown(index_rst)} has no toctree entries")
    return documents


def globals_index(globals_md: Path) -> list[tuple[str, str]]:
    """List ``(group, markdown path)`` from TypeDoc's own module index.

    ``globals.md`` is the page TypeDoc renders as ``modules.html``: every
    export, grouped by kind and in the order the site lists them. Reading it is
    what keeps a newly exported symbol from being missing here.
    """
    entries: list[tuple[str, str]] = []
    group = ""
    for line in read(globals_md).splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            group = heading.group(1)
            continue
        link = re.match(r"^-\s+\[.+?\]\((.+?\.md)\)\s*$", line)
        if link and group:
            entries.append((group, link.group(1)))
    if not entries:
        fail(f"{shown(globals_md)} lists no exports")
    return entries


CODE_SPAN = re.compile(r"(`+[^`]*`+)")
ESCAPE = re.compile(r"\\([*_])")
"""Only the two escapes that stand inside an identifier.

The others a generator emits are load-bearing where they are: ``\\|`` is what
keeps a union type from ending a table cell, ``\\<`` is what keeps a type
parameter from being read as raw HTML, and ``\\[iterator\\]`` is what keeps a
Symbol-named method from being read as an empty link. Unescaping those trades
a cosmetic gain in the text for markdown that renders wrongly.
"""


def readable(text: str) -> str:
    r"""Undo the backslashes a generator adds to protect an identifier.

    ``create_memory(\*, query, ...)`` and ``MEMORY\_REMOVED`` render as the
    identifiers they are, so the escapes are correct markdown — but this file
    is read as text as often as it is rendered, and a reader copying a
    signature out of it should not have to know that. Fenced code and inline
    code spans are left alone: a backslash inside them is already literal, so
    removing one would change what the example says.
    """
    return "".join(
        part if index % 2 else ESCAPE.sub(r"\1", part)
        for index, part in enumerate(CODE_SPAN.split(text))
    )


def heading(markdown: str) -> str:
    """Return the page's leading H1, or the empty string when it has none.

    The first non-blank *line*, so a heading with prose on the line below it —
    which is one paragraph, not one heading — is still read as a heading. The
    block-wise reading disagreed with :func:`as_page` about what an H1 is, and
    a page can only carry one if both agree.
    """
    for line in markdown.strip().splitlines():
        if not line.strip():
            continue
        match = re.match(r"^#\s+(.+?)\s*$", line)
        return readable(match.group(1)) if match else ""
    return ""


def blocks(markdown: str) -> list[str]:
    """Split markdown into blank-line-separated blocks, leading ones first."""
    return [block for block in re.split(r"\n\s*\n", markdown.strip()) if block.strip()]


def body(markdown: str) -> str:
    """Drop a page's leading banner: raw HTML blocks and horizontal rules.

    A README opening with centred logo and badge markup is the right first
    thing to see on the rendered page and noise at the top of a text file an
    agent is reading. Only leading blocks are dropped, so HTML further down —
    a table, an inline image beside prose — survives untouched.
    """
    remaining = markdown.strip()
    while True:
        head = blocks(remaining)[:1]
        if not head or not (head[0].lstrip().startswith("<") or head[0].strip() == "---"):
            return remaining
        # `blocks` splits a stripped string on blank lines, so the first block
        # it returns is a prefix of it.
        remaining = remaining[len(head[0]) :].strip()


NOT_PROSE = re.compile(r"[#>|]|```|[-*+] |\d+\. |<")
"""Openings that mean a block is structure rather than the page's first prose:
a heading, a blockquote, a table row, a fence, a list item, raw HTML."""


def summarise(markdown: str, limit: int = 200) -> str:
    """Return the first sentence of a page's lead-in, for the index's notes.

    Only what the page says before its first section heading. A page that
    opens straight into its sections has nothing that describes the whole of
    it, and taking the first prose from wherever it turns up would annotate
    the page with the description of one member of it.

    Links are flattened to their text: a link inside a note points somewhere
    other than the entry it annotates, which reads as a second destination.
    """
    lead = re.split(r"^##\s", body(markdown), maxsplit=1, flags=re.M)[0]
    prose = [block for block in blocks(lead) if not NOT_PROSE.match(block)]
    if not prose:
        return ""
    text = " ".join(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", prose[0]).split())
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(sentence) <= limit:
        return sentence
    return sentence[:limit].rsplit(" ", 1)[0] + "…"


# ---- the languages ------------------------------------------------------


def python_reference() -> Reference:
    """Describe the Python SDK's reference as Sphinx built it."""
    root = ROOT / "python"
    project = project_table(root / "pyproject.toml")
    # Named rather than reached for: a key written in a form the reader above
    # does not recognise would otherwise reach the output as a blank version
    # or a summary beginning with a space.
    missing = [key for key in ("version", "description") if not project.get(key)]
    if missing:
        fail(f"{shown(root / 'pyproject.toml')} [project] has no {' or '.join(missing)}")
    markdown = root / "docs" / "_build" / "markdown"
    pages = tuple(
        Page(
            title=heading(read(markdown / f"{name}.md")) or name,
            group="Reference",
            href=f"{name}.html",
            source=f"{name}.md",
        )
        # `index` is the root document, which no toctree names.
        for name in ["index", *toctree(root / "docs" / "index.rst")]
    )
    return Reference(
        title=f"memco Python SDK {project['version']}",
        summary=f"{project['description']} Install with `pip install memco`.",
        html=root / "docs" / "_build" / "html",
        markdown=markdown,
        pages=pages,
        # Sphinx cross-references carry HTML domain ids the markdown has no
        # anchors for, and every rubric is pinned to level three.
        markdown_anchors=False,
        rubric_level=3,
        rubrics=NAPOLEON_RUBRICS,
        # Sphinx's apparatus: two symbol indexes, the search page whose
        # content is built by JavaScript from a separate index, and the
        # highlighted module sources sphinx.ext.viewcode links every symbol to.
        # The sources are in the wheel and on GitHub; a copy of them is not
        # what an agent should be handed as documentation.
        unlisted=frozenset({"genindex.html", "py-modindex.html", "search.html", "_modules/"}),
    )


# TypeDoc's markdown and HTML renderers name two directories differently. The
# markdown is the input and the HTML is what gets published, so every link this
# script writes is translated in this direction.
NODE_HTML_DIRECTORY = {"enumerations": "enums", "type-aliases": "types"}


def node_href(source: str) -> str:
    """Where TypeDoc publishes the page it wrote to this markdown path."""
    directory, _, filename = source.rpartition("/")
    page = f"{filename.removesuffix('.md')}.html"
    # posixpath.join rather than an f-string: a page at the root of the tree
    # has no directory, and interpolating one leaves a leading slash — an
    # absolute URL into the documentation site rather than a sibling of this
    # file.
    return posixpath.join(NODE_HTML_DIRECTORY.get(directory, directory), page)


def nodejs_reference() -> Reference:
    """Describe the Node.js SDK's reference as TypeDoc built it."""
    root = ROOT / "nodejs"
    package = json.loads(read(root / "package.json"))
    markdown = root / "docs" / "_build" / "markdown"
    pages = [
        Page(title="Overview", group="Overview", href="index.html", source="README.md"),
        Page(
            title=heading(read(markdown / "globals.md")) or package["name"],
            group="Overview",
            href="modules.html",
            source="globals.md",
        ),
    ]
    for group, source in globals_index(markdown / "globals.md"):
        pages.append(
            Page(
                title=heading(read(markdown / source)) or Path(source).stem,
                group=group,
                href=node_href(source),
                source=source,
            )
        )
    return Reference(
        title=f"memco Node.js SDK {package['version']}",
        summary=f"{package['description']} Install with `npm install {package['name']}`.",
        html=root / "docs" / "_build" / "html",
        markdown=markdown,
        pages=tuple(pages),
        # TypeDoc anchors its markdown on its own heading slugs, and emits
        # every heading at its true depth.
        markdown_anchors=True,
        rubric_level=0,
        rubrics=frozenset(),
        # The inheritance tree, which is a diagram of pages listed elsewhere.
        unlisted=frozenset({"hierarchy.html"}),
    )


LANGUAGES = {"python": python_reference, "nodejs": nodejs_reference}


# ---- assembling ---------------------------------------------------------


LINK = re.compile(r"\]\(([^()\s]+)\)")


def toward(target: str, base: str) -> str:
    """Express a path from the root of the reference as one from ``base``."""
    return posixpath.relpath(target, base) if base else target


def relink(
    markdown: str, page: Page, published: dict[str, str], here: str, *, anchors: bool = True
) -> str:
    """Point a page's links at published files, as read from ``here``.

    The generator wrote each link from where the page's markdown sits in its
    own build tree, and neither of the two things this page ends up inside is
    there: it is concatenated into a file at the root of the reference, and
    published on its own beside the HTML. ``published`` says where each page
    can be reached and ``here`` says which file is doing the reaching, which
    together settle both the destination and how far up to climb to it. A
    target naming nothing this build produced — an external URL — is left
    exactly as it was.

    ``anchors`` says whether the destination carries the fragments these links
    name; where it does not, the fragment is dropped rather than published as
    a position no file has.
    """
    directory = posixpath.dirname(page.source)
    base = posixpath.dirname(here)
    own = published[page.source]

    def rewritten(match: re.Match[str]) -> str:
        target = match.group(1)
        path, _, anchor = target.partition("#")
        suffix = f"#{anchor}" if anchor else ""
        if not path:
            # In the page's own file an anchor still means this page; in a
            # file the page was folded into, it has to name the page first.
            return match.group(0) if own == here else f"]({toward(own, base)}{suffix})"
        # Resolved against the page that wrote the link, because the file
        # doing the reading is somewhere else in the tree.
        resolved = posixpath.normpath(posixpath.join(directory, path))
        destination = published.get(resolved)
        if destination is None:
            # TypeDoc copies an asset into `media/` and links it as `_media/`
            # from markdown; the HTML it publishes uses the directory's name.
            if not resolved.startswith("_media/"):
                return match.group(0)
            destination = resolved[1:]
        keep = suffix if anchors or destination.endswith(".html") else ""
        return f"]({toward(destination, base)}{keep})"

    return "\n".join(
        LINK.sub(rewritten, line) if prose else line for line, prose in prose_lines(markdown)
    ) + ("\n" if markdown.endswith("\n") else "")


INDEX = "llms.txt"
FULL = "llms-full.txt"


def markdown_href(page: Page) -> str:
    """Where the page's markdown is published: beside the HTML, same name."""
    return f"{page.href.removesuffix('.html')}.md"


def preamble(reference: Reference, note: str) -> str:
    """The H1, summary and note a published file opens with."""
    return f"# {reference.title}\n\n> {reference.summary}\n\n{note}"


def relative_note(canonical: str) -> str:
    """The sentence every published file closes its preamble with."""
    return (
        "Links are relative to this file, so they resolve under whichever version\n"
        f"of the reference it was fetched from. The current release is at {canonical}.\n"
    )


def listed(page: Page) -> str:
    """The page's title with the group heading above it not said twice.

    TypeDoc titles a page for its kind — "Class: Memco" — which reads well on
    its own page and badly as one of nineteen entries under "Classes".
    """
    kind, separator, name = page.title.partition(": ")
    return name if separator and page.group.startswith(kind) else page.title


def index(reference: Reference, canonical: str) -> str:
    """Build ``llms.txt``: the reference's pages as a link list."""
    out = [
        preamble(
            reference,
            "Every page of this reference is published as markdown beside its HTML,\n"
            "under the same name, and the links below name the markdown.\n"
            f"\n{relative_note(canonical)}",
        )
    ]
    for group in dict.fromkeys(page.group for page in reference.pages):
        out.append(f"\n## {group}\n\n")
        for page in reference.pages:
            if page.group != group:
                continue
            note = summarise(read(reference.markdown / page.source))
            suffix = f": {note}" if note else ""
            out.append(f"- [{listed(page)}]({markdown_href(page)}){suffix}\n")
    out.append(f"\n## Full text\n\n- [{FULL}]({FULL}): every page above, as one document.\n")
    return "".join(out)


FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")


def opens_fence(line: str) -> str:
    """The fence a line opens, or the empty string if it opens none."""
    match = FENCE.match(line.strip())
    if match is None:
        return ""
    fence, info = match.groups()
    # A backtick fence's info string may not itself contain a backtick, so a
    # sentence opening with an inline ```code``` span is not a fence — and
    # reading it as one would mark the rest of the page as code.
    return "" if fence[0] == "`" and "`" in info else fence


def prose_lines(markdown: str) -> Iterator[tuple[str, bool]]:
    """Yield each line with whether it is prose rather than fenced code.

    The one reading of where code begins and ends, so everything this script
    does to a line — demoting a heading, rewriting a link, dropping an escape,
    checking a target — agrees about whether a fenced example is inert. A
    closing fence is the same character, at least as long, and carries no info
    string of its own.
    """
    fence = ""
    for line in markdown.splitlines():
        if fence:
            yield line, False
            if re.fullmatch(rf"{fence[0]}{{{len(fence)},}}", line.strip()):
                fence = ""
        elif opened := opens_fence(line):
            fence = opened
            yield line, False
        else:
            yield line, True


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

NAPOLEON_RUBRICS = frozenset({"Example", "Examples", "Note", "Notes", "References"})
"""The docstring sections napoleon renders as a rubric under this configuration.

Only sphinx-markdown-builder mishandles them, so only the Python reference
names this set; see :attr:`Reference.rubrics`.
"""


def as_page(markdown: str, title: str, rubrics: frozenset[str], rubric_level: int) -> str:
    """Give a page one H1 and headings that nest, so its structure survives.

    Two things go wrong in a file where every page is an H1. A page can carry
    more than one — Sphinx renders a toctree caption as a heading in its own
    right — and a second one reads as another page; a page that carries none,
    such as a README opening with a banner, is given the title it is listed
    under. And a rubric the generator pinned to a fixed level inverts the
    nesting around it, which is what a reader chunking on headings goes by, so
    one at exactly that level is pushed back under the heading it followed.
    """
    out: list[str] = []
    seen = False
    depth = 0
    for line, prose in prose_lines(markdown):
        match = HEADING.match(line) if prose else None
        if match:
            level, text = len(match.group(1)), match.group(2)
            if text in rubrics and level == rubric_level and depth >= rubric_level:
                line = f"{'#' * min(depth + 1, 6)} {text}"
            elif level > 1:
                depth = level
            else:
                line, depth = (f"#{line}", 2) if seen else (line, 1)
                seen = True
        out.append(line)
    text = "\n".join(out) + ("\n" if markdown.endswith("\n") else "")
    return text if seen else f"# {title}\n\n{text}"


def rendered(
    reference: Reference, page: Page, published: dict[str, str], here: str, *, anchors: bool = True
) -> str:
    """One page as it is published, with its links resolved for ``here``."""
    source = body(read(reference.markdown / page.source))
    text = relink(source, page, published, here, anchors=anchors)
    # After relink: unescaping a bracket first could turn text the page
    # deliberately escaped into a link for the rewriter to follow.
    text = "\n".join(readable(line) if prose else line for line, prose in prose_lines(text))
    return as_page(text, page.title, reference.rubrics, reference.rubric_level)


def full(reference: Reference, canonical: str) -> str:
    """Build ``llms-full.txt``: every page, in the order the site lists them."""
    # The HTML, not the markdown beside it: these links carry the anchors that
    # name one symbol on a page, and only the HTML has anchors to land on.
    published = {page.source: page.href for page in reference.pages}
    out = [
        preamble(
            reference,
            "Every page of this reference follows, in the order the site lists them.\n"
            "Links point at the published HTML, where the anchors they name exist;\n"
            "each page is also published on its own as markdown, under the same name.\n"
            f"\n{relative_note(canonical)}",
        )
    ]
    for page in reference.pages:
        out.append(f"\n{rendered(reference, page, published, FULL).strip()}\n")
    return "".join(out)


def markdown_pages(reference: Reference) -> dict[str, str]:
    """Each page's markdown, keyed by where it is published.

    Published beside the HTML rather than instead of it, so an agent that has
    a page's URL can reach its text by swapping the extension, and llms.txt
    can name markdown throughout without sending a reader to a second tree.
    """
    published = {page.source: markdown_href(page) for page in reference.pages}
    return {
        published[page.source]: rendered(
            reference,
            page,
            published,
            published[page.source],
            anchors=reference.markdown_anchors,
        ).strip()
        + "\n"
        for page in reference.pages
    }


def unreachable(reference: Reference) -> list[str]:
    """List HTML pages the index does not link and has not excused.

    The check that keeps the index honest as the SDK grows: a page the
    generator publishes but nothing here lists is a page an agent reading
    ``llms.txt`` cannot discover.
    """
    linked = {page.href for page in reference.pages}
    directories = tuple(name for name in reference.unlisted if name.endswith("/"))
    return sorted(
        href
        for href in (
            path.relative_to(reference.html).as_posix() for path in reference.html.rglob("*.html")
        )
        if href not in linked and href not in reference.unlisted
        if not href.startswith(directories)
    )


def broken(html: Path, files: dict[str, str]) -> list[str]:
    """List links in the files about to be published that reach nothing.

    The rewriting that puts them there is driven by the page map, and a page
    map that is right about which pages exist can still be wrong about where a
    link inside a page pointed — which is a dead link on the published site,
    and nothing else here would notice it. Each file is read from where it will
    be served, so a page one directory down is held to what its own links mean.

    Fenced code is skipped, on the same terms the rewriter skips it: a link
    that is part of an example was never meant to be followed.
    """
    dangling: set[str] = set()
    for source, text in files.items():
        base = posixpath.dirname(source)
        for line, prose in prose_lines(text):
            if not prose:
                continue
            for match in LINK.finditer(line):
                target = match.group(1).partition("#")[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                reached = posixpath.normpath(posixpath.join(base, unquote(target)))
                # The files being assembled link to each other and are not on
                # disk yet on a clean build, which is every build that matters.
                if reached not in files and not resolves(html, reached):
                    dangling.add(f"{source} -> {target}")
    return sorted(dangling)


def resolves(html: Path, reached: str) -> bool:
    """Whether a resolved link target names a file inside the reference.

    A leading slash or a leading ``..`` leaves the reference for a path this
    build knows nothing about, and ``Path.__truediv__`` would follow either one
    out of the tree and answer for whatever it found there.
    """
    if reached.startswith("/") or reached == ".." or reached.startswith("../"):
        return False
    return (html / reached).is_file()


def main() -> int:
    """Write both files into one language's built reference."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("language", choices=sorted(LANGUAGES), help="the SDK to assemble for")
    language = parser.parse_args().language

    reference = LANGUAGES[language]()
    if not reference.html.is_dir():
        fail(f"{shown(reference.html)} does not exist; build the HTML first")
    missing = unreachable(reference)
    if missing:
        fail(
            f"{len(missing)} page(s) of the {language} reference are not listed in llms.txt: "
            + ", ".join(missing)
        )
    # And the other way, which is what lets the markdown be written beside the
    # HTML without checking for the directory first.
    absent = sorted(
        page.href for page in reference.pages if not (reference.html / page.href).is_file()
    )
    if absent:
        fail(f"{len(absent)} page(s) listed in llms.txt were never published: " + ", ".join(absent))

    canonical = f"{DOCS_SITE}/{language}/latest/"
    pages = markdown_pages(reference)
    files = {
        INDEX: index(reference, canonical),
        FULL: full(reference, canonical),
        **pages,
    }
    dangling = broken(reference.html, files)
    if dangling:
        fail(
            f"{len(dangling)} link(s) in the {language} reference reach nothing: "
            + ", ".join(dangling)
        )
    for name, text in files.items():
        # Every page's directory is one the HTML build already made, which is
        # what the second check above establishes.
        (reference.html / name).write_text(text, "utf-8")
    for name in (INDEX, FULL):
        print(f"{shown(reference.html / name)}: {len(files[name])} bytes")
    print(f"{shown(reference.html)}: {len(pages)} pages as markdown beside their HTML")
    return 0


if __name__ == "__main__":
    sys.exit(main())
