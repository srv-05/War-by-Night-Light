import Plot from "react-plotly.js";
import { api } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useGlobal } from "../state/GlobalContext";

/*
 * This React component renders a map (choropleth) to visualize night-light anomalies relative to a country's own GDP baseline.
 * It consumes global state elements such as the currently selected year and country, driving interactive updates on the map.
 */
export default function ResidualChoropleth() {
  const { year, setYear, selectedCountry, setSelectedCountry } = useGlobal();
  const { data: envelope, loading, error } = useFetch(() => api.residualChoropleth(year), [year]);

  const title = "GDP–Light Anomaly";
  
  /*
   * Handle application UI feedback during asynchronous data fetching operations.
   */
  if (loading) return <section className="view"><h3>{title}</h3><p className="status">Loading…</p></section>;
  if (error) {
    return <section className="view"><h3>{title}</h3><p className="status error">Failed to load: {error.message}</p></section>;
  }

  const rows = envelope.data;
  const meta = envelope.meta ?? {};
  const availableYears = meta.available_years ?? [];
  const fit = meta.fit ?? {};
  const locations = rows.map((r) => r.iso3);
  const z = rows.map((r) => r.residual_anomaly);
  
  /*
   * Format the tooltip text that appears when hovering over specific geographic regions.
   */
  const text = rows.map((r) =>
    `${r.country ?? r.iso3} (${r.iso3})<br>anomaly vs own norm: ${r.residual_anomaly >= 0 ? "+" : ""}${r.residual_anomaly.toFixed(2)}` +
    `<br>this-year residual: ${r.residual?.toFixed(2)} · own norm: ${r.residual_baseline?.toFixed(2)}`
  );

  /*
   * Define an interval for mapping color scales. It calculates a deadband array,
   * setting values close to zero to a neutral grey to avoid visual noise for structurally stable economies.
   */
  const clamp = Math.max(0.5, meta.color_range ?? 1.0);
  const DEAD = 0.3;
  const half = Math.min(0.42, DEAD / (2 * clamp)); 
  const pLo = 0.5 - half;
  const pHi = 0.5 + half;
  const eps = 0.02;
  const colorscale = [
    [0, "#b2182b"],
    [Math.max(0.001, pLo - eps), "#f4a582"],
    [pLo, "#ececec"],
    [pHi, "#ececec"],
    [Math.min(0.999, pHi + eps), "#92c5de"],
    [1, "#2166ac"],
  ];

  /*
   * Assemble and output the top-level view structure containing a dropdown for year selection,
   * some statistical readouts, and the main Plotly geographic visualization.
   */
  return (
    <section className="view">
      <h3>{title}</h3>
      <div className="controls">
        <label>
          Year:{" "}
          <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {availableYears.map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </label>
        {fit.r2 != null && (
          <span className="fit-note">
            {" "}underlying fit: β<sub>gdp</sub>={fit.beta_gdp?.toFixed(2)}, R²={fit.r2?.toFixed(2)}, n={fit.n}
          </span>
        )}
      </div>
      <Plot
        data={[{
          type: "choropleth",
          locationmode: "ISO-3",
          locations,
          z,
          zmin: -clamp,
          zmax: clamp,
          zmid: 0,
          colorscale,
          text,
          hoverinfo: "text",
          marker: { line: { color: locations.map((l) => (l === selectedCountry ? "#111" : "#999")), width: locations.map((l) => (l === selectedCountry ? 2.5 : 0.5)) } },
          colorbar: { title: { text: "anomaly<br>vs own norm", side: "right" }, tickvals: [-clamp, 0, clamp], ticktext: ["dimmer", "at norm", "brighter"] },
        }]}
        layout={{
          datarevision: selectedCountry,
          dragmode: false,
          geo: {
            projection: { type: "natural earth" },
            showframe: false,
            showcoastlines: true,
            coastlinecolor: "#ccc",
            showland: true,
            landcolor: "#eee",
            showcountries: true,
            countrycolor: "#ddd",
          },
          margin: { t: 0, b: 0, l: 0, r: 0 },
          height: 380,
        }}
        onClick={(e) => {
          const point = e.points?.[0];
          if (point) setSelectedCountry(point.location);
        }}
        useResizeHandler
        style={{ width: "100%" }}
        config={{ displayModeBar: false }}
      />
      <p className="hint">
        Colour = how far a country's lights sit from what its <em>own</em> GDP–light history predicts.
        <span style={{ color: "#b2182b" }}> Red = dimmed vs its norm</span>,
        <span style={{ color: "#2166ac" }}> blue = brighter</span>; grey = at its norm (so a permanently
        dark-but-stable country like Australia is neutral, not red). Click a country to drill down.
      </p>
    </section>
  );
}
