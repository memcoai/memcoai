"""Sphinx configuration for the Memco Python SDK reference."""

from importlib.metadata import version as _version

project = "memco"
author = "Memco"
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

# Third-party types the SDK exposes but does not own. Sphinx cannot resolve
# them without an inventory, and grpc publishes none.
nitpick_ignore = [
    ("py:class", "grpc.StatusCode"),
    ("py:class", "grpc.Channel"),
    ("py:class", "grpc.RpcError"),
    ("py:class", "grpc.ClientCallDetails"),
]

exclude_patterns = ["_build"]
html_theme = "furo"
html_title = f"memco {release}"
