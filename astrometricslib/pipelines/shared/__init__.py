"""Pieces more than one pipeline needs, so no pipeline owns them alone.

Grouping frames into observing sessions, saving a found star to the
shared catalog, and the per-image working state every pipeline builds
up as it runs -- none of these belong to just one pipeline, so they
live here instead of inside any single pipeline's own folder.
"""
