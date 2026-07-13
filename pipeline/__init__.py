"""War by Night Light data pipeline.

    gee_extract ──┐
    ingest_ntl_annual ─┤
    ingest_acled ──────┼─> build_master_panel ─> compute_derived ─> data/processed/plot{1..5}
    ingest_worldbank ──┤        (data/interim/*)        (5 per-plot files, one per view)
    ingest_trade ──────┘   (BACI bilateral trade -> Plots 4 & 5)

Intermediates live in ``data/interim/``; ``compute_derived`` distills them into
exactly five per-plot files under ``data/processed/`` — the only surface the
backend serves. Each stage takes ``--mode {local,live,synthetic}`` and falls
back to a deterministic synthetic fixture (see ``pipeline.utils.FIXTURE_COUNTRIES``)
when its live source is unavailable, so the pipeline is always runnable.
"""

__version__ = "0.3.0"
