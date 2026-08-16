.DEFAULT_GOAL := help

.PHONY: help setup run test check

help:
	@printf '%s\n' "make setup  Create the local virtual environment" "make run    Launch Monitor Desktop" "make test   Run unit tests" "make check  Compile and test"

setup:
	./scripts/bootstrap.sh

run:
	./scripts/run.sh

test:
	.venv/bin/python -m unittest discover -s tests -v

check:
	.venv/bin/python -m compileall monitor_desktop run.py
	$(MAKE) test
