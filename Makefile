PYTHON ?= .venv-upgrade/bin/python
.PHONY: test ci lint privacy

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check --isolated --select E4,E7,E9,F,I src/pilot tests/test_pilot_*.py

privacy:
	$(PYTHON) scripts/privacy_gate.py

ci: lint
	$(PYTHON) -m pip check
	$(PYTHON) scripts/run_local_ci.py
