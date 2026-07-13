#!/usr/bin/env python3
"""Smoke-test every backend route (§5-§6) against a running server.

Hits each endpoint once and asserts a 200 plus the documented response shape
(the ``{data, meta}`` envelope, or raw GeoJSON for ``/geo/countries``). It
discovers a real conflict country from ``/api/countries`` so the country-scoped
routes get exercised with live data rather than a hard-coded ISO3.

Usage:
    flask --app backend.app run --port 5000        # in one shell
    python scripts/smoke_test_api.py                # in another
    python scripts/smoke_test_api.py --base-url http://localhost:5050/api
"""
from __future__ import annotations

import argparse
import sys

import requests

DEFAULT_BASE = "http://localhost:5000/api"


class SmokeError(AssertionError):
    pass


def _get(base: str, path: str, **params) -> dict:
    url = f"{base}{path}"
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        raise SmokeError(f"{path} -> HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _expect_envelope(payload: dict, path: str) -> None:
    if "data" not in payload or "meta" not in payload:
        raise SmokeError(f"{path} -> missing {{data, meta}} envelope: keys={list(payload)}")


def run(base: str) -> int:
    checks: list[str] = []

    def ok(name: str) -> None:
        checks.append(name)
        print(f"  PASS  {name}")

    # --- health ------------------------------------------------------------
    _get(base, "/health")
    ok("GET /health")

    # --- countries registry + a conflict country to drive the rest ---------
    countries = _get(base, "/countries")
    _expect_envelope(countries, "/countries")
    rows = countries["data"]
    if not rows:
        raise SmokeError("/countries returned no countries")
    for r in rows:
        for key in ("iso3", "name", "region", "has_conflict"):
            if key not in r:
                raise SmokeError(f"/countries row missing '{key}': {r}")
    ok("GET /countries")

    conflict = next((r["iso3"] for r in rows if r["has_conflict"]), rows[0]["iso3"])
    print(f"  ...  using conflict country {conflict} for country-scoped routes")

    # --- geo (raw GeoJSON, NOT enveloped) ----------------------------------
    geo = _get(base, "/geo/countries")
    if geo.get("type") != "FeatureCollection" or "features" not in geo:
        raise SmokeError("/geo/countries did not return a GeoJSON FeatureCollection")
    ok("GET /geo/countries")

    # --- View 1 ------------------------------------------------------------
    rc = _get(base, "/residual-choropleth")
    _expect_envelope(rc, "/residual-choropleth")
    years = rc["meta"].get("available_years")
    if not years:
        raise SmokeError("/residual-choropleth meta missing available_years")
    _expect_envelope(_get(base, "/residual-choropleth", year=years[-1]), "/residual-choropleth?year=")
    ok("GET /residual-choropleth[?year=]")

    # --- View 2 ------------------------------------------------------------
    cl = _get(base, "/conflict-light-overview")
    _expect_envelope(cl, "/conflict-light-overview")
    if "dates" not in cl["data"] or "countries" not in cl["data"]:
        raise SmokeError("/conflict-light-overview data missing dates/countries")
    ok("GET /conflict-light-overview")

    # --- View 3 ------------------------------------------------------------
    sr = _get(base, f"/country/{conflict}/shock-recovery")
    _expect_envelope(sr, "/shock-recovery")
    if "series" not in sr["data"]:
        raise SmokeError("/shock-recovery data missing series")
    ok("GET /country/<iso3>/shock-recovery")

    _expect_envelope(_get(base, "/recovery-tradeoff"), "/recovery-tradeoff")
    ok("GET /recovery-tradeoff")

    # --- View 4 ------------------------------------------------------------
    sp = _get(base, f"/spillover/{conflict}")
    _expect_envelope(sp, "/spillover")
    if "nodes" not in sp["data"] or "edges" not in sp["data"]:
        raise SmokeError("/spillover data missing nodes/edges")
    ok("GET /spillover/<iso3>")

    # --- View 5 — conflict's trade shock -----------------------------------
    tr = _get(base, f"/trade/{conflict}")
    _expect_envelope(tr, "/trade")
    if "series" not in tr["data"] or "realignment" not in tr["data"]:
        raise SmokeError("/trade data missing series/realignment")
    if "ranking" not in tr["meta"] or "caveats" not in tr["meta"]:
        raise SmokeError("/trade meta missing ranking/caveats")
    ok("GET /trade/<iso3> (series + realignment + ranking)")

    print(f"\nAll {len(checks)} routes OK against {base}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=DEFAULT_BASE, help=f"API base (default {DEFAULT_BASE})")
    args = p.parse_args(argv)
    try:
        return run(args.base_url.rstrip("/"))
    except requests.ConnectionError:
        print(f"ERROR: could not connect to {args.base_url} — is the backend running?", file=sys.stderr)
        return 2
    except SmokeError as exc:
        print(f"SMOKE FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
