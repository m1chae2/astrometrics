"""Interactive Python scripting environment for the application.

Allows users to execute scripts that interact with the backend
services and scientific libraries, providing workspace manifests,
code completions, figure exports, script recipes, documentation,
and run scope loading. REQ: AGENT-1.1, AGENT-3.1
"""

import code
import contextlib
import inspect
import io
import json
import logging
import rlcompleter
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def inspect_api(obj_or_path: Any) -> dict[str, Any]:
    """Return API introspection details for an object or dotted path.

    Parameters
    ----------
    obj_or_path : `Any`
        An object, function, class, or module to inspect.

    Returns
    -------
    info : `dict[str, Any]`
        Structured dictionary containing name, docstring, type, and
        callable method signatures.
    """
    if obj_or_path is None:
        return {"error": "Target object is None"}

    obj = obj_or_path
    obj_name = getattr(obj, "__name__", str(obj))
    doc = inspect.getdoc(obj) or ""
    summary = doc.split("\n\n")[0] if "\n\n" in doc else doc

    methods: list[dict[str, Any]] = []
    if not callable(obj):
        for attr_name in sorted(dir(obj)):
            if attr_name.startswith("_"):
                continue
            try:
                attr = getattr(obj, attr_name)
            except Exception as exc:
                logger.debug("Attribute '%s' inaccessible during inspect_api: %s", attr_name, exc)
                continue
            if callable(attr):
                method_doc = inspect.getdoc(attr) or ""
                method_summary = method_doc.split("\n\n")[0] if "\n\n" in method_doc else method_doc
                sig_str = "(*args, **kwargs)"
                try:
                    sig_str = str(inspect.signature(attr))
                except Exception as exc:
                    logger.debug("Signature inaccessible for '%s': %s", attr_name, exc)
                methods.append({
                    "name": attr_name,
                    "signature": sig_str,
                    "summary": method_summary[:120],
                })

    sig = ""
    if callable(obj):
        try:
            sig = str(inspect.signature(obj))
        except Exception:
            sig = "(*args, **kwargs)"

    return {
        "name": obj_name,
        "type": type(obj).__name__,
        "signature": sig,
        "summary": summary[:200],
        "methods": methods,
    }


