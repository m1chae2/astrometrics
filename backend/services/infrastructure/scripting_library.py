"""The Command Console's reading matter: recipes, saved scripts and documents.

Recipes are the tutorial notebooks and scripts in
``documentation/notebooks/``; a notebook comes back as one script. Saved
scripts are the user's own files in their data folder. Documents are the
Markdown guides under ``documentation/`` plus the generated API topics.
`ScriptingService` takes these methods from `ConsoleLibraryMixin`.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from astrometricslib.exceptions import NotFoundError

logger = logging.getLogger(__name__)

#: The folder that holds the tutorial notebooks and the recipe scripts.
NOTEBOOKS_ROOT = Path(__file__).resolve().parents[3] / "documentation" / "notebooks"


def _notebook_recipe_code(notebook_path: Path) -> tuple[str, str, str]:
    """Turn a tutorial notebook into a console script.

    The code cells become the script, in order. Each text cell becomes a
    block of ``#`` comments above the code it explains. A line that makes a
    new ``Astrometrics()`` or ``Wayfinder()`` object is turned into a
    comment, because the console already holds the app's own objects under
    those names; a second copy would open the library and the observatory
    again.

    Parameters
    ----------
    notebook_path : `Path`
        The ``.ipynb`` file.

    Returns
    -------
    title : `str`
        The notebook's first heading, or its file name.
    description : `str`
        The first paragraph after that heading.
    code : `str`
        The script.

    Raises
    ------
    ValueError
        If the file is not a notebook.
    """
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValueError(f"{notebook_path.name} has no cells.")
    title = notebook_path.stem.replace("_", " ")
    description = ""
    found_heading = False
    script_lines = [
        f'"""Recipe made from the tutorial notebook {notebook_path.relative_to(NOTEBOOKS_ROOT).as_posix()}.',
        "",
        "The console already holds `astrometrics` and `wayfinder`, so the",
        "notebook's lines that make new ones are comments here.",
        '"""',
        "",
    ]
    for cell in cells:
        source = cell.get("source", "")
        text = "".join(source) if isinstance(source, list) else str(source)
        if cell.get("cell_type") == "markdown":
            paragraphs = [part.strip() for part in text.strip().split("\n\n") if part.strip()]
            if paragraphs and paragraphs[0].startswith("#") and not found_heading:
                found_heading = True
                title = paragraphs[0].splitlines()[0].lstrip("#").strip()
                if len(paragraphs) > 1:
                    description = " ".join(paragraphs[1].split())
            script_lines += [f"# {line}".rstrip() for line in text.strip().splitlines()]
            script_lines.append("")
        elif cell.get("cell_type") == "code":
            for line in text.splitlines():
                stripped = line.strip()
                if stripped in ("astrometrics = Astrometrics()", "wayfinder = Wayfinder()"):
                    indent = line[: len(line) - len(line.lstrip())]
                    line = f"{indent}# {stripped}  (the console already has this object)"
                script_lines.append(line)
            script_lines.append("")
    return title, description, "\n".join(script_lines).rstrip() + "\n"


