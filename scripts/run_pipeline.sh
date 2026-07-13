#!/usr/bin/env bash
# ===========================================================================
# Run the full data pipeline end to end:
#   gee_extract -> ingest_acled / ingest_worldbank -> build_master_panel -> compute_derived
#
#   bash scripts/run_pipeline.sh                  # synthetic fixture (default; no credentials needed)
#   MODE=local bash scripts/run_pipeline.sh        # ACLED + World Bank from data/raw/DataSet (or $DATASET_DIR)
#   MODE=live  bash scripts/run_pipeline.sh        # attempt real GEE/ACLED/World Bank API pulls
#
# NOTE: the local DataSet has ACLED + World Bank but NOT VIIRS night-time light.
# NTL (`sum_of_lights`) still has to come from Google Earth Engine, so the NTL
# stage runs separately via NTL_MODE (default: synthetic). Set NTL_MODE=live and
# configure GEE credentials to pull real lights — the residual/spillover views
# are only meaningful once NTL is real too.
#
#   MODE=local NTL_MODE=live bash scripts/run_pipeline.sh   # real everything
# ===========================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"

MODE="${MODE:-synthetic}"
# NTL now HAS a local source (VIIRS + harmonized CSVs), so local means local.
# Geo defaults to local under MODE=local (fetches real Natural Earth borders +
# derives real land-border adjacency; needs shapely + network).
if [[ "$MODE" == "local" ]]; then
  NTL_MODE="${NTL_MODE:-local}"
  GEO_MODE="${GEO_MODE:-local}"
else
  NTL_MODE="${NTL_MODE:-$MODE}"
  GEO_MODE="${GEO_MODE:-$MODE}"
fi

step() { echo; echo "==> $*"; }

step "gee_extract (monthly VIIRS NTL panel, mode=$NTL_MODE)"
python -m pipeline.gee_extract --mode "$NTL_MODE"

step "ingest_ntl_annual (annual harmonized NTL for Plot 1, mode=$MODE)"
python -m pipeline.ingest_ntl_annual --mode "$([ "$MODE" = "local" ] && echo local || echo "$NTL_MODE")"

step "ingest_acled (conflict events, mode=$MODE)"
python -m pipeline.ingest_acled --mode "$MODE"

step "ingest_worldbank (GDP/military/gov spend, mode=$MODE)"
python -m pipeline.ingest_worldbank --mode "$MODE"

if [ "$MODE" = "local" ]; then
  step "ingest_trade (BACI bilateral trade for Plots 4 & 5)"
  python -m pipeline.ingest_trade --mode local
fi

step "build_master_panel (join + geo/adjacency, geo-mode=$GEO_MODE)"
python -m pipeline.build_master_panel --geo-mode "$GEO_MODE"

step "compute_derived (baseline, residual, severity, synthetic control, spillover)"
python -m pipeline.compute_derived

echo
echo "==> Pipeline complete. Outputs in data/processed/ and data/geo/"
