"""Sphinx extension to dynamically generate the tutorials/index.md page."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_tutorials_index(app):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Automatically discover notebooks and generate tutorials/index.md."""
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
        except Exception as exc:
            logger.debug("Failed to parse title/summary from '%s': %s", nb_path, exc)

        parent_name = nb_path.parent.name
        for _group, cats in sections.items():
            if parent_name in cats:
                cats[parent_name][1].append({
                    "title": title,
                    "summary": summary,
                    "link": doc_link,
                })

    out = [
        "# Learn Astrometrics: Interactive Notebook Tutorials",
        "",
        "Welcome to **Learn Astrometrics**, an automatically discovered collection of ",
        "executable Jupyter Notebook tutorials (`.ipynb`) covering scientific image ",
        "processing, target catalog queries, photometry, spectroscopy, moving object ",
        "recovery, observation planning, and telescope control using `astrometricslib` ",
        "and `wayfindinglib`.",
        "",
        "Each tutorial is an executable Jupyter Notebook stored in the ",
        "`documentation/notebooks/` directory. You can open and run them interactively ",
        "in VS Code or JupyterLab, or click any card below to view the rendered ",
        "notebook in the browser.",
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


def setup(app):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Register Sphinx extension hooks.

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
