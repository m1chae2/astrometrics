"""Sphinx documentation configuration file for Astrometrics.

Includes PyData Sphinx Theme, numpydoc, sphinx-automodapi,
MyST-NB notebook execution, and InterSphinx mappings to
Astropy, NumPy, SciPy, and Matplotlib. It also sets up the site features a
reader expects on Read the Docs: an "Edit on GitHub" button, the date each page
last changed, a sitemap, a 404 page, and a PDF of the five papers.
"""

import json
import os
import re
import sys
import tomllib
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import ClassVar

from docutils import nodes
from sphinx import addnodes
from sphinx.application import Sphinx
from sphinx.environment import BuildEnvironment
from sphinx.util.docutils import SphinxDirective

# Insert root project path into sys.path to enable Sphinx autodoc reflection
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("../build/documentation"))

# Project information
# The version and author come from pyproject.toml, so each is written once.
_PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"
_PYPROJECT = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]

project = "Astrometrics"
author = _PYPROJECT["authors"][0]["name"]
copyright = f"2022-2026, {author}"
try:
    # The installed version. Read the Docs and CI install the package first.
    release = installed_version("astrometrics")
except PackageNotFoundError:
    release = _PYPROJECT["version"]
# The short X.Y version.
version = ".".join(release.split(".")[:2])

# Sphinx extension module names
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.mathjax",
    "sphinx_automodapi.automodapi",
    "sphinx_automodapi.smart_resolver",
    "numpydoc",
    "myst_nb",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinxcontrib.mermaid",
    "sphinx_sitemap",
    "notfound.extension",
    "sphinx_last_updated_by_git",
    "ui_autodoc",
    "tutorial_indexer",
    "audit_pipelines",
    "generate_pipeline_review_docs",
]


# Source suffix options
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "myst-nb",
    ".ipynb": "myst-nb",
}

# Template and static paths
templates_path = ["_templates"]
# plans/ holds working plans for the people and AI agents changing the code,
# not user documentation, so Sphinx does not build them. evidence/ holds raw
# measurement records that code comments cite, so it is not built either.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "README.md", ".venv", "plans", "evidence"]

html_static_path = ["_static"]
html_css_files = ["custom.css"]

html_theme = "pydata_sphinx_theme"
html_logo = "_static/logo.png"
html_favicon = "_static/logo.png"
html_title = "Astrometrics documentation"
# The light or dark theme follows the reader's system setting (the theme's
# default). "github_*" and "doc_path" feed the "Edit on GitHub" button.
html_context = {
    "github_user": "m1chae2",
    "github_repo": "astrometrics",
    "github_version": "main",
    "doc_path": "documentation",
}
# The root toctree lists the seven section pages (index.rst), so all seven
# fit in the navigation bar without a "More" menu.
html_theme_options = {
    "header_links_before_dropdown": 7,
    "navbar_align": "left",
    "logo": {
        "image_light": "_static/logo.png",
        "image_dark": "_static/logo.png",
        "text": "Astrometrics",
    },
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/m1chae2/astrometrics",
            "icon": "fa-brands fa-github",
        },
    ],
    "show_toc_level": 2,
    "navbar_start": ["navbar-logo"],
    "navbar_center": ["navbar-nav"],
    "navbar_end": ["search-button", "theme-switcher", "navbar-icon-links"],
    "navbar_persistent": [],
    "use_edit_page_button": True,
    "article_footer_items": ["last-updated"],
}

# Each page shows the date of the last commit that changed it
# (sphinx-last-updated-by-git). Read the Docs fetches the full history first.
html_last_updated_fmt = "%Y-%m-%d"
# Generated pages have no history of their own; they are not given a date.
git_untracked_show_sourcelink = False

# The sitemap and the 404 page need the site's address. Read the Docs sets
# READTHEDOCS_CANONICAL_URL for each version it builds.
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "https://astrometrics.readthedocs.io/en/latest/")
sitemap_url_scheme = "{link}"

# PDF. XeLaTeX handles the Greek letters and symbols (σ, Å, ≤) used throughout.
latex_engine = "xelatex"
# makeindex builds the index everywhere; xindy needs language modules that not
# every TeX installation ships.
latex_use_xindy = False
# The PDF holds the five papers only (papers.rst), not the whole site: the API
# reference, the register and the guides are meant to be searched online.
latex_documents = [("papers", "astrometrics-papers.tex", "Astrometrics papers", author, "manual")]

