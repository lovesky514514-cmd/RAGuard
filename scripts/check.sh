#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
python3 scripts/check_utf8.py
python3 -m compileall -q raguard tests bench audit scripts
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s bench -p "test_*.py" -v
(cd mock && go test ./... && go vet ./...)
echo 'Checks passed.'
