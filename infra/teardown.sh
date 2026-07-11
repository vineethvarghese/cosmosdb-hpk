#!/usr/bin/env bash
# Delete the whole resource group -> removes all Cosmos DB cost. Irreversible.
set -euo pipefail

AZ="${AZ:-az}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi
RG="${COSMOS_RG:-rg-cosmos-hpk-test}"

read -r -p "Delete resource group '$RG' and ALL its resources? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
  $AZ group delete -n "$RG" --yes --no-wait
  echo ">> Delete requested (running async). Verify with: $AZ group show -n $RG"
else
  echo "Aborted."
fi
