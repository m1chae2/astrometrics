# Explanation

*Version 1.0 · 2026-10-15 · Status: current*

Explanation pages say how the software works and why it is built that way. The library papers give the theory without naming code; their companion implementation papers, under [Reference](reference.md), name the code and give the tuned values.

- [How the Astrometrics processing pipelines fit together](library_design/Astrometrics_Pipeline_Dataflow.md): how raw telescope exposures flow through the five processing pipelines into scientific data products.
- [Pipeline review guides](library_design/pipelines/index.md): one page per processing pipeline, with its inputs, equations, tuned limits, fallbacks and quality gates.
- [Astrometrics library architecture](library_design/Astrometrics_Library_Architecture.md): the theory behind stacking, astrometry, photometry, spectroscopy and asteroid detection.
- [Wayfinding library architecture](library_design/Wayfinding_Library_Architecture.md): the theory behind observation planning and observatory control.
- [Wayfinding quality judgment](library_design/Wayfinding_Quality_Judgment.md): the theory of how the library judges quality and recommends the next observation: statements, efficiency, the loss account, the forecast check and tonight's ranking.
- [Desktop app architecture](user_interface/technical_reference/User_Interface_Architecture.md): how the desktop app is built and how it talks to the Python backend.

```{toctree}
:hidden:

library_design/Astrometrics_Pipeline_Dataflow
library_design/pipelines/index
library_design/Astrometrics_Library_Architecture
library_design/Wayfinding_Library_Architecture
library_design/Wayfinding_Quality_Judgment
user_interface/technical_reference/User_Interface_Architecture
```
