"""Purpose: User script for launching interactive combined dashboard.

Description: Demonstrates high-level plot_target_dashboard API usage for
astrometry, photometry, and spectroscopy analysis.

**Architecture Note:** This script demonstrates the **Visualization Layer**.
It relies on the data processed by the other scripts and stored in the
database, leveraging the `astrometricslib.visualization` namespace to
present the analysis results back to the user without doing any
processing itself.
"""

import matplotlib.pyplot as plt

from astrometricslib import Astrometrics

astrometrics = Astrometrics()
target = astrometrics.targets.get("M 57")
if not target:
    raise ValueError("Target 'M 57' not found in library.")

# astrometrics.visualization.plot_target_dashboard(target, limit=30)
astrometrics.visualization.plot_spectroscopy(target)

plt.show()
