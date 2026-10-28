#!/bin/sh
# StateCat demo: run this in an empty folder with statecat.py beside it.
# It creates a demo project, adds one item per bucket, then shows
# what a fresh session reads in one go. No network, no account.
set -e
python3 statecat.py init "Demo Bakery"
python3 statecat.py add Decided "Sell at the Saturday market" --because "foot traffic is highest there"
python3 statecat.py add Open "Bake the test batch"
python3 statecat.py add "Waiting on human" "Pick a launch date"
python3 statecat.py add Parked "Mobile app"
echo "=== Fresh session, one read ==="
python3 statecat.py show
python3 statecat.py check
