"""The reference an agent reads is the reference a person reads.

``scripts/build_llms_txt.py`` assembles ``llms.txt`` and ``llms-full.txt`` from
a markdown rendering of the same sources the HTML is built from, and writes
them into the build output the documentation site publishes verbatim. Nothing
at runtime opens either file, and neither is committed, so these tests are what
holds the assembler to its contract: the page order is the site's own, every
page the build published is reachable from the index, and a link that meant a
markdown file means the published HTML by the time it reaches a reader.

The Node.js half of the assembler is exercised here too. It reads TypeDoc's
``globals.md`` rather than a Sphinx toctree, but everything after that — the
link rewriting, the coverage check, the shape of both files — is one
implementation, and this is where it can be held to fixtures.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def script() -> ModuleType:
    """Load the assembler, which lives outside the package and ships with none."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "build_llms_txt.py"
    spec = importlib.util.spec_from_file_location("build_llms_txt", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it is executed: the assembler defers its annotations,
    # so `dataclass` resolves each field's type by looking its module up in
    # sys.modules, and finds nothing there for a module loaded from a path.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write(root: Path, files: dict[str, str]) -> Path:
    """Write a fixture tree and return its root."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
    return root


# ---- reading the generators' own table of contents ----------------------


def test_the_toctree_decides_the_page_order(script, tmp_path):
    # The order pages are read in is the order the site presents them, and the
    # toctree is where that is actually decided. Reading it is what keeps a
    # page added there from being silently absent from what an agent reads.
    index = tmp_path / "index.rst"
    index.write_text(
        "memco\n"
        "=====\n"
        "\n"
        "Prose that mentions nothing.\n"
        "\n"
        ".. toctree::\n"
        "   :maxdepth: 2\n"
        "   :caption: Reference\n"
        "\n"
        "   clients\n"
        "   operations\n"
        "   Agent tools <agent>\n"
        "\n"
        "Trailing prose at column zero, which ends the directive.\n"
        "\n"
        "   not_a_document\n",
        "utf-8",
    )
    assert script.toctree(index) == ["clients", "operations", "agent"]


def test_a_toctree_ends_at_its_own_column_not_at_column_zero(script, tmp_path):
    # Nested in another directive, the toctree's entries end where the toctree
    # does. Reading "indented at all" as "still inside" swallowed the rest of
    # the enclosing directive and turned its prose into page names.
    index = tmp_path / "index.rst"
    index.write_text(
        ".. only:: html\n"
        "\n"
        "   .. toctree::\n"
        "      :caption: A caption that wraps onto\n"
        "         a second line\n"
        "\n"
        "      .. a comment inside the directive\n"
        "\n"
        "      clients\n"
        "      errors\n"
        "\n"
        "   Trailing prose belonging to `only`, not to the toctree.\n",
        "utf-8",
    )
    assert script.toctree(index) == ["clients", "errors"]


def test_a_glob_entry_is_refused_rather_than_read_as_a_page_name(script, tmp_path):
    # `api/*` is a pattern Sphinx expands against the source tree. Passing it
    # on as a document name fails later, reporting a file named `api/*.md`.
    index = tmp_path / "index.rst"
    index.write_text(".. toctree::\n   :glob:\n\n   api/*\n", "utf-8")
    with pytest.raises(SystemExit):
        script.toctree(index)


def test_an_index_with_no_toctree_is_a_failure_not_an_empty_file(script, tmp_path):
    # Silently publishing a one-page reference would be far worse than saying
    # the build is not what it looks like.
    index = tmp_path / "index.rst"
    index.write_text("memco\n=====\n\nNothing else.\n", "utf-8")
    with pytest.raises(SystemExit):
        script.toctree(index)


def test_typedocs_module_index_supplies_both_grouping_and_order(script, tmp_path):
    # globals.md is the page TypeDoc renders as modules.html: every export, in
    # the order the site lists it. The escaped underscore is TypeDoc's own.
    globals_md = tmp_path / "globals.md"
    globals_md.write_text(
        "# @memcoai/memco\n"
        "\n"
        "Node.js SDK for Memco Shared Memory.\n"
        "\n"
        "## Classes\n"
        "\n"
        "- [Toolset](classes/Toolset.md)\n"
        "- [Memco](classes/Memco.md)\n"
        "\n"
        "## Variables\n"
        "\n"
        "- [AGENT\\_RECOVERABLE](variables/AGENT_RECOVERABLE.md)\n",
        "utf-8",
    )
    assert script.globals_index(globals_md) == [
        ("Classes", "classes/Toolset.md"),
        ("Classes", "classes/Memco.md"),
        ("Variables", "variables/AGENT_RECOVERABLE.md"),
    ]


def test_the_project_table_answers_for_itself_and_not_for_a_tool(script, tmp_path):
    # `version` appears in more than one table of a real pyproject.toml, and
    # the one that names the release is the one under [project].
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "memco"\nversion = "1.2.3"\ndescription = "A description."\n'
        '\n[tool.ruff]\nversion = "not-the-release"\ntarget-version = "py310"\n',
        "utf-8",
    )
    assert script.project_table(pyproject) == {
        "name": "memco",
        "version": "1.2.3",
        "description": "A description.",
    }


def test_the_project_table_is_read_even_when_nothing_follows_it(script, tmp_path):
    # [project] is not required to have another table after it, and a reader
    # that insists on one reports the table as absent when it is simply last.
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[build-system]\nrequires = ["hatchling"]\n'
        '\n[project]\nversion = "1.2.3"\ndescription = "A description."\n',
        "utf-8",
    )
    assert script.project_table(pyproject)["version"] == "1.2.3"


def test_both_quote_styles_toml_allows_are_read(script, tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nversion = '1.2.3'\ndescription = 'A description.'\n", "utf-8")
    assert script.project_table(pyproject) == {"version": "1.2.3", "description": "A description."}


def test_a_value_in_a_form_the_reader_does_not_know_is_absent_not_blank(script, tmp_path):
    # A triple-quoted description used to match the empty string between the
    # first two quotes, which reached the published summary as a blank.
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\ndescription = """Long\ntext"""\n', "utf-8")
    assert "description" not in script.project_table(pyproject)


# ---- turning a page into text ------------------------------------------


def test_a_readmes_badge_banner_does_not_open_the_file_an_agent_reads(script):
    markdown = (
        '<p align="center">\n  <img alt="Memco" src="logo.svg">\n</p>\n'
        "\n"
        '<p align="center">\n  <a href="ci"><img alt="CI" src="badge.svg"></a>\n</p>\n'
        "\n"
        "---\n"
        "\n"
        "Memco Shared Memory is a persistent memory.\n"
        "\n"
        "<table><tr><td>kept</td></tr></table>\n"
    )
    stripped = script.body(markdown)
    assert stripped.startswith("Memco Shared Memory is a persistent memory.")
    # Only the banner goes. Markup further down is part of the page.
    assert "<table>" in stripped


def test_a_page_that_opens_with_prose_is_left_exactly_as_it_is(script):
    markdown = "# Clients\n\nA client owns one connection.\n"
    assert script.body(markdown) == markdown.strip()


def test_the_index_note_is_one_sentence_with_no_second_destination_in_it(script):
    note = script.summarise(
        "# Clients\n"
        "\n"
        "A client owns one [gRPC](https://grpc.io) connection. It also owns the\n"
        "credential, which this sentence does not need to say.\n"
    )
    assert note == "A client owns one gRPC connection."


def test_a_long_first_sentence_is_cut_on_a_word(script):
    note = script.summarise("# Types\n\n" + "one two three " * 30 + "end.\n", limit=40)
    assert note.endswith("…")
    assert len(note.removesuffix("…")) <= 40
    # Cut between words, never through one.
    words = note.removesuffix("…").split()
    assert words == ("one two three " * 3).split()[:8]


def test_a_page_that_is_all_structure_gets_no_note_rather_than_a_wrong_one(script):
    assert script.summarise("# Variables\n\n- [one](one.md)\n- [two](two.md)\n") == ""


def test_the_note_describes_the_page_and_not_one_member_of_it(script):
    # A page that opens straight into its sections says nothing about itself.
    # Taking the first prose from wherever it turns up annotated the page with
    # the description of whichever member happened to come first.
    assert (
        script.summarise("# Interface: Foo\n\n## Properties\n\n### bar\n\nThe bar of the thing.\n")
        == ""
    )


# ---- links --------------------------------------------------------------


@pytest.fixture
def hrefs() -> dict[str, str]:
    """Where each page of a small reference is published, as HTML."""
    return {
        "index.md": "index.html",
        "clients.md": "clients.html",
        "classes/Memco.md": "classes/Memco.html",
        "README.md": "index.html",
        "interfaces/MemcoOptions.md": "interfaces/MemcoOptions.html",
        "type-aliases/SunsetKind.md": "types/SunsetKind.html",
    }


@pytest.fixture
def pages(hrefs) -> dict[str, str]:
    """The same pages, as the markdown published beside that HTML."""
    return {source: f"{href.removesuffix('.html')}.md" for source, href in hrefs.items()}


@pytest.fixture
def nested(script):
    """A page one directory down, so a relative link has somewhere to go."""
    return script.Page(
        title="Memco", group="Classes", href="classes/Memco.html", source="classes/Memco.md"
    )


@pytest.fixture
def root(script):
    """A page at the root of the reference."""
    return script.Page(title="memco", group="Reference", href="index.html", source="index.md")


def test_a_link_to_another_page_reaches_the_html_that_page_is_published_as(script, nested, hrefs):
    # The markdown says `../interfaces/...`; relative to the reference root
    # that is `interfaces/...`, and the reader is holding a file at the root.
    assert (
        script.relink("See [options](../interfaces/MemcoOptions.md).", nested, hrefs, script.FULL)
        == "See [options](interfaces/MemcoOptions.html)."
    )


def test_the_two_directories_typedoc_names_differently_are_translated(script):
    # TypeDoc's markdown renderer and its HTML renderer disagree about two
    # directory names, so a page's markdown path does not name its own URL.
    assert script.node_href("type-aliases/SunsetKind.md") == "types/SunsetKind.html"
    assert script.node_href("enumerations/DataSource.md") == "enums/DataSource.html"
    assert script.node_href("classes/Memco.md") == "classes/Memco.html"
    assert script.node_href("README.md") == "README.html"


def test_a_bare_anchor_stops_meaning_this_page_once_every_page_is_one_file(script, nested, hrefs):
    assert (
        script.relink("Run [connect](#connect) first.", nested, hrefs, script.FULL)
        == "Run [connect](classes/Memco.html#connect) first."
    )


def test_an_anchor_on_another_page_survives_the_rewrite(script, root, hrefs):
    assert (
        script.relink("The [operations](clients.md#memco.Memco.memory).", root, hrefs, script.FULL)
        == "The [operations](clients.html#memco.Memco.memory)."
    )


def test_a_target_this_build_did_not_produce_is_left_alone(script, root, hrefs):
    markdown = "See [memco.ai](https://memco.ai) and [a stranger](../elsewhere.md)."
    assert script.relink(markdown, root, hrefs, script.FULL) == markdown


def test_a_link_inside_a_fenced_example_is_left_as_the_example_wrote_it(script, root, hrefs):
    # Fenced code is inert. The heading pass already reads it that way, and a
    # link rewriter that does not agree edits the text of an example.
    markdown = "Prose [x](clients.md).\n\n```md\n[x](clients.md)\n```\n"
    assert script.relink(markdown, root, hrefs, script.FULL) == (
        "Prose [x](clients.html).\n\n```md\n[x](clients.md)\n```\n"
    )


def test_typedocs_asset_directory_is_named_as_the_site_serves_it(script, hrefs):
    # TypeDoc copies an asset into `media/` and links it from markdown as
    # `_media/`, so the link as written reaches nothing on the site.
    page = script.Page(title="Overview", group="Overview", href="index.html", source="README.md")
    assert (
        script.relink("[MIT](_media/LICENSE)", page, hrefs, script.FULL) == "[MIT](media/LICENSE)"
    )


def test_an_asset_linked_from_a_page_one_directory_down_still_resolves(script, nested, hrefs):
    # TypeDoc writes `../_media/...` from a page in classes/. Rewriting only
    # the root-relative spelling left that pointing outside the reference.
    assert (
        script.relink("[MIT](../_media/LICENSE)", nested, hrefs, script.FULL)
        == "[MIT](media/LICENSE)"
    )


# ---- headings -----------------------------------------------------------


def test_a_second_h1_on_a_page_is_demoted_so_it_is_not_read_as_a_page(script):
    markdown = "# memco\n\nProse.\n\n# Reference\n\n- [Clients](clients.html)\n"
    assert script.as_page(markdown, "memco", frozenset(), 0) == (
        "# memco\n\nProse.\n\n## Reference\n\n- [Clients](clients.html)\n"
    )


def test_a_comment_in_a_python_example_is_not_a_heading(script):
    markdown = (
        "# memco\n\n```python\n# reads MEMCO_API_TOKEN\nclient = Memco()\n```\n\n# Reference\n"
    )
    result = script.as_page(markdown, "memco", frozenset(), 0)
    assert "# reads MEMCO_API_TOKEN" in result
    assert "## reads" not in result
    # The heading after the fence is still demoted, so the fence closed.
    assert result.endswith("## Reference\n")


def test_a_page_with_no_h1_is_given_the_title_it_is_listed_under(script):
    assert script.as_page("Prose with no heading.\n", "Overview", frozenset(), 0) == (
        "# Overview\n\nProse with no heading.\n"
    )


def test_a_page_whose_h1_arrives_late_is_not_given_a_second_one(script):
    # The H1 is the page's, wherever it is. Prepending a title on top of it
    # would put two in the file for one page, which is what the split reads
    # as two pages.
    markdown = "An introduction.\n\n# Real title\n\nBody.\n"
    result = script.as_page(markdown, "Listed as", frozenset(), 0)
    assert [line for line in result.splitlines() if line.startswith("# ")] == ["# Real title"]


# ---- what the two files promise -----------------------------------------


@pytest.fixture
def reference(script, tmp_path):
    """A two-page reference, built and ready to assemble."""
    html = write(
        tmp_path / "html",
        {
            "index.html": "",
            "clients.html": "",
            "types/Tag.html": "",
            "search.html": "",
            "_modules/memco/client.html": "",
        },
    )
    markdown = write(
        tmp_path / "markdown",
        {
            "index.md": "# memco\n\nPython SDK for Memco.\n",
            "clients.md": "# Clients\n\nA client owns one connection.\n\nSee [index](index.md).\n",
            # No heading of its own, the shape a README arrives in.
            "Tag.md": "One tag on a memory.\n",
        },
    )
    return script.Reference(
        title="memco Python SDK 1.2.3",
        summary="Python SDK for Memco. Install with `pip install memco`.",
        html=html,
        markdown=markdown,
        pages=(
            script.Page(title="memco", group="Reference", href="index.html", source="index.md"),
            script.Page(
                title="Clients", group="Reference", href="clients.html", source="clients.md"
            ),
            script.Page(title="Tag", group="Types", href="types/Tag.html", source="Tag.md"),
        ),
        markdown_anchors=False,
        rubric_level=3,
        rubrics=script.NAPOLEON_RUBRICS,
        unlisted=frozenset({"search.html", "_modules/"}),
    )


def test_the_index_is_the_shape_llmstxt_describes(script, reference):
    text = script.index(reference, "https://docs.memco.ai/sdk/python/latest/")
    lines = text.splitlines()
    assert lines[0] == "# memco Python SDK 1.2.3"
    assert lines[2].startswith("> Python SDK for Memco.")
    # The links name the markdown, not the HTML: an agent following one gets
    # text rather than a page it has to strip.
    assert "- [memco](index.md): Python SDK for Memco." in text
    assert "- [Clients](clients.md): A client owns one connection." in text
    assert "- [llms-full.txt](llms-full.txt)" in text
    # One heading per group, in the order the pages first mention them, and
    # each page listed under its own.
    assert [line for line in lines if line.startswith("## ")] == [
        "## Reference",
        "## Types",
        "## Full text",
    ]
    assert text.index("- [Tag](types/Tag.md)") > text.index("## Types")


def test_the_full_text_carries_every_page_in_order_and_one_h1_each(script, reference):
    text = script.full(reference, "https://docs.memco.ai/sdk/python/latest/")
    headings = [line for line in text.splitlines() if line.startswith("# ")]
    # Tag.md carries no heading, so it is given the one it is listed under.
    assert headings == ["# memco Python SDK 1.2.3", "# memco", "# Clients", "# Tag"]
    # The link that named a markdown file names the page the site publishes.
    assert "See [index](index.html)." in text


def test_a_page_the_build_published_but_nothing_indexes_is_reported(script, reference):
    # The check that keeps llms.txt honest as the SDK grows: a page an agent
    # cannot reach from the index is a page it will not find at all. `main`
    # turns a non-empty report into a failed build.
    (reference.html / "operations.html").write_text("", "utf-8")
    assert script.unreachable(reference) == ["operations.html"]


def test_the_generators_own_apparatus_is_excused_by_name_and_by_directory(script, reference):
    # search.html is named; _modules/ is a directory of highlighted sources
    # that viewcode links every symbol to, and is excused wholesale.
    assert script.unreachable(reference) == []


def test_a_link_that_reaches_nothing_is_reported_before_anything_is_written(script, reference):
    # The page map being right about which pages exist does not make a link
    # inside a page right about where it pointed, and a dangling link is a
    # dead link on the published site.
    assert script.broken(reference.html, {"llms.txt": "[gone](operations.html)"}) == [
        "llms.txt -> operations.html"
    ]
    assert script.broken(reference.html, {"llms.txt": "[here](clients.html)"}) == []


def test_a_link_is_read_from_where_the_file_carrying_it_is_served(script, reference):
    # A published page sits one directory down, so its links climb out of it.
    # Reading every file as though it were at the root would call a sound link
    # broken and a broken one sound.
    assert script.broken(reference.html, {"types/Tag.md": "[up](../clients.html)"}) == []
    assert script.broken(reference.html, {"types/Tag.md": "[flat](clients.html)"}) == [
        "types/Tag.md -> clients.html"
    ]


def test_a_link_in_a_fenced_example_was_never_meant_to_be_followed(script, reference):
    # The rewriter leaves fenced code alone, so the guard must too, or the
    # first example showing markdown syntax fails the build.
    assert script.broken(reference.html, {"llms.txt": "```md\n[x](nowhere.html)\n```"}) == []


def test_a_target_that_leaves_the_reference_is_never_answered_for_from_outside(script, reference):
    # `html / "/etc/passwd"` is `/etc/passwd`, and `html / "../../x"` is
    # whatever is two directories up — either would report a link as sound
    # because some unrelated file exists.
    assert not script.resolves(reference.html, "/index.html")
    assert not script.resolves(reference.html, "../markdown/index.md")
    assert script.resolves(reference.html, "clients.html")


def test_percent_encoding_does_not_smuggle_a_target_out_of_the_reference(script, reference):
    # Resolving before decoding let `%2e%2e/` through as an ordinary path
    # segment, and a file outside the reference then answered for a link
    # inside it.
    assert script.broken(reference.html, {"llms.txt": "[x](%2e%2e/markdown/index.md)"}) == [
        "llms.txt -> %2e%2e/markdown/index.md"
    ]


def test_an_escape_a_generator_added_does_not_reach_the_reader(script):
    # `create_memory(\*, ...)` and `MEMORY\_REMOVED` are correct markdown and
    # wrong text, and this file is read as text at least as often.
    assert script.readable(r"create\_memory(\*, query)") == "create_memory(*, query)"


def test_an_escape_that_is_holding_the_markdown_together_survives(script):
    # These three are not decoration. `\|` is what keeps a union type from
    # ending a table cell, `\<` what keeps a type parameter from being read as
    # raw HTML, and `\[` what keeps a Symbol-named method from becoming an
    # empty link. Dropping them trades correct rendering for tidier text.
    assert script.readable(r"| `A` \| `B` |") == r"| `A` \| `B` |"
    assert script.readable(r"Promise\<void\>") == r"Promise\<void\>"
    assert script.readable(r"### \[asyncDispose\]()") == r"### \[asyncDispose\]()"


def test_an_escape_inside_code_is_part_of_the_example(script):
    # A backslash in a code span is already literal; removing it would change
    # what the example says.
    assert script.readable(r"prose \* and `code \* here`") == r"prose * and `code \* here`"


def test_a_rubric_is_pushed_under_what_it_documents(script):
    # sphinx-markdown-builder emits a rubric at a fixed level however deep its
    # owner is, so a method's example landed above the method and every member
    # after it read as part of that example.
    markdown = "# Clients\n\n### class Memco\n\n#### close()\n\n### Example\n\n### class Async\n"
    assert script.as_page(markdown, "Clients", script.NAPOLEON_RUBRICS, 3).splitlines() == [
        "# Clients",
        "",
        "### class Memco",
        "",
        "#### close()",
        "",
        "##### Example",
        "",
        "### class Async",
    ]


def test_a_generator_that_nests_its_own_headings_is_left_alone(script):
    # TypeDoc puts `## Example` beside `## Returns`, both children of the
    # method above them. Applying the Sphinx repair here would push the
    # example under Returns — the same inversion, the other way round — so a
    # reference that does not need it names no rubrics at all.
    markdown = "# render()\n\n## Parameters\n\n## Returns\n\n## Example\n"
    assert script.as_page(markdown, "render()", frozenset(), 0) == markdown


def test_a_real_section_sharing_a_rubrics_name_is_left_where_it_is(script):
    # The shape that used to bury it: a genuine page section arriving after a
    # documented object, so it is shallower than the heading before it. Only a
    # heading at exactly the level the generator pins rubrics to is moved, and
    # Sphinx renders a document's own sections one level above that.
    markdown = "# Errors\n\n### class MemcoError\n\n#### code\n\n## Notes\n\nProse.\n"
    assert "\n## Notes\n" in script.as_page(markdown, "Errors", script.NAPOLEON_RUBRICS, 3)


def test_a_module_index_listing_nothing_is_a_failure_not_an_empty_reference(script, tmp_path):
    globals_md = tmp_path / "globals.md"
    globals_md.write_text("# @memcoai/memco\n\nProse and no exports.\n", "utf-8")
    with pytest.raises(SystemExit):
        script.globals_index(globals_md)


# ---- the pages published beside the HTML --------------------------------


def test_a_page_is_published_as_markdown_under_its_html_name(script):
    page = script.Page(title="Memco", group="Classes", href="classes/Memco.html", source="x.md")
    assert script.markdown_href(page) == "classes/Memco.md"


def test_in_its_own_file_a_page_keeps_the_anchors_that_mean_itself(script, nested, pages):
    # Folded into llms-full.txt the page is no longer the whole file, so a
    # bare anchor has to name it first. On its own it still is.
    markdown = "Run [connect](#connect) first."
    assert script.relink(markdown, nested, pages, "classes/Memco.md") == markdown


def test_a_published_page_reaches_its_siblings_from_where_it_sits(script, nested, pages):
    # The page is served from classes/, not from the root, so a link to a
    # sibling directory climbs out of it — and one to its own directory does
    # not climb at all.
    assert (
        script.relink("See [o](../interfaces/MemcoOptions.md).", nested, pages, "classes/Memco.md")
        == "See [o](../interfaces/MemcoOptions.md)."
    )
    assert (
        script.relink("A [k](../type-aliases/SunsetKind.md).", nested, pages, "classes/Memco.md")
        == "A [k](../types/SunsetKind.md)."
    )


def test_every_page_is_published_and_carries_the_reference_it_came_from(script, reference):
    published = script.markdown_pages(reference)
    assert sorted(published) == ["clients.md", "index.md", "types/Tag.md"]
    # Each is the same text llms-full.txt carries, differing only in where its
    # links point — here, at the markdown beside it.
    assert published["clients.md"].startswith("# Clients")
    assert "See [index](index.md)." in published["clients.md"]
    # A page with no heading of its own still gets the one it is listed under.
    assert published["types/Tag.md"].startswith("# Tag")


def test_a_fragment_the_destination_has_no_anchor_for_is_dropped(script, nested, pages):
    # sphinx-markdown-builder writes no anchors while still emitting the HTML
    # domain's ids as fragments, so `errors.md#memco.errors.MemcoError` names
    # a position that file has never heard of. The page survives; the position
    # does not, because a link to nowhere on the right page is worse than a
    # link to the top of it.
    markdown = "See [options](../interfaces/MemcoOptions.md#memco.Options)."
    assert script.relink(markdown, nested, pages, "classes/Memco.md", anchors=False) == (
        "See [options](../interfaces/MemcoOptions.md)."
    )
    # Where the generator does anchor its markdown, the fragment is the point.
    assert script.relink(markdown, nested, pages, "classes/Memco.md", anchors=True) == (
        "See [options](../interfaces/MemcoOptions.md#memco.Options)."
    )


def test_a_fragment_into_the_html_survives_whatever_the_markdown_lacks(script, nested, hrefs):
    # llms-full.txt links the HTML precisely because that is where the anchors
    # are, so `anchors` describes the markdown and must not reach those.
    markdown = "See [options](../interfaces/MemcoOptions.md#memco.Options)."
    assert script.relink(markdown, nested, hrefs, script.FULL, anchors=False) == (
        "See [options](interfaces/MemcoOptions.html#memco.Options)."
    )


def test_a_published_page_ends_with_a_newline_like_any_other_file(script, reference):
    assert all(text.endswith("\n") for text in script.markdown_pages(reference).values())


def test_a_page_listed_but_never_published_is_reported(script, reference):
    # The other direction of the coverage check, and what lets a page's
    # markdown be written beside its HTML without looking for the directory.
    (reference.html / "clients.html").unlink()
    absent = [p.href for p in reference.pages if not (reference.html / p.href).is_file()]
    assert absent == ["clients.html"]


def test_an_inline_code_span_at_the_start_of_a_line_does_not_open_a_fence(script):
    # ```code``` in a sentence is an inline span; reading it as a fence would
    # mark the rest of the page as code and stop every rewrite there.
    assert script.opens_fence("```code``` is inline.") == ""
    assert script.opens_fence("```ts") == "```"
    assert script.opens_fence("~~~") == "~~~"


def test_a_kind_the_group_heading_already_states_is_not_said_twice(script):
    page = script.Page(title="Class: Memco", group="Classes", href="c.html", source="c.md")
    assert script.listed(page) == "Memco"
    aliases = script.Page(
        title="Type Alias: SunsetKind", group="Type Aliases", href="t.html", source="t.md"
    )
    assert script.listed(aliases) == "SunsetKind"


def test_a_title_that_merely_contains_a_colon_keeps_all_of_itself(script):
    page = script.Page(title="Errors: a tour", group="Reference", href="e.html", source="e.md")
    assert script.listed(page) == "Errors: a tour"
