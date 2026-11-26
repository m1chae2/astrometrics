"""The Command Console's figures: browser drawing, capture and lookup.

The console draws Matplotlib figures with the WebAgg backend, which draws on
an HTML canvas in the app's own figure windows and sends pan and zoom over a
WebSocket. `browser_pyplot` switches Matplotlib to it. `ConsoleFiguresMixin`
saves each figure a run made or changed as a PNG file, tells the open
windows about it when needed, and finds a figure's manager for the
``/figure`` pages. `ScriptingService` takes these methods from it.
"""

import tempfile
from typing import Any

from astrometricslib.drivers import scratch_directory


def browser_pyplot() -> Any:
    """Switch Matplotlib to the browser backend and give its pyplot module.

    Unlike desktop backends (Tk, Qt), which fail in a server with many
    threads, WebAgg draws on an HTML5 canvas and streams interactive events
    (pan, zoom, coordinates) over WebSockets to the app. The switch applies
    to the whole process.

    Returns
    -------
    plt : `module` or `None`
        `matplotlib.pyplot`, or `None` when Matplotlib is not installed.
    """
    try:
        import matplotlib

        matplotlib.use("module://matplotlib.backends.backend_webagg_core")
        import matplotlib.backends.backend_webagg_core as webagg_core

        webagg_core.FigureManagerWebAgg._toolbar2_class = webagg_core.NavigationToolbar2WebAgg

        import matplotlib.pyplot as plt
    except ImportError:
        return None
    return plt


class ConsoleFiguresMixin:
    """Keep track of the console's figures.

    The class that uses it sets `container`, `active_figure_managers` (the
    figure managers by figure number) and `_figure_signatures` (what each
    figure looked like when a run last showed it).
    """

    container: Any
    active_figure_managers: dict[int, Any]
    _figure_signatures: dict[int, tuple[Any, ...]]

    def get_figure_manager(self, fignum: int) -> Any:
        """Return the WebAgg figure manager for the given figure number.

        Parameters
        ----------
        fignum : `int`
            Figure number.

        Returns
        -------
        manager : `Any` or `None`
            The active FigureManagerWebAgg instance if present.
        """
        if fignum in self.active_figure_managers:
            return self.active_figure_managers[fignum]

        try:
            from matplotlib._pylab_helpers import Gcf

            mgr = Gcf.get_fig_manager(fignum)
            if mgr:
                if mgr.toolbar is None:
                    import matplotlib.backends.backend_webagg_core as webagg_core

                    mgr.toolbar = webagg_core.NavigationToolbar2WebAgg(mgr.canvas)
                    mgr.canvas.toolbar = mgr.toolbar
                self.active_figure_managers[fignum] = mgr
            return mgr
        except ImportError, ValueError, TypeError, RuntimeError:
            return None

    @staticmethod
    def _figure_signature(fig: Any) -> tuple[Any, ...]:
        """Describe a figure's make-up, to tell whether a run changed it.

        The signature holds the figure object's identity and how many parts
        each axes holds, so a new figure, or a figure with plots, labels or
        images added, differs from its earlier signature. Changing the data
        of a part that is already there is not noticed.

        Parameters
        ----------
        fig : `matplotlib.figure.Figure`
            The figure to describe.

        Returns
        -------
        signature : `tuple`
            A value that compares equal when the figure has not changed.
        """
        return (id(fig), tuple(len(axes.get_children()) for axes in fig.axes))

    def _capture_changed_figures(
        self, plt: Any, plots: list[str], interactive_plots: list[dict[str, Any]]
    ) -> None:
        """Save each figure a run made or changed, for the run's reply.

        A figure left open by an earlier run, and not changed since, is not
        saved again. Closed figures are forgotten. The figures are added to
        the two lists as they are saved, so the ones saved before an error
        are still returned.

        Parameters
        ----------
        plt : `module`
            The console's `matplotlib.pyplot`.
        plots : `list` [`str`]
            The PNG file of each figure is added here.
        interactive_plots : `list` [`dict` [`str`, `Any`]]
            For each figure, its number, PNG file, ``/figure`` address and
            size in pixels is added here.
        """
        # Forget the figures that were closed.
        open_numbers = set(plt.get_fignums())
        for closed in set(self._figure_signatures) - open_numbers:
            del self._figure_signatures[closed]
        if not open_numbers:
            return
        from matplotlib._pylab_helpers import Gcf

        for fignum in plt.get_fignums():
            fig = plt.figure(fignum)
            # A figure left open by an earlier run, and not changed since, is
            # not shown again.
            signature = self._figure_signature(fig)
            if self._figure_signatures.get(fignum) == signature:
                continue
            self._figure_signatures[fignum] = signature
            mgr = Gcf.get_fig_manager(fignum)
            if mgr:
                self.active_figure_managers[fignum] = mgr
            with tempfile.NamedTemporaryFile(
                suffix=".png", delete=False, dir=scratch_directory()
            ) as tmp_file:
                fig.savefig(tmp_file.name, bbox_inches="tight", dpi=100)
                plots.append(tmp_file.name)
                # The interactive page draws the figure at this size, so the
                # app opens its window to fit it.
                width, height = fig.canvas.get_width_height()
                interactive_plots.append({
                    "figure_id": fignum,
                    "plot_path": tmp_file.name,
                    "url": f"/figure/{fignum}",
                    "width": int(width),
                    "height": int(height),
                })

    def _broadcast_figures(self, plots: list[str], interactive_plots: list[dict[str, Any]]) -> None:
        """Tell the open windows about a run's figures.

        Each window gets a ``terminal:plot_generated`` event for each
        figure: with its interactive page when there is one, else with its
        PNG file only. Nothing is sent when there is no app container.

        Parameters
        ----------
        plots : `list` [`str`]
            The PNG files of the run's figures.
        interactive_plots : `list` [`dict` [`str`, `Any`]]
            The run's interactive figures, as `_capture_changed_figures`
            gives them.
        """
        if not (self.container and hasattr(self.container, "socket_manager")):
            return
        if interactive_plots:
            for item in interactive_plots:
                self.container.socket_manager.broadcast_ui_event_sync(
                    "terminal:plot_generated",
                    {
                        "plot_path": item["plot_path"],
                        "figure_id": item["figure_id"],
                        "interactive_url": item["url"],
                        "width": item["width"],
                        "height": item["height"],
                    },
                )
        else:
            for plot_path in plots:
                self.container.socket_manager.broadcast_ui_event_sync(
                    "terminal:plot_generated",
                    {"plot_path": plot_path},
                )
