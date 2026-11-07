#!/usr/bin/env bash
# standin_ssh.sh
# Stand-in for `ssh` in a simulated observatory (the app's simulator sources
# and the TPM test harness). The app's one transfer code
# (StellarMateInterface) runs `ssh <host> <command>` and starts rsync with
# `-e "ssh ..."`. Both reach the telescope computer. In the simulated run the
# "telescope computer" is a folder in the throwaway directory, so this script
# drops the ssh options and the host name and runs the command on this
# computer, with HOME set to that folder. It opens no network connection.
#
# It is reached only through the PATH of the backend that the simulator or
# the harness starts. It refuses to work without ASTROMETRICS_STANDIN_HOME, refuses any
# host except 127.0.0.1 and localhost, and refuses port forwards.
#
# Refuse mode: with ASTROMETRICS_STANDIN_SSH_MODE=refuse (the offline world) it never
# runs anything. It prints what ssh prints when nothing listens on port 22 and
# exits 255, so the offline run never starts the real ssh.
set -u
while [ $# -gt 0 ]; do
  case "$1" in
    --) shift; break ;;
    -L|-R|-D|-W) echo "standin ssh: port forwards are not supported; refusing." >&2; exit 255 ;;
    -o|-p|-l|-i|-F|-J|-b|-c|-E|-e|-m|-O|-S|-w|-B|-I|-Q) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
host="${1:-}"
shift || true
if [ "${ASTROMETRICS_STANDIN_SSH_MODE:-}" = "refuse" ]; then
  echo "ssh: connect to host ${host:-localhost} port 22: Connection refused" >&2
  exit 255
fi
if [ -z "${ASTROMETRICS_STANDIN_HOME:-}" ] || [ ! -d "$ASTROMETRICS_STANDIN_HOME" ]; then
  echo "standin ssh: ASTROMETRICS_STANDIN_HOME is not set to a folder; refusing." >&2
  exit 255
fi
case "$host" in
  127.0.0.1|localhost) ;;
  *) echo "standin ssh: refusing host '$host'; only 127.0.0.1 and localhost are allowed." >&2; exit 255 ;;
esac
if [ $# -eq 0 ]; then
  echo "standin ssh: no command given; refusing an interactive login." >&2
  exit 255
fi
cd "$ASTROMETRICS_STANDIN_HOME" || exit 255
HOME="$ASTROMETRICS_STANDIN_HOME" exec sh -c "$*"
