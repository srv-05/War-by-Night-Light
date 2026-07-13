import Plot from "react-plotly.js";
import { api } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useGlobal } from "../state/GlobalContext";

/* This component renders a horizontal bar chart displaying the countries with the most extreme residual values. */
const TOP_N = 8;
const MIN_POP = 1_000_000;

export default function ResidualRanking() {
  /* Retrieve the current active year and country selection functions from the global context state. */
  const { year, selectedCountry, setSelectedCountry } = useGlobal();
  
  /* Fetch the residual choropleth data for the current year from the API backend. */
  const { data: envelope, loading, error } = useFetch(() => api.residualChoropleth(year), [year]);

  const title = "Most anomalous economies";
  
  /* Display loading or error states while data is being fetched or if an error occurs. */
  if (loading) return <section className="view"><h3>{title}</h3><p className="status">Loading…</p></section>;
  if (error) return <section className="view"><h3>{title}</h3><p className="status error">Failed to load: {error.message}</p></section>;

  /* Filter out small nations and select the top anomalies on both negative and positive scales. */
  const eligible = envelope.data.filter((r) => r.population == null || r.population >= MIN_POP);
  const asc = [...eligible].sort((a, b) => a.residual - b.residual);
  const dim = asc.slice(0, TOP_N);
  const bright = asc.slice(-TOP_N);
  const shown = [...dim, ...bright];

  /* Prepare labels, values, and colors for the Plotly bar chart rendering. */
  const labels = shown.map((r) => r.country ?? r.iso3);
  const values = shown.map((r) => r.residual);
  const colors = shown.map((r) =>
    r.iso3 === selectedCountry ? "#111" : r.residual >= 0 ? "#b2182b" : "#2166ac"
  );

  /* Render the bar chart using react-plotly.js and apply click handlers for global state updates. */
  return (
    <section className="view">
      <h3>{title} — {year}</h3>
      <Plot
        data={[{
          type: "bar",
          orientation: "h",
          y: labels,
          x: values,
          marker: { color: colors },
          text: shown.map((r) => r.iso3),
          hovertemplate: "%{y}<br>residual: %{x:.2f}<extra></extra>",
        }]}
        layout={{
          margin: { t: 6, b: 32, l: 110, r: 10 },
          height: 380,
          xaxis: { title: "residual (brighter → / ← dimmer)", zeroline: true, zerolinecolor: "#888" },
          yaxis: { automargin: true },
        }}
        onClick={(e) => {
          const p = e.points?.[0];
          if (p?.text) setSelectedCountry(p.text);
        }}
        useResizeHandler
        style={{ width: "100%" }}
        config={{ displayModeBar: false }}
      />
      <p className="hint">Red = brighter than its GDP predicts; blue = dimmer. Click to drill down.</p>
    </section>
  );
}
