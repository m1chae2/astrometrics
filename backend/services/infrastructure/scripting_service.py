"""Interactive Python scripting environment for the application.

Allows users to execute scripts that interact with the backend
services.
"""

import code
import contextlib
import io
import logging
import rlcompleter
import traceback

logger = logging.getLogger(__name__)


class ScriptingService:
    """Manage an interactive Python console with access to services."""

    class SmartPrinter:
        """Callable/reprable wrapper that lazily renders help text.

        Deferring text generation to `__repr__`/`__call__` lets the
        interactive console print up-to-date help (e.g. typing
        ``help`` with no parentheses) without eagerly computing it.
        """

        def __init__(self, text_factory):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
            self.text_factory = text_factory

        def __repr__(self):  # ruff: ignore[missing-return-type-special-method]
            """Return the rendered text so bare-name repl echo works.

            Returns
            -------
            text : `str`
                The freshly generated help text.
            """
            return self.text_factory()

        def __call__(self):  # ruff: ignore[missing-return-type-special-method]
            """Print the rendered text, for call-style invocation."""
            print(self.text_factory())

    def __init__(self, container=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.container = container
        self.console = code.InteractiveConsole(locals=self._get_locals())
        self.completer = rlcompleter.Completer(self.console.locals)

    def get_completions(self, text: str) -> list[str]:
        """Return a list of possible completions for the given text.

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

    def _get_locals(self):  # ruff: ignore[missing-return-type-private-function]
        """Return the dictionary of objects available in the script scope.

        Returns
        -------
        local_scope : `dict`
            The names and objects to expose in the console, or an
            empty dict if no container is configured.
        """
        if not self.container:
            return {}

        def get_help_text():  # ruff: ignore[missing-return-type-private-function]
            return self._generate_help_text()

        help_obj = self.SmartPrinter(get_help_text)

        local_scope = {
            "help": help_obj,
            "list_commands": help_obj,
            "astrometrics": self.container.astrometrics,
        }

        # Dynamically expose all services from container
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
        }

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

    def _generate_help_text(self):  # ruff: ignore[missing-return-type-private-function]
        """Generate the help text string.

        Returns
        -------
        help_text : `str`
            The formatted list of available objects.
        """
        lines = ["Available objects:"]
        current_locals = self.console.locals if hasattr(self, "console") else self._default_locals_keys()

        for name in current_locals:
            if not name.startswith("_"):
                lines.append(f"  - {name}")
        lines.append("\nUse dir(object_name) to see methods.")
        return "\n".join(lines)

    def _default_locals_keys(self):  # ruff: ignore[missing-return-type-private-function]
        return [
            "help",
            "list_commands",
            "telescope",
            "image_processing",
            "target",
            "sync",
            "guiding",
            "imaging",
            "alignment",
            "observatory",
            "planner",
            "executor",
            "astronomy",
            "mosaic",
            "ingestion",
            "config",
        ]

    def help(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Print available commands and objects."""
        print(self._generate_help_text())

    def list_commands(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """List available objects in the scripting environment."""
        print(self._generate_help_text())

    def execute(self, code_str: str) -> str:
        """Execute code in the interactive console.

        Returns
        -------
        output : `str`
            The captured stdout/stderr produced by `code_str`.
        """
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            try:
                if "\n" in code_str.strip():
                    # This class *is* an interactive Python console with
                    # access to backend services -- executing arbitrary code
                    # is the intended feature, not an injection vector.
                    exec(code_str, self.console.locals)  # ruff: ignore[exec-builtin]
                else:
                    self.console.push(code_str)
            except Exception:
                traceback.print_exc()
        return buffer.getvalue()

    def run_code(self, source_code: str) -> str:
        """Legacy wrapper, delegates to execute.

        Returns
        -------
        output : `str`
            The captured stdout/stderr produced by `source_code`.
        """
        return self.execute(source_code)

    def get_introspection_tree(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return a tree of available objects and their methods.

        Returns
        -------
        objects : `list` of `dict`
            One entry per console-local object, describing its type,
            docstring, and callable methods.
        """
        import inspect

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

    def run_repl_command(self, command: str) -> str:
        """Run `command` through the interactive console and return output.

        Returns
        -------
        output : `str`
            The captured stdout/stderr produced by `command`.
        """
        return self.execute(command)
