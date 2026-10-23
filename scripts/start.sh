#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
command -v go >/dev/null || { echo 'Install Go >= 1.21'; exit 1; }
command -v python3 >/dev/null || { echo 'Install Python >= 3.11'; exit 1; }
mkdir -p data
python3 scripts/prepare_local_demo.py
export RAGUARD_LOCAL_DEMO=1
(cd mock && go build -o ../data/raguard-mock .)
./data/raguard-mock &
mock_pid=$!
gateway_pid=''
cleanup() {
    if [ -n "$gateway_pid" ]; then kill "$gateway_pid" 2>/dev/null || true; fi
    kill "$mock_pid" 2>/dev/null || true
    if [ -n "$gateway_pid" ]; then wait "$gateway_pid" 2>/dev/null || true; fi
    wait "$mock_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 scripts/wait.py
printf '另一个终端执行：python3 -m raguard.demo\n'
python3 -m raguard.http &
gateway_pid=$!
wait "$gateway_pid"
