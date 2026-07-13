# `backend/` — REST API

Flask API serving the processed panel + derived analytics to the frontend.
One blueprint per visualization endpoint group (`routes/`), reading either
cached Parquet (default) or PostgreSQL (`DATA_BACKEND=db`) via `db.py`.

```bash
pip install -r ../requirements.txt      # from the repo root
flask --app backend.app run             # http://localhost:5000
```

Reads Parquet from `../data/processed/` by default. Set `DATA_BACKEND=db` plus
`DATABASE_URL` to serve from PostgreSQL instead (`db.load_panel_to_postgres`
does the one-off load).

| Endpoint | View | Returns |
|---|---|---|
| `GET /api/countries` | — | `[{iso3, name, region, has_conflict}]` |
| `GET /api/geo/countries` | 1, 4 | raw GeoJSON boundary polygons |
| `GET /api/kpis` | Overview | high-level global metrics |
| `GET /api/residual-choropleth?year=` | 1 | residual per country for a year |
| `GET /api/conflict-light-overview` | 2 | cross-country overview of conflict vs light |
| `GET /api/conflict-type-decay?iso3=` | 2 | light decay anomalies grouped by event type |
| `GET /api/country/<iso3>/shock-recovery` | 3 | luminosity/fatalities + counterfactual |
| `GET /api/recovery-tradeoff` | 3 | capacity vs. recovery time, all episodes |
| `GET /api/spillover/<iso3>?start=&end=` | 4 | epicenter -> neighbor edges |
| `GET /api/trade/<iso3>` | 5 | indexed trade series + partner realignment + ranking + exported commodities |

Every endpoint returns `{"data": ..., "meta": {...}}` except `/geo/countries`
(raw GeoJSON, for direct consumption by D3/Plotly's geo layers).