class ConsoleLibraryMixin:
    """List and read recipes, the user's scripts and the documentation.

    The class that uses it sets `container`, used to tell the open windows
    when a script is saved.
    """

    container: Any

    def _get_recipe_dirs(self) -> list[Path]:
        """Return the folders that hold the recipe scripts.

        Returns
        -------
        dirs : `list` of `Path`
            The ``scripts`` folders of the tutorial notebooks.
        """
        return [
            NOTEBOOKS_ROOT / "astrometrics" / "scripts",
            NOTEBOOKS_ROOT / "wayfinding" / "control" / "scripts",
            NOTEBOOKS_ROOT / "wayfinding" / "execution" / "scripts",
        ]

    def _recipe_files(self) -> list[Path]:
        """List every recipe file: the tutorial notebooks, then the scripts.

        The notebooks are the ones a library user reads in
        ``documentation/notebooks/``, so the console's recipes and the
        notebooks show the same examples.

        Returns
        -------
        files : `list` of `Path`
            The ``.ipynb`` notebooks in sorted order, then the ``.py``
            recipe scripts.
        """
        notebooks = (
            sorted(path for path in NOTEBOOKS_ROOT.rglob("*.ipynb") if ".ipynb_checkpoints" not in path.parts)
            if NOTEBOOKS_ROOT.exists()
            else []
        )
        scripts = [
            path
            for recipe_dir in self._get_recipe_dirs()
            if recipe_dir.exists()
            for path in sorted(recipe_dir.glob("*.py"))
            if path.name != "__init__.py"
        ]
        return [*notebooks, *scripts]

    @staticmethod
    def _recipe_category(path: Path) -> str:
        """Name the library a recipe belongs to.

        Parameters
        ----------
        path : `Path`
            The recipe file.

        Returns
        -------
        category : `str`
            ``astrometrics`` or ``wayfinding``: the first folder under
            ``documentation/notebooks/``.
        """
        try:
            return path.relative_to(NOTEBOOKS_ROOT).parts[0]
        except ValueError, IndexError:
            return path.parent.name

    def list_recipes(self) -> list[dict[str, Any]]:
        """List the built-in recipes: the tutorial notebooks and scripts.

        Returns
        -------
        recipes : `list` of `dict`
            One entry per recipe with ``id``, ``name``, ``filename``,
            ``category`` (the library), ``kind`` (``notebook`` or
            ``script``), ``description`` and ``size_bytes``.
        """
        recipes = []
        for recipe_file in self._recipe_files():
            description = ""
            name = recipe_file.stem.replace("_", " ").title()
            kind = "notebook" if recipe_file.suffix == ".ipynb" else "script"
            try:
                if kind == "notebook":
                    name, description, _code = _notebook_recipe_code(recipe_file)
                else:
                    content = recipe_file.read_text(encoding="utf-8")
                    if content.startswith('"""') or content.startswith("'''"):
                        delim = '"""' if content.startswith('"""') else "'''"
                        parts = content.split(delim)
                        if len(parts) >= 2:
                            description = parts[1].strip().split("\n\n")[0]
            except (OSError, ValueError) as exc:
                logger.debug("Failed to read recipe %s: %s", recipe_file, exc)
                continue
            recipes.append({
                "id": recipe_file.stem,
                "name": name,
                "filename": recipe_file.name,
                "category": self._recipe_category(recipe_file),
                "kind": kind,
                "description": " ".join(description.split())[:160],
                "size_bytes": recipe_file.stat().st_size,
            })
        return recipes

    def get_recipe(self, recipe_id: str) -> dict[str, Any]:
        """Retrieve the code of a recipe, as a script the console can run.

        A notebook comes back as one script (see `_notebook_recipe_code`).

        Parameters
        ----------
        recipe_id : `str`
            Recipe identifier or file name.

        Returns
        -------
        data : `dict[str, Any]`
            Recipe identifier, file name (``.py`` for a notebook too) and
            code text.

        Raises
        ------
        NotFoundError
            If the requested recipe cannot be found.
        """
        for recipe_file in self._recipe_files():
            if recipe_file.stem != recipe_id and recipe_file.name != recipe_id:
                continue
            if recipe_file.suffix == ".ipynb":
                _title, _description, code_text = _notebook_recipe_code(recipe_file)
                return {"id": recipe_file.stem, "filename": f"{recipe_file.stem}.py", "code": code_text}
            return {
                "id": recipe_file.stem,
                "filename": recipe_file.name,
                "code": recipe_file.read_text(encoding="utf-8"),
            }
        raise NotFoundError(f"Recipe {recipe_id!r} not found.")

    def _get_user_scripts_dir(self) -> Path:
        """Return the folder for the user's saved scripts.

        It is ``scripts`` inside the user's data folder,
        ``~/.local/share/astrometrics/scripts`` (or under ``$XDG_DATA_HOME``
        when that is set).

        Returns
        -------
        dir_path : `Path`
            Path object pointing to user scripts directory.
        """
        data_root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        user_dir = Path(data_root) / "astrometrics" / "scripts"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def list_user_scripts(self) -> list[dict[str, Any]]:
        """List scripts saved by the user in their scripts folder.

        Returns
        -------
        scripts : `list` of `dict`
            List of user script summaries.
        """
        user_dir = self._get_user_scripts_dir()
        scripts = []
        for py_file in sorted(user_dir.glob("*.py")):
            scripts.append({
                "id": py_file.stem,
                "name": py_file.stem.replace("_", " ").title(),
                "filename": py_file.name,
                "size_bytes": py_file.stat().st_size,
                "modified_at": py_file.stat().st_mtime,
            })
        return scripts

    def read_user_script(self, filename: str) -> dict[str, Any]:
        """Read a user script from ~/.local/share/astrometrics/scripts.

        Parameters
        ----------
        filename : `str`
            The script filename (e.g. 'my_analysis.py').

        Returns
        -------
        data : `dict[str, Any]`
            Filename and code content.

        Raises
        ------
        NotFoundError
            If the requested script does not exist.
        """
        safe_name = Path(filename).name
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        script_path = self._get_user_scripts_dir() / safe_name
        if not script_path.exists():
            raise NotFoundError(f"User script {safe_name!r} not found.")

        return {
            "filename": safe_name,
            "code": script_path.read_text(encoding="utf-8"),
        }

    def save_user_script(self, filename: str, content: str) -> dict[str, Any]:
        """Save user script to ~/.local/share/astrometrics/scripts.

        Parameters
        ----------
        filename : `str`
            The script filename.
        content : `str`
            Python code to save.

        Returns
        -------
        data : `dict[str, Any]`
            Filename and save status.

        Notes
        -----
        After the file is written, the service broadcasts a ``scripts:changed``
        event with the file name, so open Command Consoles reload their list.
        """
        safe_name = Path(filename).name
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        script_path = self._get_user_scripts_dir() / safe_name
        script_path.write_text(content, encoding="utf-8")
        # Tell every open window, so the User Scripts list shows the new file.
        if self.container and hasattr(self.container, "socket_manager"):
            self.container.socket_manager.broadcast_ui_event_sync(
                "scripts:changed",
                {"filename": safe_name},
            )
        return {
            "filename": safe_name,
            "size_bytes": script_path.stat().st_size,
            "status": "saved",
        }

    def _get_doc_root(self) -> Path:
        """Return root documentation path.

        Returns
        -------
        doc_dir : `Path`
            Path pointing to the repository's documentation directory.
        """
        return Path(__file__).resolve().parent.parent.parent.parent / "documentation"

    def list_doc_topics(self) -> list[dict[str, Any]]:
        """List markdown documentation guides available for in-console reading.

        Returns
        -------
        topics : `list` of `dict`
            List of documentation topics with ID, title, and relative path.
        """
        doc_root = self._get_doc_root()
        from .api_doc_generator import get_api_topics

        topics: list[dict[str, Any]] = [dict(t) for t in get_api_topics()]
        for doc_file in sorted(doc_root.rglob("*.md")):
            # Ignore build artifacts
            rel_parts = doc_file.relative_to(doc_root).parts
            if any(part.startswith(("_", ".")) for part in rel_parts):
                continue

            title = doc_file.stem.replace("_", " ").replace("-", " ").title()
            try:
                for line in doc_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
            except (OSError, ValueError) as exc:
                logger.debug("Failed reading title for doc topic %s: %s", doc_file, exc)

            topic_id = str(doc_file.relative_to(doc_root).as_posix())
            if any(t["id"] == topic_id for t in topics):
                continue

            topics.append({
                "id": topic_id,
                "title": title,
                "path": topic_id,
                "category": rel_parts[0] if len(rel_parts) > 1 else "General",
            })
        return topics

    def search_doc_topics(self, query: str) -> list[dict[str, Any]]:
        """Find doc topics whose title, path, category, or body match `query`.

        Parameters
        ----------
        query : `str`
            Case-insensitive search text. An empty or blank query
            returns every topic, unfiltered.

        Returns
        -------
        matches : `list` of `dict`
            Topics from `list_doc_topics`, in the same order, each with
            an added `snippet` key (text surrounding the match) when
            the match was found in the body text rather than the
            title, path, or category.
        """
        topics = self.list_doc_topics()
        trimmed = query.strip().lower()
        if not trimmed:
            return topics

        matches: list[dict[str, Any]] = []
        for topic in topics:
            if (
                trimmed in topic["title"].lower()
                or trimmed in topic["path"].lower()
                or trimmed in (topic.get("category") or "").lower()
            ):
                matches.append(topic)
                continue

            try:
                content = self.get_doc_topic(topic["id"])["content"]
            except NotFoundError:
                continue

            match_pos = content.lower().find(trimmed)
            if match_pos == -1:
                continue

            snippet_start = max(0, match_pos - 40)
            snippet_end = min(len(content), match_pos + len(trimmed) + 40)
            snippet = content[snippet_start:snippet_end].replace("\n", " ").strip()
            matches.append({**topic, "snippet": snippet})

        return matches

    def get_doc_topic(self, topic_id: str) -> dict[str, Any]:
        """Retrieve markdown content for a documentation topic.

        Parameters
        ----------
        topic_id : `str`
            Relative path or topic ID.

        Returns
        -------
        data : `dict[str, Any]`
            Topic ID, title, and markdown content.

        Raises
        ------
        NotFoundError
            If the requested documentation file does not exist.
        """
        from .api_doc_generator import get_api_topic_content

        # 1. Check virtual / generated API documentation
        api_topic = get_api_topic_content(topic_id)
        if api_topic is not None:
            return api_topic

        # 2. Check filesystem documentation
        doc_root = self._get_doc_root()
        safe_path = (doc_root / topic_id).resolve()
        if not safe_path.exists() or not safe_path.is_file():
            fallback_md = (doc_root / f"{topic_id}.md").resolve()
            if fallback_md.exists() and fallback_md.is_file():
                safe_path = fallback_md
            else:
                raise NotFoundError(f"Documentation topic {topic_id!r} not found.")

        if not str(safe_path).startswith(str(doc_root)):
            raise NotFoundError(f"Documentation topic {topic_id!r} not found.")

        content = safe_path.read_text(encoding="utf-8")
        title = safe_path.stem.replace("_", " ").replace("-", " ").title()
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        return {
            "id": topic_id,
            "title": title,
            "content": content,
        }