# Intersphinx mapping for external scientific library links
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "astropy": ("https://docs.astropy.org/en/stable/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "scipy": ("https://docs.scipy.org/doc/scipy/", None),
    "matplotlib": ("https://matplotlib.org/stable/", None),
}

# MyST-NB Execution Configuration
nb_execution_mode = "off"  # Set to auto for live dynamic generation

# Tutorial notebooks intentionally carry no `kernelspec` metadata. myst-nb then
# cannot pick a Pygments lexer for their code cells and emits a "No source code
# lexer found" warning per cell. myst-nb has no config hook to supply a default
# lexer (see the TODO in myst_nb.core.render._get_nb_source_code_lexer), so the
# warning is suppressed.
suppress_warnings = [
    "myst-nb.lexer",
    # In a shallow clone (one commit, as CI checks out by default) git cannot
    # date each page. The page then shows no date; that is no reason to fail
    # the build. The docs workflow and Read the Docs fetch the full history.
    "git.too_shallow",
]

myst_enable_extensions = [
    "dollarmath",
    "amsmath",
    "colon_fence",
    "html_admonition",
    "fieldlist",
]

# Draw equations with the MathJax release the CI equation check uses
# (build/documentation/math_check/), so a page draws exactly what the check
# passed. The version is read from the check's package.json, so the two cannot
# drift apart. Sphinx's default follows the latest 4.x release instead.
_MATH_CHECK_PACKAGE = Path(__file__).resolve().parent.parent / "build/documentation/math_check/package.json"
_MATHJAX_VERSION = json.loads(_MATH_CHECK_PACKAGE.read_text(encoding="utf-8"))["dependencies"]["@mathjax/src"]
mathjax_path = f"https://cdn.jsdelivr.net/npm/mathjax@{_MATHJAX_VERSION}/tex-mml-chtml.js"

# Route ```mermaid fences to sphinxcontrib-mermaid's directive. Without this
# MyST hands them to Pygments as an ordinary code block, and since there is no
# "mermaid" lexer every diagram raises a highlighting warning and renders as
# plain text instead of a diagram.
myst_fence_as_directive = ["mermaid"]

# Generate slug anchors for headings down to level 4. The user guides link to
# their own sections (e.g. "#1-calibration--pre-processing"); without this
# MyST creates no such targets and every in-page link fails to resolve.
myst_heading_anchors = 4

# numpydoc and automodapi configuration
numpydoc_show_class_members = False
numpydoc_attributes_as_param_list = True
automodapi_toctreedirnm = "api/generated"


def _builtin_type_xrefs(app: Sphinx, doctree: nodes.document) -> None:
    """Point the builtin ``type`` in annotations at Python, not a field.

    Constants such as ``DATA_ERRORS`` are annotated
    ``tuple[type[Exception], ...]``. Sphinx looks such a name up with a
    suffix search, which also matches every
    provenance model field named ``type`` and fails the ``-W`` build as
    ambiguous. An exact lookup finds no local ``type``, so intersphinx links it
    to the Python documentation.

    Parameters
    ----------
    app : `sphinx.application.Sphinx`
        The running Sphinx application.
    doctree : `docutils.nodes.document`
        The document just read.
    """
    for node in doctree.findall(addnodes.pending_xref):
        if node.get("refdomain") == "py" and node.get("reftarget") == "type":
            # The domain searches by suffix whenever the attribute exists, even
            # when it is False, so it is removed rather than cleared.
            node.attributes.pop("refspecific", None)
            node["py:module"] = None
            node["py:class"] = None


