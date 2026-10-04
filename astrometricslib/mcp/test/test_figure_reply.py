"""Tests that a plot tool's figure is sent back as a picture.

A figure sent as text reads ``Figure(1600x900)``. These tests register a
tool that returns a matplotlib figure and check the reply is a PNG image
plus a short description, and that the figure is closed afterwards.
"""

import asyncio
import base64

import matplotlib.pyplot as plt

from astrometricslib.mcp.tool_registry import ToolRegistry

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_a_figure_comes_back_as_a_png_and_is_closed() -> None:
    """The reply holds an image block and the figure is released."""
    registry = ToolRegistry()

    @registry.register("make_plot", "Draw a plot.", {"type": "object", "properties": {}, "required": []})
    def make_plot() -> object:
        """Draw a small plot.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            A figure with one titled panel.
        """
        figure, axis = plt.subplots(figsize=(4, 3))
        axis.plot([1, 2, 3], [3, 1, 2])
        axis.set_title("Light curve")
        return figure

    before = len(plt.get_fignums())
    reply = asyncio.run(registry.execute("make_plot", {}))
    kinds = [block.type for block in reply]
    assert kinds == ["image", "text"]
    assert base64.b64decode(reply[0].data).startswith(PNG_SIGNATURE)
    assert "Light curve" in reply[1].text
    assert len(plt.get_fignums()) == before
