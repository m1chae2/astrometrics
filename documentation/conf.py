"""Sphinx documentation configuration file for Astrometrics.

Includes PyData Sphinx Theme, numpydoc, sphinx-automodapi,
MyST-NB notebook execution, and InterSphinx mappings to
Astropy, NumPy, SciPy, and Matplotlib.
"""

import os
import sys

# Insert root project path into sys.path to enable Sphinx autodoc reflection
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("../build/documentation"))

# Project information
project = "Astrometrics"
copyright = "2026, Astrometrics"
author = "Astrometrics"
version = "2.0.0"
release = "2.0.0"

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
    "ui_autodoc",
    "tutorial_indexer",
]


# Source suffix options
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "myst-nb",
    ".ipynb": "myst-nb",
}

# Template and static paths
templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "README.md", ".venv"]

html_static_path = ["_static"]
html_css_files = ["custom.css"]

html_theme = "pydata_sphinx_theme"
html_logo = "_static/logo.png"
html_favicon = "_static/logo.png"
html_title = "Astrometrics Documentation"
html_context = {
    "default_mode": "dark",
}
html_theme_options = {
    "header_links_before_dropdown": 6,
    "navbar_align": "left",
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
}

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

# Tutorial notebooks intentionally carry no `language_info`/`kernelspec`
# metadata, so myst-nb cannot pick a Pygments lexer for their code cells and
# emits a "No source code lexer found" warning per cell. myst-nb has no
# config hook to supply a default lexer (see the TODO in
# myst_nb.core.render._get_nb_source_code_lexer), so the warning is
# suppressed rather than restoring metadata that was deliberately dropped.
suppress_warnings = ["myst-nb.lexer"]

myst_enable_extensions = [
    "dollarmath",
    "amsmath",
    "colon_fence",
    "html_admonition",
    "fieldlist",
]

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
