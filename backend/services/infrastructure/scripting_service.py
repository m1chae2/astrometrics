"""Interactive Python scripting environment for the application.

The Command Console runs the user's Python here. Its workspace holds the
same public objects a script or notebook would use: ``astrometrics`` (the
`astrometricslib.Astrometrics` object), ``wayfinder`` (the
`wayfindinglib.Wayfinder` object), ``np``, ``plt`` and ``fits``. The app's
internal services are not in it. The module also gives workspace lists, code
completions, figure exports, script recipes (from the tutorial notebooks and
scripts in ``documentation/notebooks/``), documentation and run history.
"""

import code
import rlcompleter
import threading
from collections import deque
from typing import Any

from backend.services.infrastructure.console_helpers import ConsoleRuns
from backend.services.infrastructure.scripting_figures import ConsoleFiguresMixin
from backend.services.infrastructure.scripting_library import ConsoleLibraryMixin
from backend.services.infrastructure.scripting_runs import ConsoleRunMixin
from backend.services.infrastructure.scripting_workspace import ConsoleWorkspaceMixin


class ScriptingService(ConsoleWorkspaceMixin, ConsoleRunMixin, ConsoleFiguresMixin, ConsoleLibraryMixin):
    """Manage an interactive Python console over the public libraries.

    Each part of the work lives in its own module: the workspace, help and
    completions in `scripting_workspace`, running and stopping code and the
    run history in `scripting_runs`, figures in `scripting_figures`, and
    recipes, saved scripts and documents in `scripting_library`. This class
    holds the shared state and the Command Console editor's text.
    """

    def __init__(self, container: Any = None) -> None:
        """Initialize the interactive scripting service.

        Parameters
        ----------
        container : `Any`, optional
            Application service container exposing domain and
            infrastructure services.
        """
        self.container = container
        # The runs going now: Ctrl+C in the terminal stops them, and show()
        # and doc() add their requests to the app to them.
        self._runs = ConsoleRuns()
        self.console = code.InteractiveConsole(locals=self._get_locals())
        self.completer = rlcompleter.Completer(self.console.locals)
        self.active_editor_code = ""
        self.active_figure_managers: dict[int, Any] = {}
        # What each figure looked like when a run last showed it.
        self._figure_signatures: dict[int, tuple[Any, ...]] = {}
        self._recent_runs: deque[dict[str, Any]] = deque()
        self._recent_runs_lock = threading.Lock()

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
