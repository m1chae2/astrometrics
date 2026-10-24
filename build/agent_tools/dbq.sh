#!/bin/bash
# Read-only SQL on the COPY database. Usage: dbq.sh "<select ...>"
#
# The database is the library database of the copy config (ASTROMETRICS_COPY_CONFIG), opened with
# SQLite's read-only mode, so a write is refused. Rows print one per line, columns separated by |.
: "${ASTROMETRICS_COPY_CONFIG:?set ASTROMETRICS_COPY_CONFIG to the copy config file}"
library=$(grep -m1 '^path = ' "$ASTROMETRICS_COPY_CONFIG" | cut -d'"' -f2)
PYTHON="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.venv/bin/python"
exec "$PYTHON" -I -c '
import sqlite3, sys
connection = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
for row in connection.execute(sys.argv[2]):
    print("|".join("" if value is None else str(value) for value in row))
' "$library/astrometrics.db" "$1"
