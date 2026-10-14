"""Purpose: Observation Execution tasks.

Description: Execution layer. Queue advancement, meridian flips, fault
and guide-star-loss recovery, session recording, divergence recording
and post-session reconciliation. Execution may import Control, Planning
and the Foundation; nothing outside Execution imports it except the
composition root, so the library without this package is still a
complete planning-and-control system (`Wayfinding_Library_Architecture.md`,
Design Invariant 3, Execution Is Optional).
"""
