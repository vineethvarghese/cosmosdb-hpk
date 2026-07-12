#!/usr/bin/env bash
# End-to-end orchestrator — reproduces the published run in ONE command:
#   deploy -> generate -> scale-down -> query (both SDKs) -> build report.
#
# Prereq: az login && az account set -s <SUBSCRIPTION>.
# Defaults reproduce the published ~1.27M-doc / 10-partition run. Override any knob
# via the environment for a cheaper/smaller run, e.g.:
#   COSMOS_MAX_THROUGHPUT=20000 GEN_ORDERS_PER_DAY=200 ./run-all.sh
# Skip stages with SKIP_DEPLOY=1 / SKIP_GEN=1 / SKIP_SCALEDOWN=1.
# Skip the cost prompt in automation with CONFIRM=1.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

step() { echo; echo "==================== $* ===================="; }

MAX_THROUGHPUT="${COSMOS_MAX_THROUGHPUT:-100000}"
TARGET_MAX="${TARGET_MAX:-10000}"

# Cost gate: provisioning bills real money. Require confirmation unless CONFIRM=1
# or the deploy stage is skipped.
if [[ "${SKIP_DEPLOY:-0}" != "1" && "${CONFIRM:-0}" != "1" ]]; then
  cat <<WARN

  This provisions Azure Cosmos DB at ${MAX_THROUGHPUT} RU/s during load
  (~$((MAX_THROUGHPUT/10000)) physical partitions), generates ~1.27M docs, then scales
  down to ${TARGET_MAX} RU/s (~US\$29/day idle). Delete everything afterwards with
  ./infra/teardown.sh. Set CONFIRM=1 to skip this prompt.

WARN
  read -r -p "Type 'yes' to continue: " _ans
  [[ "${_ans:-}" == "yes" ]] || { echo "Aborted."; exit 1; }
fi

if [[ "${SKIP_DEPLOY:-0}" != "1" ]]; then
  step "1/6 Deploy infrastructure (Bicep)"
  ./infra/deploy.sh
fi

if [[ "${SKIP_GEN:-0}" != "1" ]]; then
  step "2/6 Generate + bulk-load data (.NET)"
  ( cd src/DataGenerator && dotnet run -c Release )
fi

if [[ "${SKIP_SCALEDOWN:-0}" != "1" ]]; then
  step "3/6 Scale container down to idle floor"
  ./infra/scale-down.sh || echo "(scale-down skipped/failed — continuing)"
fi

step "4/6 Run .NET query client"
( cd src/DotNetQueryClient && dotnet run -c Release )

step "5/6 Run Go query client"
( cd src/go-query-client && go run . )

step "6/6 Build report"
python3 analysis/report.py

echo
echo "Done. Report: analysis/report.html  (open in a browser)"
echo "Raw diagnostics: diagnostics/dotnet/*.diag.json, diagnostics/go/*.diag.json"
echo
echo "Remember to tear down when finished (stops billing): ./infra/teardown.sh"
