# Ekos session logs

This folder reads the log files that Ekos writes on the telescope computer. Ekos is the control software in KStars that currently runs the observatory. These readers only record what Ekos did. They send no commands and compute no corrections.

Ekos writes two kinds of file:

- **Guide logs** (`guide_log-*.txt`) record the guiding error frame by frame. They use the PHD2 log format, so `wayfindinglib/drivers/phd2/guide_log_parser.py` reads them. They are not read here.
- **Analyze logs** (`ekos-*.analyze`) record the rest of a session. `analyze_log_parser.py` reads them.
- **KStars text logs** (`log_*.txt`) record what each Ekos module did, in words, when logging is on. `kstars_log_parser.py` reads the guider's dither messages from them.

## What `analyze_log_parser.py` does

1. Reads the file as text and ignores the run of NUL bytes that Ekos sometimes leaves at the end.
2. Converts the start time to an exact moment. Ekos writes local time with a zone abbreviation such as `MDT`. The parser turns the abbreviation into a fixed offset from UTC, so daylight saving time cannot shift the result.
3. Reads each record. Each record's time is the start time plus the seconds written on its line.
4. Returns an `EkosSessionContext` and a list of guide statistics. The context holds the mount position, temperature readings, finished and aborted exposures, autofocus runs, and the plate-solve, guider and mount state changes.

A record the parser cannot read is skipped. The other records in the file still load. A file with no start time, or an empty file, returns `None`, because no record in it could be placed in time.

## What `kstars_log_parser.py` does

A dither is a small deliberate move of the guide star between exposures. It spreads the sensor's fixed pattern across the stack. The guider logs each dither as a fixed set of messages: the start with its size, a warning if the guide star never reached its new position, the wait before the next exposure, and a completion message.

The parser turns each set into one `DitherEvent`. The event records the start time, the requested size in guide pixels, whether the dither worked, the settle time, and the duration. A dither that the log cuts off keeps `succeeded=None`. Log times carry a zone abbreviation, which the parser converts the same way the analyze parser does.

## Measurements the parser produces

- **Half-flux radius** of an exposure, in pixels. It measures how wide the stars are. A smaller value means sharper stars. Ekos writes `-1` when it could not measure it, and the parser stores `None` in that case.
- **Eccentricity** of an exposure, from 0 (round stars) toward 1 (stretched stars). Ekos writes `-1` when it could not measure it, and the parser stores `None`.
- **Autofocus run** with its measured curve and the position Ekos chose. A run that was aborted has no chosen position.

Later code reads these through `control.history.query(kind="ekos_sessions")`. The equipment-specific limits that judge them come from the performance envelope (`wayfindinglib/analytics/performance_envelope.py`).

## What is deliberately not kept

Ekos writes the mount's right ascension and hour angle in mixed units: hours in some rows and degrees in others. The parser keeps declination, altitude, azimuth and pier side, which are consistent, and drops the other two.

For exact behavior, read the code. The code is the source of truth.
