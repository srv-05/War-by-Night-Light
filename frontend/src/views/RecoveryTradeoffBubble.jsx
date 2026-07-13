import { useState } from "react";
import Plot from "react-plotly.js";
import { api } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useGlobal } from "../state/GlobalContext";

/*
 * Maps each specific conflict type to a designated color hex code for consistency in visual representation.
 */
const TYPE_COLORS = {
  "Battles": "#e63946",
  "Explosions/Remote violence": "#9d0208",
  "Violence against civilians": "#f48c06",
  "Riots": "#457b9d",
  "Protests": "#2a9d8f",
  "Strategic developments": "#8338ec",
  "Unknown": "#999999",
};

/*
 * A React component that renders a bubble chart comparing a chosen development factor against recovery speed.
 * The user can filter the display by conflict type and change the independent variable (development factor).
 */
export default function RecoveryTradeoffBubble() {
  const { selectedCountry, setSelectedCountry } = useGlobal();
  const { data: envelope, loading, error } = useFetch(() => api.recoveryTradeoff(), []);
  const [factor, setFactor] = useState("capital_formation_pct_gdp");
  const [typeFilter, setTypeFilter] = useState("all");

  const title = "Recovery vs Development Profile";
  
  /*
   * Handle the loading and error states for the data fetching process.
   */
  if (loading || !envelope) return <section className="view"><h3>{title}</h3><p className="status">Loading…</p></section>;
  if (error) return <section className="view"><h3>{title}</h3><p className="status error">Failed to load: {error.message}</p></section>;

  const rows = envelope.data ?? [];
  const factors = envelope.meta?.development_factors ?? { gdp_per_capita: "GDP per capita" };
  const factorKeys = Object.keys(factors);
  const activeFactor = factorKeys.includes(factor) ? factor : factorKeys[0];

  const types = Array.from(new Set(rows.map((r) => r.conflict_type))).filter(Boolean);
  
  /*
   * Filter the dataset to exclude incomplete records and apply the user's conflict type filter.
   * Then map over the valid records to calculate a derived 'recovery_speed' metric.
   */
  const shown = rows.filter(
    (r) => r[activeFactor] != null && 
           r.recovery_gap_closed_pct != null && 
           r.recovery_time_months != null && 
           r.recovery_time_months > 0 &&
           !r.censored &&
           (typeFilter === "all" || r.conflict_type === typeFilter)
  ).map(r => ({
    ...r,
    recovery_speed: (r.recovery_gap_closed_pct * 100) / r.recovery_time_months
  }));

  /*
   * Configure the scatter plot trace properties including marker size scaling, conditional opacity based on selection, 
   * and custom hover tooltips containing country details and conflict severity.
   */
  const trace = {
    type: "scatter",
    mode: "markers",
    name: "Episodes",
    x: shown.map((r) => r[activeFactor]),
    y: shown.map((r) => r.recovery_speed),
    text: shown.map((r) => `${r.country} (${r.iso3})`),
    customdata: shown.map((r) => [r.iso3, r.severity_score, r.conflict_type]),
    marker: {
      size: shown.map((r) => 8 + (r.severity_score ?? 0) * 34),
      color: shown.map((r) => TYPE_COLORS[r.conflict_type] || TYPE_COLORS["Unknown"]),
      opacity: shown.map((r) => (!selectedCountry || r.iso3 === selectedCountry) ? 0.8 : 0.1),
      line: { 
        width: shown.map((r) => (r.iso3 === selectedCountry ? 3 : 0.5)), 
        color: shown.map((r) => (r.iso3 === selectedCountry ? "#fff" : "#222")) 
      },
    },
    hovertemplate: `%{text}<br>${factors[activeFactor]}: %{x}<br>Recovery Speed: %{y:.2f} %/mo<br>Severity: %{customdata[1]:.2f}<br>Type: %{customdata[2]}<extra></extra>`,
  };

  const logX = activeFactor === "gdp_per_capita";

  /*
   * Render control selectors for the axes alongside the generated bubble chart, enabling user interactions like selection clicks.
   */
  return (
    <section className="view">
      <h3>{title}</h3>
      <div className="controls" style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
        <label>
          x-axis:{" "}
          <select value={activeFactor} onChange={(e) => setFactor(e.target.value)}>
            {factorKeys.map((k) => <option key={k} value={k}>{factors[k]}</option>)}
          </select>
        </label>
        <label>
          conflict:{" "}
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            <option value="all">all types</option>
            {types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      </div>
      <div>
        <Plot
          data={[trace]}
          layout={{
            xaxis: { title: factors[activeFactor], type: logX ? "log" : "linear" },
            yaxis: { title: "Recovery speed (% / month)" },
            margin: { t: 30, b: 40, l: 50, r: 20 },
            height: 450,
            hovermode: "closest",
          }}
          onClick={(e) => {
            const custom = e.points?.[0]?.customdata;
            const iso = Array.isArray(custom) ? custom[0] : custom;
            if (iso) setSelectedCountry(iso);
          }}
          useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false }}
        />
      </div>
      <p className="hint">
        Each bubble is a country's conflict episode — x = development factor, y = recovery speed (% gap closed per month),
        color = conflict type, size = conflict severity.
      </p>
    </section>
  );
}
