"""Sphinx configuration for the Memco Python SDK reference."""

import pathlib
import re
from importlib.metadata import version as _version

# Taken from the licence so the footer cannot drift from the legal notice.
_LICENCE = (pathlib.Path(__file__).parent.parent / "LICENSE").read_text()
_MATCH = re.search(r"Copyright \(c\) (\d{4})", _LICENCE)
if _MATCH is None:
    raise RuntimeError("LICENSE carries no copyright year for the documentation footer")
_LICENCE_YEAR = _MATCH.group(1)

project = "memco"
author = "Memco Labs, Inc."
# Sphinx does not derive the footer notice from `author`; without this it
# renders a bare "Copyright ©".
copyright = f"{_LICENCE_YEAR} {author}"  # noqa: A001 - the name Sphinx requires
release = _version("memco")
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
]

# The docstrings are Google style throughout.
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
# Render an "Attributes:" section as instance-variable fields on the class
# rather than as separate object descriptions. Without this, every dataclass
# field is documented twice — once by napoleon and once by autodoc.
napoleon_use_ivar = True

# Types belong with the parameter they describe, not repeated in the signature.
autodoc_typehints = "description"
autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}
# The SDK re-exports everything through memco.client; documenting the module a
# symbol happens to live in would leak the private layout into the reference.
add_module_names = False

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

# Report every unresolved cross-reference. Without this, `-W` in CI catches
# nothing: a broken :class: or :meth: in a docstring resolves to nothing
# silently and rots in the published reference.
nitpicky = True

# Third-party types the SDK exposes but does not own. Sphinx cannot resolve
# them without an inventory, and grpc publishes none.
nitpick_ignore = [
    ("py:class", "grpc.StatusCode"),
    ("py:class", "grpc.Channel"),
    ("py:class", "grpc.RpcError"),
    ("py:class", "grpc.ClientCallDetails"),
    # Generated protobuf types, returned by the to_proto() helpers. They ship
    # no documentation inventory and are an implementation detail.
    ("py:class", "memco.memory.v1.memory_pb2.Tag"),
    ("py:class", "memco.memory.v1.memory_pb2.FeedbackRating"),
    # Set in __init__ rather than at class level, so napoleon renders them as
    # instance-variable fields, which are not cross-reference targets.
    ("py:attr", "MemcoAPIError.code"),
    ("py:attr", "MemcoAPIError.message"),
]

exclude_patterns = ["_build"]
html_theme = "furo"
html_title = f"memco {release}"

# The wordmark is near-black, so it needs a light variant to stay visible when
# the theme switches. Both are shared with the repository READMEs.
html_static_path = ["../../assets"]
html_theme_options = {
    "light_logo": "logo.svg",
    "dark_logo": "logo-dark.svg",
    "source_repository": "https://github.com/memcoai/memco/",
    "source_branch": "main",
    "source_directory": "python/docs/",
}
