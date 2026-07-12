#!/usr/bin/env bash
# Scale the container's autoscale max down to its minimum AFTER data is loaded.
# Physical partitions created during the high-throughput load do NOT merge back,
# so routing behaviour stays observable while idle cost drops to the ~10% floor.
# NOTE: the minimum autoscale max is max(1000, highestProvisioned/10). The published
# run provisioned 100000 to force ~10 partitions, so the floor is 10000 (not 1000).
# TARGET_MAX comes from the .env deploy.sh wrote; override by exporting TARGET_MAX.
set -euo pipefail

AZ="${AZ:-az}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load account/rg/db/container from the generated .env
ENV_FILE="$SCRIPT_DIR/../.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

RG="${COSMOS_RG:-rg-cosmos-hpk-test}"
ACCOUNT="${COSMOS_ACCOUNT:?Set COSMOS_ACCOUNT (run deploy.sh first)}"
DB="${COSMOS_DATABASE:-ordersdb}"
CONTAINER="${COSMOS_CONTAINER:-orders}"
TARGET_MAX="${TARGET_MAX:-10000}"

echo ">> Scaling container '$CONTAINER' autoscale max to $TARGET_MAX RU/s..."
$AZ cosmosdb sql container throughput update \
  -g "$RG" -a "$ACCOUNT" -d "$DB" -n "$CONTAINER" \
  --max-throughput "$TARGET_MAX" -o table

echo ">> Current partition count (physical partitions persist after scale-down):"
$AZ cosmosdb sql container throughput show \
  -g "$RG" -a "$ACCOUNT" -d "$DB" -n "$CONTAINER" \
  --query '{maxThroughput:resource.autoscaleSettings.maxThroughput, minThroughput:resource.minimumThroughput}' -o table || true
