#!/bin/zsh
cd "${0:A:h}"
exec .venv-upgrade/bin/python -m src.pilot.desktop
