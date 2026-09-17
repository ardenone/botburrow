#!/usr/bin/env bash
# Canonical offline verification gate for botburrow.
#
# Runs the agent-runner suite (runner/tests), the registration-tooling
# suite (scripts/tests: fixture-driven config validation covering every
# documented agent type, repo scanning, multi-repo handling, and Hub
# registration calls against an in-process stub Hub), and the Hub database
# schema/repository suite (tests/). It needs no Hub, no HUB_ADMIN_KEY and
# no OpenBao access, so it is safe to run anywhere.
#
# Deliberately NOT here: hub/tests/ (the assembled-app API suite). It needs
# a fastapi/starlette pair the system interpreter cannot provide (system
# starlette 1.6 is incompatible with fastapi 0.115 — Router.__init__ no
# longer accepts on_startup), so it runs inside .venv/ only:
#   LD_LIBRARY_PATH=<nix gcc lib>/lib .venv/bin/python -m pytest hub/tests/
# Putting it under this gate would make the gate fail everywhere the venv
# is absent, and a verification gate that only passes in one checkout is
# worth nothing.
#
# CI (Argo Workflows — see docs/agent-registration-cicd-automation-guide.md)
# and NEEDLE verification re-run exactly this script and fail on non-zero
# exit, so neither a runner nor a validator/registration change can regress
# silently.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pytest runner/tests scripts/tests "$@"
python3 -m pytest tests/ "$@"
