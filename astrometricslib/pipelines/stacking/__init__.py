"""Combining many frames of a target into one stacked image.

Before any of the analysis pipelines can run, a target's raw frames
need to be checked for consistency and combined into a single stacked
image. This folder holds that step and the checks that go with it:
whether the frames match each other closely enough to combine, how
many outlier pixels to throw away, whether the final stack came out
sharp, and how well the telescope tracked the sky while shooting.
"""
