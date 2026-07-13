import argparse
import pandas as pd
import config
from pipeline.utils import get_logger, read_table, write_table

log = get_logger("pipeline.compute_plot2_conflict_type")

def main(argv=None):
    p = argparse.ArgumentParser()
    p.parse_args(argv)

    try:
        events = read_table(config.ACLED_EVENTS_PARQUET)
        residuals = read_table(config.PLOT1_RESIDUAL_PARQUET)
    except FileNotFoundError as e:
        log.error(f"Required files missing: {e}")
        return 1

    events["event_date"] = pd.to_datetime(events["event_date"])
    events["year"] = events["event_date"].dt.year
    events["fatalities"] = pd.to_numeric(events["fatalities"], errors="coerce").fillna(0)

    # Group by iso3, year, event_type
    grouped = events.groupby(["iso3", "year", "event_type"], as_index=False).agg(
        event_count=("event_date", "count"),
        total_fatalities=("fatalities", "sum")
    )
    
    # Join with residuals to get residual_anomaly
    joined = grouped.merge(residuals[["iso3", "year", "residual_anomaly"]], on=["iso3", "year"], how="inner")
    joined = joined.dropna(subset=["residual_anomaly"])

    out_file = config.DATA_PROCESSED_DIR / "plot2_conflict_type.parquet"
    write_table(joined, out_file)
    log.info(f"Wrote {out_file.name} with {len(joined)} rows.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
