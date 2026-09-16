#!/usr/bin/env bash
# Definition of done for the botburrow agent runner:
# the full offline runner test suite must pass from a clean checkout.
#
# Invoked directly (scripts/definition-of-done.sh) so verification systems
# re-run exactly this; the bare `pytest` shim on some boxes is unusable,
# so the suite is driven through `python3 -m pytest`.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pytest runner/tests -q
