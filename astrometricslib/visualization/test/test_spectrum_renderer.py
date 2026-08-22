"""Purpose: Unit tests for SpectrumRenderer.

Description: Verifies spectrum rendering and dynamic Balmer line toggling.
"""

import matplotlib.pyplot as plt
import numpy as np

from astrometricslib.visualization.layers.spectrum_overlay import SpectrumOverlay
from astrometricslib.visualization.visualization_config import VisualizationConfig


def test_spectrum_renderer_balmer_lines():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Tests that SpectrumOverlay correctly saves active data and renders.

    vertical Balmer lines when toggled on.
    """
    fig, ax = plt.subplots()
    config = VisualizationConfig()
    renderer = SpectrumOverlay(ax, fig, config)

    # Render a mock spectrum covering the H-alpha line (6563 Å)
    wavelengths = np.linspace(6000, 7000, 100)
    intensities = np.sin(wavelengths) + 12.0

    renderer.render_spectrum(0, "Test Star", "A", wavelengths.tolist(), intensities.tolist())

    # Initially balmer lines are hidden and artists are empty
    assert renderer.balmer_lines_visible is False
    assert len(renderer.balmer_line_artists) == 0

    # Toggle Balmer lines on
    renderer.toggle_balmer_lines(None)

    # Assert they are marked visible and the vertical line artist is created
    assert renderer.balmer_lines_visible is True
    assert len(renderer.balmer_line_artists) > 0

    # Toggle them off
    renderer.toggle_balmer_lines(None)
    assert renderer.balmer_lines_visible is False
    assert len(renderer.balmer_line_artists) == 0

    plt.close(fig)
