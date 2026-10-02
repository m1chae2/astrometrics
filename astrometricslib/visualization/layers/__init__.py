"""The overlays (stars, a spectrum, etc.) drawn on top of a plotted image."""

from .dispersion_overlay import DispersionOverlay
from .image_overlay import ImageOverlay
from .photometry_overlay import PhotometryOverlay
from .spectrum_overlay import SpectrumOverlay
from .star_overlay import StarOverlay, resolve_star_radius
from .star_selection_overlay import StarSelectionOverlay
from .track_overlay import TrackOverlay

__all__ = [
    "DispersionOverlay",
    "ImageOverlay",
    "PhotometryOverlay",
    "SpectrumOverlay",
    "StarOverlay",
    "StarSelectionOverlay",
    "TrackOverlay",
    "resolve_star_radius",
]
