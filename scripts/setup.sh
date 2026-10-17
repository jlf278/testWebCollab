#!/bin/bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
venv_dir=${CABANA_VENV:-/workspace/.cabana-venv}
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
if [ ! -x /usr/bin/chromium ]; then
  echo 'Chromium is required at /usr/bin/chromium; install it through the host package manager first.' >&2
  exit 1
fi
python3 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --no-input --cache-dir /workspace/.cabana-pip-cache -r "$repo/requirements.txt"
cd "$repo"
"$venv_dir/bin/python" -m unittest discover -s tests -v
echo 'Dependencies and fixture tests are ready. Disney authentication and live site calibration are separate prerequisites.'
