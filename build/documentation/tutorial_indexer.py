"""Purpose: Sphinx extension that writes the notebook tutorials index.

Description: At the start of each build it finds every notebook under
``documentation/notebooks/`` and writes ``documentation/notebooks/index.md``:
one card per notebook, grouped by library, and a hidden toctree.
"""

import json
import logging
import textwrap
from pathlib import Path
from typing import Any

from sphinx.application import Sphinx

logger = logging.getLogger(__name__)

# Matches this repo's other prose line-length convention (see pyproject.toml's
# doc-line-too-long rule) purely so the wrapped width looks intentional, not
# because MyST/CommonMark cares -- a paragraph is every consecutive run of
# non-blank lines regardless of where each one happens to end.
_PROSE_WRAP_WIDTH = 79


def generate_tutorials_index(app: Sphinx) -> None:
    """Find the notebooks and write ``notebooks/index.md``.

    Parameters
    ----------
    app : `sphinx.application.Sphinx`
        The running Sphinx application; its source folder holds the notebooks.
    """
    doc_dir = Path(app.srcdir)
    notebooks_dir = doc_dir / "notebooks"
    if not notebooks_dir.exists():
        return

    sections = {
        "Astrometrics Library Tutorials": {
            "user_guide": ("User Guide", []),
        },
        "Wayfinding Library Tutorials": {
            "planning": ("Observation Planning & Mosaics", []),
            "control": ("Hardware Control & Remote Ingestion", []),
            "execution": ("Session Execution & Automation", []),
        },
    }

    for nb_path in sorted(notebooks_dir.rglob("*.ipynb")):
        rel_path = nb_path.relative_to(doc_dir)
        doc_link = "/" + str(rel_path.with_suffix(""))

        # Parse notebook title and summary from first markdown cell
        title = nb_path.stem.replace("_", " ").title()
        summary = "Executable Jupyter Notebook tutorial."
        try:
            with open(nb_path, encoding="utf-8") as f:
                data = json.load(f)
                for cell in data.get("cells", []):
                    if cell.get("cell_type") == "markdown":
                        lines = cell.get("source", [])
                        if isinstance(lines, str):
                            lines = lines.splitlines()
                        for i, line in enumerate(lines):
                            sline = line.strip()
                            if sline.startswith("# "):
                                title = sline.lstrip("# ").strip()
                                for next_line in lines[i + 1 :]:
                                    next_s = next_line.strip()
                                    if next_s and not next_s.startswith("#") and not next_s.startswith("---"):
                                        summary = next_s
                                        break
                                break
                        break
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            # OSError: the file cannot be read. ValueError: it is not JSON.
            # AttributeError and TypeError: the JSON is not a notebook.
            logger.debug("Failed to parse title/summary from '%s': %s", nb_path, exc)

        parent_name = nb_path.parent.name
        for _group, cats in sections.items():
            if parent_name in cats:
                cats[parent_name][1].append({
                    "title": title,
                    "summary": summary,
                    "link": doc_link,
                })

    intro_paragraph = (
        "These tutorials are Jupyter notebooks that call the two Python libraries "
        "directly. They cover image processing, catalog queries, photometry, "
        "spectroscopy, asteroid detection, observation planning and telescope control."
    )
    usage_paragraph = (
        "Each notebook lives in `documentation/notebooks/`. Open it in VS Code or "
        "JupyterLab to run it, or choose a card below to read it here."
    )

    out = [
        "# Notebook tutorials",
        "",
        "*Generated from the notebooks at each documentation build.*",
        "",
        *textwrap.fill(intro_paragraph, width=_PROSE_WRAP_WIDTH).splitlines(),
        "",
        *textwrap.fill(usage_paragraph, width=_PROSE_WRAP_WIDTH).splitlines(),
        "",
        "---",
        "",
    ]

    for group_title, cats in sections.items():
        out.append(f"## {group_title}")
        out.append("")
        out.append("::::{grid} 1 2 2 2")
        out.append(":gutter: 3")
        out.append("")
        for _cat_key, (_cat_label, items) in cats.items():
            for item in items:
                out.append(f":::{'{grid-item-card}'} {item['title']}")
                out.append(f":link: {item['link']}")
                out.append(":link-type: doc")
                out.append("")
                out.append(item["summary"])
                out.append(":::")
                out.append("")
        out.append("::::")
        out.append("")
        out.append("---")
        out.append("")

    out.append("```{toctree}")
    out.append(":maxdepth: 2")
    out.append(":hidden:")
    out.append("")
    for _group_title, cats in sections.items():
        for _cat_key, (_cat_label, items) in cats.items():
            for item in items:
                out.append(item["link"])
    out.append("```")
    out.append("")

    index_path = doc_dir / "notebooks" / "index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def setup(app: Sphinx) -> dict[str, Any]:
    """Register Sphinx extension hooks.

    Parameters
    ----------
    app : `sphinx.application.Sphinx`
        The running Sphinx application.

    Returns
    -------
    metadata : `dict`
        Sphinx extension metadata (version and parallel-safety flags).
    """
    app.connect("builder-inited", generate_tutorials_index)
    return {
        "version": "0.1",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
