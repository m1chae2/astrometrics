"""Purpose: Pure calculations on observatory records.

Description: Foundation layer. Each module here turns plain records
(plate solves, guide pulses, guiding samples) into derived numbers such
as pointing jitter, a periodic-error spectrum or a pointing model. None
of them talks to hardware or imports Control, Planning or Execution code,
so they run the same on archived data as on a live night
(`Wayfinding_Library_Architecture.md` Section 2.1, rule 1, "Foundation
independence").
"""