class ScriptingService:
    """Manage an interactive Python console with access to services."""

    class SmartPrinter:
        """Callable/reprable wrapper that lazily renders help text.

        Deferring text generation to `__repr__`/`__call__` lets the
        interactive console print up-to-date help (e.g. typing
        ``help`` with no parentheses) without eagerly computing it.
        """

        def __init__(self, text_factory: Any) -> None:
            self.text_factory = text_factory

        def __repr__(self) -> str:
            """Return the rendered text so bare-name repl echo works.

            Returns
            -------
            text : `str`
                The freshly generated help text.
            """
            return self.text_factory()

        def __call__(self) -> None:
            """Print the rendered text, for call-style invocation."""
            print(self.text_factory())

    def __init__(self, container: Any = None) -> None:
        """Initialize the interactive scripting service.

        Parameters
        ----------
        container : `Any`, optional
            Application service container exposing domain and
            infrastructure services.
        """
        self.container = container
        self.console = code.InteractiveConsole(locals=self._get_locals())
        self.completer = rlcompleter.Completer(self.console.locals)
        self.active_editor_code = ""

    def get_completions(self, text: str) -> list[str]:
        """Return a list of possible completions for the given text.

        Parameters
        ----------
        text : `str`
            The input prefix to complete.

        Returns
        -------
        results : `list` of `str`
            The candidate completions for `text`.
        """
        results = []
        state = 0
        while True:
            match = self.completer.complete(text, state)
            if match is None:
                break
            results.append(match)
            state += 1
        return results

    def _get_locals(self) -> dict[str, Any]:
        """Return the dictionary of objects available in the script scope.

        Returns
        -------
        local_scope : `dict[str, Any]`
            The names and objects to expose in the console.
        """

        def get_help_text() -> str:
            return self._generate_help_text()

        help_obj = self.SmartPrinter(get_help_text)

        local_scope: dict[str, Any] = {
            "help": help_obj,
            "list_commands": help_obj,
            "inspect_api": inspect_api,
            "doc": inspect_api,
        }

        # Inject standard scientific packages
        try:
            import numpy as np

            local_scope["np"] = np
        except ImportError:
            pass

        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            local_scope["plt"] = plt
        except ImportError:
            pass

        try:
            import pandas as pd

            local_scope["pd"] = pd
        except ImportError:
            pass

        try:
            import astropy

            local_scope["astropy"] = astropy
        except ImportError:
            pass

        if not self.container:
            return local_scope

        local_scope["astrometrics"] = getattr(self.container, "astrometrics", None)
        local_scope["wayfinder"] = getattr(self.container, "wayfinder", None)

        # Standard system aliases for rapid scripting
        aliases = {
            "telescope_service": "telescope",
            "image_processing_service": "image_processing",
            "target_service": "target",
            "sync_service": "sync",
            "guiding_service": "guiding",
            "imaging_service": "imaging",
            "alignment_service": "alignment",
            "observatory_service": "observatory",
            "target_imaging_planner": "planner",
            "target_imaging_executor": "executor",
            "mosaic_service": "mosaic",
            "ingestion_service": "ingestion",
            "config_service": "config",
            "system_status_service": "system",
            "job_service": "jobs",
            "stellar_object_service": "stars",
        }

        # Convenient plural shortcuts
        target_srv = getattr(self.container, "target_service", None)
        if target_srv:
            local_scope["targets"] = target_srv

        job_srv = getattr(self.container, "job_service", None)
        if job_srv:
            local_scope["jobs"] = job_srv

        stellar_srv = getattr(self.container, "stellar_object_service", None)
        if stellar_srv:
            local_scope["stars"] = stellar_srv

        for attr_name in dir(self.container):
            if attr_name.startswith("_"):
                continue

            val = getattr(self.container, attr_name)
            if val is None:
                continue

            if attr_name in aliases:
                local_scope[aliases[attr_name]] = val
            elif attr_name.endswith("_service"):
                local_scope[attr_name] = val

        return local_scope

    def _generate_help_text(self) -> str:
        """Generate the help text string.

        Returns
        -------
        help_text : `str`
            The formatted list of available objects.
        """
        lines = ["Available objects:"]
        current_locals = self.console.locals if hasattr(self, "console") else self._default_locals_keys()

        for name in sorted(current_locals):
            if not name.startswith("_"):
                lines.append(f"  - {name}")
        lines.append("\nUse dir(object_name) to see methods. Use inspect_api(obj) for signatures.")
        return "\n".join(lines)

    def _default_locals_keys(self) -> list[str]:
        """Return the default fallback keys exposed in locals.

        Returns
        -------
        keys : `list` of `str`
            List of variable names available in locals.
        """
        return [
            "help",
            "list_commands",
            "np",
            "plt",
            "pd",
            "astropy",
            "astrometrics",
            "wayfinder",
            "targets",
            "stars",
            "jobs",
            "telescope",
            "imaging",
            "guiding",
            "image_processing",
            "observatory",
            "planner",
            "executor",
            "sync",
            "config",
            "system",
        ]

    def help(self) -> None:
        """Print available commands and objects."""
        print(self._generate_help_text())

    def list_commands(self) -> None:
        """List available objects in the scripting environment."""
        print(self._generate_help_text())

    def execute(self, code_str: str) -> str:
        """Execute code in the interactive console.

        Parameters
        ----------
        code_str : `str`
            Python code to run.

        Returns
        -------
        output : `str`
            The captured stdout/stderr produced by `code_str`.
        """
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            try:
                if "\n" in code_str.strip():
                    exec(code_str, self.console.locals)  # ruff: ignore[exec-builtin]
                else:
                    self.console.push(code_str)
            except Exception:
                traceback.print_exc()
        return buffer.getvalue()

    def run_code(self, source_code: str) -> str:
        """Legacy wrapper, delegates to execute.

        Parameters
        ----------
        source_code : `str`
            Source code string to run.

        Returns
        -------
        output : `str`
            The captured stdout/stderr produced by `source_code`.
        """
        return self.execute(source_code)

    def get_introspection_tree(self) -> list[dict[str, Any]]:
        """Return a tree of available objects and their methods.

        Returns
        -------
        objects : `list` of `dict`
            One entry per console-local object, describing its type,
            docstring, and callable methods.
        """
        objects = []
        local_scope = self.console.locals

        for name, obj in local_scope.items():
            if name.startswith("_"):
                continue

            obj_info = {
                "name": name,
                "type": type(obj).__name__,
                "doc": inspect.getdoc(obj) or "",
                "methods": [],
            }

            try:
                for attr_name in dir(obj):
                    if attr_name.startswith("_"):
                        continue
                    try:
                        attr = getattr(obj, attr_name)
                    except Exception as exc:
                        logger.debug("Skipping unreadable attribute '%s': %s", attr_name, exc)
                        continue
                    if callable(attr):
                        method_info = {"name": attr_name, "doc": inspect.getdoc(attr) or "", "args": []}
                        try:
                            sig = inspect.signature(attr)
                            method_info["args"] = [str(param) for param in sig.parameters.values()]
                        except Exception:
                            method_info["args"] = ["*args", "**kwargs"]
                        obj_info["methods"].append(method_info)
            except Exception as exc:
                logger.debug("Failed to introspect methods for one console object: %s", exc)
            objects.append(obj_info)
        return objects

    def get_workspace_manifest(self) -> list[dict[str, Any]]:
        """Return a structured manifest of active objects in console scope.

        Returns
        -------
        variables : `list` of `dict`
            List of variable summaries including name, type, shape,
            dtype, size in bytes, and human-friendly string preview.
        """
        manifest = []
        ignored_names = {
            "help",
            "list_commands",
            "inspect_api",
            "doc",
            "astrometrics",
            "wayfinder",
            "telescope",
            "image_processing",
            "target",
            "targets",
            "sync",
            "guiding",
            "imaging",
            "alignment",
            "observatory",
            "planner",
            "executor",
            "mosaic",
            "ingestion",
            "config",
            "system",
            "jobs",
            "stars",
            "np",
            "plt",
            "pd",
            "astropy",
        }

        local_scope = self.console.locals
        for name, val in local_scope.items():
            if name.startswith("_") or name in ignored_names:
                continue

            type_name = type(val).__name__
            shape = getattr(val, "shape", None)
            dtype = getattr(val, "dtype", None)
            size_bytes = getattr(val, "nbytes", sys.getsizeof(val))

            summary = str(val)
            if len(summary) > 80:
                summary = summary[:77] + "..."

            manifest.append({
                "name": name,
                "type": type_name,
                "shape": str(shape) if shape is not None else None,
                "dtype": str(dtype) if dtype is not None else None,
                "size_bytes": int(size_bytes) if isinstance(size_bytes, (int, float)) else 0,
                "summary": summary,
            })
        return manifest

    def execute_structured(self, code_str: str, source: str = "repl") -> dict[str, Any]:
        """Execute code and return a structured result envelope.

        Parameters
        ----------
        code_str : `str`
            The Python code snippet to execute.
        source : `str`, optional
            The originator of the execution ('repl', 'editor', 'agent').
            Defaults to 'repl'.

        Returns
        -------
        envelope : `dict[str, Any]`
            Contains status, stdout, stderr, result, plots, and
            updated workspace manifest.
        """
        start_time = time.time()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        plots: list[str] = []
        result: Any = None
        status = "success"

        # Broadcast event to UI if execution is triggered by an agent
        # or external source
        if source == "agent" and self.container and hasattr(self.container, "socket_manager"):
            self.container.socket_manager.broadcast_ui_event_sync(
                "terminal:agent_exec",
                {"code": code_str, "timestamp": time.time()},
            )

        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            try:
                try:
                    import matplotlib

                    matplotlib.use("Agg")
                    import matplotlib.pyplot as plt
                except Exception:
                    plt = None

                if "\n" in code_str.strip():
                    exec(code_str, self.console.locals)  # ruff: ignore[exec-builtin]
                else:
                    self.console.push(code_str)

                if "result" in self.console.locals:
                    raw_res = self.console.locals["result"]
                    try:
                        json.dumps(raw_res)
                        result = raw_res
                    except TypeError, OverflowError:
                        result = str(raw_res)

                if plt and plt.get_fignums():
                    for fignum in plt.get_fignums():
                        fig = plt.figure(fignum)
                        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                            fig.savefig(tmp_file.name, bbox_inches="tight", dpi=100)
                            plots.append(tmp_file.name)
                    plt.close("all")

            except Exception:
                status = "error"
                traceback.print_exc(file=stderr_buf)

        execution_time_ms = int((time.time() - start_time) * 1000)

        # Broadcast plots to open figure windows in UI
        # if figures were generated
        if plots and self.container and hasattr(self.container, "socket_manager"):
            for plot_path in plots:
                self.container.socket_manager.broadcast_ui_event_sync(
                    "terminal:plot_generated",
                    {"plot_path": plot_path},
                )

        return {
            "status": status,
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
            "result": result,
            "plots": plots,
            "execution_time_ms": execution_time_ms,
            "workspace": self.get_workspace_manifest(),
        }

    def run_repl_command(self, command: str) -> str:
        """Run `command` through the interactive console and return output.

        Parameters
        ----------
        command : `str`
            The command string to evaluate.

        Returns
        -------
        output : `str`
            The captured stdout/stderr produced by `command`.
        """
        return self.execute(command)

    def load_job_into_scope(self, job_id: str) -> dict[str, Any]:
        """Inject a processing run and target into console scope.

        Parameters
        ----------
        job_id : `str`
            Identifier of the ProcessingJob to load.

        Returns
        -------
        info : `dict[str, Any]`
            Information about loaded job and target.

        Raises
        ------
        RuntimeError
            If backend container is unavailable.
        ValueError
            If the requested job cannot be found.
        """
        if not self.container:
            raise RuntimeError("Backend container unavailable.")

        job = None
        if hasattr(self.container, "job_service"):
            job = self.container.job_service.get_job(job_id)

        if not job:
            raise ValueError(f"Job {job_id!r} not found.")

        self.console.locals["run"] = job

        target = None
        target_name = getattr(job, "target_id", None) or getattr(job, "target_name", None)
        if target_name and hasattr(self.container, "target_service"):
            target = self.container.target_service.get_targets(target_name)
            self.console.locals["target"] = target

        return {
            "job_id": job.id,
            "job_type": job.job_type.value if hasattr(job.job_type, "value") else str(job.job_type),
            "target_name": target_name,
            "injected_variables": ["run"] + (["target"] if target is not None else []),
        }

    def _get_recipe_dirs(self) -> list[Path]:
        """Return list of recipe search paths in repo.

        Returns
        -------
        dirs : `list` of `Path`
            Search directories for recipe scripts.
        """
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        return [
            repo_root / "documentation" / "notebooks" / "astrometrics" / "scripts",
            repo_root / "documentation" / "notebooks" / "wayfinding" / "control" / "scripts",
            repo_root / "documentation" / "notebooks" / "wayfinding" / "execution" / "scripts",
        ]

    def list_recipes(self) -> list[dict[str, Any]]:
        """List built-in script recipes available in documentation notebooks.

        Returns
        -------
        recipes : `list` of `dict`
            List of recipes with name, path, category, description,
            and byte size.
        """
        recipes = []
        for recipe_dir in self._get_recipe_dirs():
            if not recipe_dir.exists():
                continue
            for py_file in sorted(recipe_dir.glob("*.py")):
                if py_file.name == "__init__.py":
                    continue

                description = ""
                try:
                    content = py_file.read_text(encoding="utf-8")
                    if content.startswith('"""') or content.startswith("'''"):
                        delim = '"""' if content.startswith('"""') else "'''"
                        parts = content.split(delim)
                        if len(parts) >= 2:
                            description = parts[1].strip().split("\n\n")[0]
                except Exception as exc:
                    logger.debug("Failed to read recipe %s docstring: %s", py_file, exc)

                category = recipe_dir.parent.name
                recipes.append({
                    "id": py_file.stem,
                    "name": py_file.stem.replace("_", " ").title(),
                    "filename": py_file.name,
                    "category": category,
                    "description": description[:160],
                    "size_bytes": py_file.stat().st_size,
                })
        return recipes

    def get_recipe(self, recipe_id: str) -> dict[str, Any]:
        """Retrieve the code contents of a script recipe.

        Parameters
        ----------
        recipe_id : `str`
            Recipe identifier or filename stem.

        Returns
        -------
        data : `dict[str, Any]`
            Recipe identifier, filename, and code text.

        Raises
        ------
        FileNotFoundError
            If the requested recipe cannot be found.
        """
        for recipe_dir in self._get_recipe_dirs():
            if not recipe_dir.exists():
                continue
            for py_file in recipe_dir.glob("*.py"):
                if py_file.stem == recipe_id or py_file.name == recipe_id:
                    return {
                        "id": py_file.stem,
                        "filename": py_file.name,
                        "code": py_file.read_text(encoding="utf-8"),
                    }
        raise FileNotFoundError(f"Recipe {recipe_id!r} not found.")

    def _get_user_scripts_dir(self) -> Path:
        """Return directory path for user scripts ~/.astrometrics/scripts.

        Returns
        -------
        dir_path : `Path`
            Path object pointing to user scripts directory.
        """
        user_dir = Path.home() / ".astrometrics" / "scripts"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def list_user_scripts(self) -> list[dict[str, Any]]:
        """List scripts saved by the user in ~/.astrometrics/scripts.

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
        """Read a user script from ~/.astrometrics/scripts.

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
        FileNotFoundError
            If the requested script does not exist.
        """
        safe_name = Path(filename).name
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        script_path = self._get_user_scripts_dir() / safe_name
        if not script_path.exists():
            raise FileNotFoundError(f"User script {safe_name!r} not found.")

        return {
            "filename": safe_name,
            "code": script_path.read_text(encoding="utf-8"),
        }

    def save_user_script(self, filename: str, content: str) -> dict[str, Any]:
        """Save user script to ~/.astrometrics/scripts.

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
        """
        safe_name = Path(filename).name
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        script_path = self._get_user_scripts_dir() / safe_name
        script_path.write_text(content, encoding="utf-8")
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
        topics = []
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
            except Exception as exc:
                logger.debug("Failed reading title for doc topic %s: %s", doc_file, exc)

            topic_id = str(doc_file.relative_to(doc_root).as_posix())
            topics.append({
                "id": topic_id,
                "title": title,
                "path": topic_id,
                "category": rel_parts[0] if len(rel_parts) > 1 else "General",
            })
        return topics

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
        FileNotFoundError
            If the requested documentation file does not exist.
        """
        doc_root = self._get_doc_root()
        safe_path = (doc_root / topic_id).resolve()
        if not safe_path.exists() or not safe_path.is_file() or not str(safe_path).startswith(str(doc_root)):
            raise FileNotFoundError(f"Documentation topic {topic_id!r} not found.")

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

    def get_editor_buffer(self) -> dict[str, Any]:
        """Retrieve active code in the Command Console editor.

        Returns
        -------
        data : `dict[str, Any]`
            The current editor text buffer.
        """
        return {"code": self.active_editor_code}

    def set_editor_buffer(self, code_content: str) -> dict[str, Any]:
        """Update the active code buffer in the Command Console editor.

        Parameters
        ----------
        code_content : `str`
            Code to set in the editor.

        Returns
        -------
        status : `dict[str, Any]`
            Confirmation and length of set code.
        """
        self.active_editor_code = code_content
        if self.container and hasattr(self.container, "socket_manager"):
            self.container.socket_manager.broadcast_ui_event_sync(
                "editor:sync",
                {"code": code_content},
            )
        return {"status": "success", "length": len(code_content)}
