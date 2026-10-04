#!/usr/bin/env bash
# Run with: bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ ! -x .venv/bin/python ]]; then
  "${PYTHON:-python3}" -m venv .venv
fi
if ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)'; then
  echo 'FAIL Python: install Python 3.13+, rename the old .venv and rerun setup.' >&2
  exit 1
fi
.venv/bin/python -m pip install .
if [[ ! -e .env ]]; then
  cp .env.example .env
fi
.venv/bin/python scripts/doctor.py --offline
