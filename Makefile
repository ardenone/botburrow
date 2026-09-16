# Verification gates for the botburrow repository.
#
# `make test` exists so verification systems have a stable, allowed-prefix
# entry point: the bare `pytest` shim on this box is unusable (bad
# interpreter), so the suites are driven through `python3 -m pytest`.
# See tests/README.md for what the schema suite covers and why.

.PHONY: test

test:
	python3 -m pytest tests/ -q
