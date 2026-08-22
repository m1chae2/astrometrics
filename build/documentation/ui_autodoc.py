"""Sphinx extension rendering a UI-facing summary from an API docstring.

Provides the ``ui-action`` directive, which resolves a dotted import
path to its object, parses its numpydoc docstring, and inserts a
short parameter summary with a cross-link back to the full API
reference.
"""

import importlib

from docutils.statemachine import StringList
from numpydoc.docscrape import NumpyDocString
from sphinx.util.docutils import SphinxDirective


class UIActionDirective(SphinxDirective):
    """Render a UI action's parameter summary from its API docstring."""

    has_content = False
    required_arguments = 1

    def run(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Resolve the directive's argument and emit its summary reST.

        Returns
        -------
        nodes : `list`
            The generated docutils nodes, or a single error node if
            the target path could not be resolved.

        Raises
        ------
        ValueError
            Caught internally and reported as a docutils error node
            rather than propagated, so it never actually escapes this
            method.
        """
        target_path = self.arguments[0]

        try:
            parts = target_path.split(".")
            obj = None
            # Find the module and then the object
            for i in range(len(parts) - 1, 0, -1):
                mod_name = ".".join(parts[:i])
                try:
                    mod = importlib.import_module(mod_name)
                    obj = mod
                    for attr in parts[i:]:
                        obj = getattr(obj, attr)
                    break
                except ImportError, AttributeError:
                    continue

            if obj is None:
                raise ValueError(f"Could not resolve {target_path}")

            if not obj.__doc__:
                raise ValueError(f"No docstring found for {target_path}")

            doc = NumpyDocString(obj.__doc__)

            rst_lines = []

            parameters = doc.get("Parameters", [])
            if parameters:
                for param in parameters:
                    name = param.name
                    desc = " ".join(param.desc)
                    rst_lines.append(f"* **{name}**: {desc}")
                rst_lines.append("")

            # Add API Cross-link
            rst_lines.append(
                "*(For the deep-dive scientific algorithm definition, "
                f"see the :py:meth:`~{target_path}` API Reference)*"
            )
            rst_lines.append("")

            self.state_machine.insert_input(StringList(rst_lines, source=target_path), target_path)
            return []

        except Exception as e:
            error_msg = f"Failed to generate UI Action for {target_path}: {e!s}"
            return [self.state_machine.reporter.error(error_msg, line=self.lineno)]


def setup(app):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Register the ``ui-action`` directive.

    Returns
    -------
    metadata : `dict`
        Sphinx extension metadata (version and parallel-safety flags).
    """
    app.add_directive("ui-action", UIActionDirective)
    return {
        "version": "0.1",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
