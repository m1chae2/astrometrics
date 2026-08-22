"""Configuration tokens for the interactive visualization suite.

Centralizes hardcoded dimensions, colors, and thresholds.
"""

from pydantic import BaseModel, Field


class VisualizationConfig(BaseModel):
    """Hardcoded dimensions, colors, and thresholds for visualization.

    Attributes
    ----------
    fixed_radius : `float`
        Radius for star selection circles, in pixels (default 30.0).
    aperture_px : `float`
        Standard width for dispersion rectangles, in pixels
        (default 20.0).
    annulus_inner : `float`
        Inner radius for the background annulus, in pixels
        (default 7.0).
    annulus_outer : `float`
        Outer radius for the background annulus, in pixels
        (default 12.0).
    search_radius : `float`
        Radius for star matching/alignment, in pixels (default 100.0).
    active_color : `str`
        Color used for the active/selected star marker
        (default ``"yellow"``).
    inactive_color : `str`
        Color used for inactive star markers (default ``"red"``).
    rectangle_color : `str`
        Color used for dispersion rectangles (default ``"lime"``).
    crosshair_color : `str`
        Color used for the crosshair marker (default ``"red"``).
    balmer_color : `str`
        Color used to highlight Balmer series lines (default
        ``"cyan"``).
    button_color : `str`
        Background color for UI buttons (default ``"#222222"``).
    button_hover : `str`
        Background color for UI buttons on hover (default
        ``"#444444"``).
    default_percentile : `float`
        Upper percentile used to clip image display intensity, paired
        with a fixed 1st-percentile lower clip (default 99.5).
    picker_tolerance : `float`
        Tolerance, in pixels, for picking a star with the pointer
        (default 5.0).
    """

    # Dimensions (Pixels)
    fixed_radius: float = Field(default=30.0, description="Radius for star selection circles")
    aperture_px: float = Field(default=20.0, description="Standard width for dispersion rectangles")
    annulus_inner: float = Field(default=7.0, description="Inner radius for background annulus")
    annulus_outer: float = Field(default=12.0, description="Outer radius for background annulus")
    search_radius: float = Field(default=100.0, description="Radius for star matching/alignment")

    # Aesthetics
    active_color: str = Field(default="yellow")
    inactive_color: str = Field(default="red")
    rectangle_color: str = Field(default="lime")
    crosshair_color: str = Field(default="red")
    balmer_color: str = Field(default="cyan")
    button_color: str = Field(default="#222222")
    button_hover: str = Field(default="#444444")

    # Behavior
    default_percentile: float = Field(default=99.5)
    picker_tolerance: float = Field(default=5.0)
