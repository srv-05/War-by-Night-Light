# War by Night Light

**A visual analytics system for the economic impact of conflict, seen from orbit.**

Reliable economic data is often the first casualty of war — governments stop
reporting when infrastructure breaks down. But satellites keep watching every
night, and the amount of artificial light a country emits tracks its electricity
use, industry, and urban activity closely. **War by Night Light** uses
Night-Time Light (NTL) imagery as a proxy for economic activity and combines it
with conflict records and economic statistics to reveal how war reshapes an
economy — before, during, and after the fighting.

> CS661 course project — Group 18.

---

## What it does

An interactive, web-based dashboard of five **linked** views, laid out
overview-first → filter → details-on-demand: an overview row (global
choropleth + severity ranking) on top, detail views below that all react to a
single shared country + time-window selection.

| # | View | Question it answers |
|---|------|---------------------|
| 1 | **Global Overview** | Global KPI metrics and top most anomalous economies. |
| 2 | **NTL vs GDP (log-log residual)** | Which countries are brighter or dimmer than their reported GDP predicts? |
| 3 | **Conflict Types vs Light Decay** | How do different conflict types uniquely correlate with local light decay? |
| 4 | **Conflict vs Spillover** | Did the light loss cross borders into neighboring countries? |
| 5 | **Trade vs Conflict** | How did trade flows and partner realignments change during the conflict? |

---

## Data sources

| Source | What | Access |
|--------|------|--------|
| **VIIRS DNB** (NTL) | Monthly nighttime radiance, summed per country | Google Earth Engine → tabular (`pipeline/gee_extract.py`) |
| **ACLED** | Geolocated, dated conflict events + fatalities | [developer.acleddata.com](https://developer.acleddata.com) (free key) |
| **World Bank** | GDP (current/constant), GDP/capita, military & govt spend (%GDP) | Open API, no key (`pipeline/ingest_worldbank.py`) |
| **Natural Earth** | Country boundaries (GeoJSON) + land-border adjacency | Public (`pipeline/build_master_panel.py`) |

---

## Architecture

```
 VIIRS (GEE)        ACLED           World Bank        Natural Earth
     │                │                 │                  │
     ▼                ▼                 ▼                  ▼
 gee_extract.py   ingest_acled.py  ingest_worldbank.py  build_master_panel.py
     │                │                 │                  │
     └──────────────────────┬───────────────────────────────┘
                             ▼
                    build_master_panel.py
              master_panel.parquet + geo/adjacency
                             │
                             ▼
                    compute_derived.py
        (analysis/: baseline, residual,
         recovery_time, spillover — pure functions)
                             │
                             ▼
                  backend/ (Flask REST API)
                             │
                             ▼
                 frontend/ (React + D3/Plotly)
```

### Repository layout

```
.
├── config.py            # paths, DB URL, schema, analysis constants — single source of truth
├── docker-compose.yml    # postgres + backend
├── data/                 # raw -> processed panels -> geo (small fixture, committed — see data/README.md)
├── pipeline/             # gee_extract, ingest_acled, ingest_worldbank, build_master_panel, compute_derived
├── analysis/             # shared pure functions: baseline, residual, severity, synthetic_control, spillover
├── backend/              # Flask REST API (app.py, db.py, routes/)
├── frontend/             # React + D3/Plotly dashboard
├── tests/                # unit tests for analysis/
└── scripts/               # run_pipeline.sh
```

---

## Quickstart

### 1. Install

```bash
git clone <repo-url> && cd War-by-Night-Light
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # only needed for live GEE/ACLED credentials
```

### 2. Data is already built

The processed panels under `data/processed/` are committed (~6 MB total), so you
can skip straight to plotting. **What's committed is real ACLED conflict + real
World Bank economics** for the all countries, with **synthetic Night-Time
Light**. Thec4.9 GB of raw source is *not* in git; only the small derived panels are.

> **The raw source (~4.9 GB) is not in this repo** — it's far past GitHub's
> per-file limit and isn't needed to run the app (the committed 6 MB processed
> panels are). Prople who want raw data can download the
> raw `DataSet` folder here:
>
> **📁 Raw DataSet (Google Drive): `https://drive.google.com/drive/folders/1HoqAusuPYZRyM4Me8CW9S3qk08Y4LcK1?usp=drive_link`**

### 3. Server

Run both of them on seperate terminal windows.

```bash
flask --app backend.app run --port 5000     # backend :5000
```

```bash
cd frontend && npm install && npm run dev   # frontend :5173
```

---

## Tech stack

- **Data engine:** Google Earth Engine (Python `ee` API) on VIIRS DNB.
- **Backend:** Flask REST API, pandas/NumPy/scikit-learn/SciPy; Parquet by
  default, PostgreSQL optional (`DATA_BACKEND=db`).
- **Frontend:** React (hooks + Context) + Vite, D3.js / Plotly.js.

---

## Team — Group 18

| Member | Roll | 
|--------|------|
| Sumath Rengasami V | 241054 
| K G Sri Sanjay | 240504 
| Ibrahim Imtiyaz Mukadam | 240461 
| Divyansh Agarwal | 251110603 
| Saurav Kumar | 240949 
| Himank Khandelwal | 240450 

---

## License

Source code under the [MIT License](LICENSE). The underlying datasets retain
their own terms — cite VIIRS (NOAA/NASA), ACLED, the World Bank, and Natural
Earth per their licenses.
