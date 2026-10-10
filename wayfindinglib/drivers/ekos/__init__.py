"""Purpose: Ekos session-log integration.

Description: Reads the log files the Ekos control software (part of KStars)
writes on the telescope computer. The guiding log is PHD2-format and is
read by `wayfindinglib.drivers.phd2.guide_log_parser`; this package reads
the Ekos "analyze" log (analyze_log_parser), which records the rest of a
session: mount pointing, temperature, exposures, autofocus runs and the
plate-solve and guider state changes.
"""
