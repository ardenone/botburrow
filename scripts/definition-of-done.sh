#!/usr/bin/env bash
# Canonical offline verification gate for botburrow.
#
# Runs the agent-runner suite (runner/tests) and the registration-tooling
# suite (scripts/tests: fixture-driven config validation covering every
# documented agent type, repo scanning, multi-repo handling, and Hub
# registration calls against an in-process stub Hub). It needs no Hub, no
# HUB_ADMIN_KEY and no OpenBao access, so it is safe to run anywhere.
#
# CI (Argo Workflows — see docs/agent-registration-cicd-automation-guide.md)
# and NEEDLE verification re-run exactly this script and fail on non-zero
# exit, so neither a runner nor a validator/registration change can regress
# silently.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m pytest runner/tests scripts/tests "$@"
