"""Purpose: The out-of-process watchdog.

Description: Control layer. `heartbeat.py` lets the controlling process
report that it is alive; `watchdog_process.py` runs as a separate
process and starts the safe-state sequence when the heartbeat stops. It
never imports Execution code, so a hang in the session runner cannot also
disable the mechanism meant to catch it
(`Wayfinding_Library_Architecture.md`, "Watchdog Is Out Of Process").
"""
