/*
 * This file provides a simple JavaScript client for communicating with the application's REST API.
 * It abstracts away the underlying fetch calls and handles query parameter serialization as well as unwrapping response data.
 */
const BASE = "/api";

/*
 * Performs an HTTP GET request to the given API path.
 * It converts the `params` object into a query string, omitting any null, undefined, or empty string values.
 * Returns the fully parsed JSON response. Throws an error if the HTTP response is not successful.
 */
async function request(path, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "")
  ).toString();
  const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ""}`);
  if (!res.ok) {
    throw new Error(`${path} -> HTTP ${res.status}`);
  }
  return res.json();
}

/*
 * A helper function that makes a request and automatically unwraps the response envelope,
 * returning only the `data` field. Useful for endpoints that wrap results in { data: ... }.
 */
async function get(path, params = {}) {
  const envelope = await request(path, params);
  return envelope.data;
}

/*
 * The `api` object exports methods for every available endpoint in the system.
 * Some methods use `get` to only return the data payload, while others use `request`
 * to return the full response (including metadata or raw GeoJSON).
 */
export const api = {
  /*
   * Checks the health of the API.
   */
  health: () => get("/health"),

  /*
   * Retrieves country registry data.
   */
  countries: () => get("/countries"),

  /*
   * Retrieves raw GeoJSON data for countries.
   * Returns the entire payload without unwrapping.
   */
  countriesGeojson: () => request("/geo/countries"),

  /*
   * Retrieves the Key Performance Indicator (KPI) summary metrics.
   */
  kpiSummary: (year) => get("/kpis", { year }),

  /*
   * Retrieves anomaly data for the residual choropleth view, parameterized by year.
   */
  residualChoropleth: (year) => request("/residual-choropleth", { year }),


  /*
   * Retrieves conflict type decay data for a specific country by ISO3 code.
   */
  conflictTypeDecay: (iso3) => get("/conflict-type-decay", { iso3 }),

  /*
   * Retrieves recovery tradeoff data, which includes development factors in its metadata.
   */
  recoveryTradeoff: () => request("/recovery-tradeoff"),

  /*
   * Retrieves spillover analysis data for a country over a specific time range.
   */
  spillover: (iso3, start, end) => request("/spillover/" + iso3, { start, end }),

  /*
   * Retrieves trade shock data for a specific country.
   */
  trade: (iso3) => request(`/trade/${iso3}`),
};