class _MermaidAsSource(SphinxDirective):
    """Show a Mermaid diagram as its source text, for print builders.

    The HTML site draws Mermaid diagrams in the reader's browser. A PDF has
    no browser, and drawing them ahead of time needs the Mermaid command-line
    tool, which Read the Docs does not have. The PDF shows each diagram's
    source as a captioned code block instead, and points to the web version.
    """

    has_content = True
    optional_arguments = 1
    final_argument_whitespace = True
    option_spec: ClassVar[dict[str, Callable[[str], str]]] = {
        "caption": str,
        "align": str,
        "alt": str,
        "name": str,
        "zoom": str,
        "config": str,
    }

    def run(self) -> list[nodes.Node]:
        """Turn the diagram into a literal block with a short note.

        Returns
        -------
        result : `list` [`docutils.nodes.Node`]
            A paragraph naming the diagram and the code block of its source.
        """
        caption = self.options.get("caption", "Diagram")
        note = nodes.paragraph(text=f"{caption} (drawn as a diagram in the web version of this page):")
        source = nodes.literal_block("\n".join(self.content), "\n".join(self.content))
        return [note, source]


def _plain_mermaid_for_print(app: Sphinx) -> None:
    """Swap in `_MermaidAsSource` when the builder writes a PDF or plain text.

    Parameters
    ----------
    app : `sphinx.application.Sphinx`
        The running Sphinx application.
    """
    if app.builder.format != "html":
        app.add_directive("mermaid", _MermaidAsSource, override=True)


def _plain_equation_numbers_for_print(app: Sphinx, docname: str, source: list[str]) -> None:
    r"""Write ``\tag{n}`` as a plain ``(n)`` in the PDF.

    The papers number their display equations with ``\tag{n}``. The web
    version draws these with MathJax, but the LaTeX writer puts each display
    equation inside a ``split`` block, where LaTeX forbids ``\tag``. For the
    PDF the number is set at the right of the line instead.

    Parameters
    ----------
    app : `sphinx.application.Sphinx`
        The running Sphinx application.
    docname : `str`
        The document being read.
    source : `list` [`str`]
        The document's text, changed in place.
    """
    if app.builder.format == "latex":
        source[0] = re.sub(r"\\tag\{([^}]*)\}", r"\\qquad(\1)", source[0])


def _numbered_equations_unwrapped(app: Sphinx, doctree: nodes.document, docname: str) -> None:
    r"""Let MathJax draw numbered equations that span several lines.

    The papers number their display equations with ``\tag{n}``. For the web,
    Sphinx wraps every equation with a line break (``\\``) in a ``split``
    block, and MathJax refuses a ``\tag`` there: the page shows "\tag not
    allowed in split environment" instead of the equation. This handler
    passes each numbered equation to MathJax as written, inside the display
    delimiters only. The source stays plain Markdown math, so GitHub draws
    it too. The PDF handles ``\tag`` separately
    (`_plain_equation_numbers_for_print`).

    Parameters
    ----------
    app : `sphinx.application.Sphinx`
        The running Sphinx application.
    doctree : `docutils.nodes.document`
        The resolved document, changed in place.
    docname : `str`
        The document's name.
    """
    if app.builder.format != "html":
        return
    opening, closing = app.config.mathjax_display
    for node in doctree.findall(nodes.math_block):
        text = node.astext()
        if r"\tag{" not in text or node.get("nowrap"):
            continue
        node["nowrap"] = True
        node.children = [nodes.Text(f"{opening}{text.strip()}{closing}")]


def _papers_page_for_print_only(app: Sphinx, env: BuildEnvironment, docnames: list[str]) -> None:
    """Leave the PDF's starting page out of every build except the PDF.

    ``papers.rst`` lists the five papers so the PDF holds only them. The
    website already lists the papers in its sections, so it must not read
    this page.

    Parameters
    ----------
    app : `sphinx.application.Sphinx`
        The running Sphinx application.
    env : `sphinx.environment.BuildEnvironment`
        The build environment.
    docnames : `list` [`str`]
        The documents about to be read, changed in place.
    """
    if app.builder.format != "latex":
        env.found_docs.discard("papers")
        if "papers" in docnames:
            docnames.remove("papers")


def setup(app: Sphinx) -> None:
    """Register the project's Sphinx event handlers.

    Parameters
    ----------
    app : `sphinx.application.Sphinx`
        The running Sphinx application.
    """
    app.connect("doctree-read", _builtin_type_xrefs)
    app.connect("builder-inited", _plain_mermaid_for_print)
    app.connect("source-read", _plain_equation_numbers_for_print)
    app.connect("doctree-resolved", _numbered_equations_unwrapped)
    app.connect("env-before-read-docs", _papers_page_for_print_only)
