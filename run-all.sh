#!/usr/bin/env bash
# End-to-end orchestrator: deploy -> generate -> scale-down -> query (both SDKs) -> analyze.
# Skip stages with env flags, e.g.  SKIP_DEPLOY=1 SKIP_GEN=1 ./run-all.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

step() { echo; echo "==================== $* ===================="; }

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
echo "NOTE: deploy/generate above use deploy.sh's .env template defaults, so a bare"
echo "  ./run-all.sh does NOT reproduce the published 1.27M-doc / 10-partition run."
echo "  To reproduce it, follow the step-by-step flow in README.md (set maxThroughput"
echo "  and the .env knobs before generating)."
