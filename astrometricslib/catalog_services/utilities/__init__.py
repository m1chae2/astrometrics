"""Helpers shared between more than one module in `catalog_services`.

`image_scaling.py` is here because both `image_conversions.py` (in
this package) and the visualization overlay code call it -- it is
pure numpy math with no file or database access of its own, so it
does not belong in either of its callers.
"""
