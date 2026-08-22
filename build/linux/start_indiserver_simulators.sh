#!/bin/bash
# Starts indiserver with Telescope, CCD, and Guide simulators
# Usage: ./scripts/dev/start_indiserver_simulators.sh

echo "Starting indiserver with simulators..."
indiserver -vv indi_simulator_telescope indi_simulator_ccd indi_simulator_guide indi_simulator_focus indi_simulator_wheel
