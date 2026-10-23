#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
python3 -m bench.faultmatrix --mode smoke --repeats 2 --output results/smoke
python3 -m bench.workflows
printf '数据已生成：results/smoke/REPORT.md 和 results/workflows.csv\n'
