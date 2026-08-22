"""Demo script demonstrating clearing the stellar object library.

Removes all objects from the stellar object library.
"""

from astrometricslib import StellarCatalog

registry = StellarCatalog()
# registry.save_all([])

# Delete each object individually by ID
for obj in registry.list_objects():
    registry.delete(obj.id)
