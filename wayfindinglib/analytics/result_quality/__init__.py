"""Purpose: Calculations that judge the quality of a target's results.

Description: Foundation layer. The rules, efficiency, noise growth and
other calculations that Control (the per-night view) and Planning (the
per-target view) share. Modules are added by later batches of issue #95.
None of them talks to hardware or imports Control, Planning or Execution
code (`Wayfinding_Library_Architecture.md` Section 2.1, rule 1).
"""
