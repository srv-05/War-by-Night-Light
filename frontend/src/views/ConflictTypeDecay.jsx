import React from "react";
import Plot from "react-plotly.js";
import { api } from "../api/client";
import { useFetch } from "../api/useFetch";

import { useGlobal } from "../state/GlobalContext";

/*
 * This React component visualizes the relationship between different types of conflicts 
 * and night light decay anomalies. It retrieves the currently selected country from the global state
 * and fetches the corresponding conflict decay data from the API.
 */
export default function ConflictTypeDecay() {
  const { selectedCountry } = useGlobal();
  const { data: envelope, loading, error } = useFetch(() => api.conflictTypeDecay(selectedCountry), [selectedCountry]);

  const title = "Conflict Type vs Light Decay";
  
  /*
   * Display loading and error states respectively while the data is being fetched or if the fetch fails.
   */
  if (loading) return <section className="view"><h3>{title}</h3><p className="status">Loading…</p></section>;
  if (error) {
    return <section className="view"><h3>{title}</h3><p className="status error">Failed to load: {error.message}</p></section>;
  }

  const rows = envelope?.conflict_types || [];
  
  /*
   * Transform the API response into a format suitable for a Plotly violin/box plot.
   * Each row becomes a separate violin trace, mapped with its respective statistics and custom hover information.
   */
  const plotData = rows.map((row) => ({
    type: "violin",
    y: row.anomalies,
    name: row.event_type,
    box: { visible: true },
    meanline: { visible: true },
    points: "all",
    jitter: 0.3,
    pointpos: -1.8,
    marker: { color: row.mean_anomaly < 0 ? "#b2182b" : "#2166ac", size: 3, opacity: 0.15 },
    hovertemplate: `<b>%{name}</b><br>Observation: %{y:.3f}<br>Group Mean: ${row.mean_anomaly.toFixed(2)}<br>Group Fatalities: ${row.fatalities.toLocaleString()}<extra></extra>`,
  }));

  /*
   * Render the plotly visualization alongside a descriptive text block that explains
   * the significance of the light decay values to the user.
   */
  return (
    <section className="view">
      <h3>{title}</h3>
      <Plot
        data={plotData}
        layout={{
          margin: { t: 30, b: 60, l: 100, r: 20 },
          height: 380,
          yaxis: {
            title: { text: "GDP–light anomaly (vs own norm)", standoff: 20 },
            zeroline: true,
            zerolinecolor: "#999",
            zerolinewidth: 2,
            range: [-2.5, 2.5],
            automargin: true
          },
          xaxis: {
            title: "Conflict Type",
            tickangle: 0,
            automargin: true
          },
          showlegend: false
        }}
        useResizeHandler
        style={{ width: "100%" }}
        config={{ displayModeBar: false }}
      />
      <p className="hint">
        Distribution of the annual GDP–light anomaly (each country vs its own norm) across
        country-years, grouped by the conflict types they experienced.
        <span style={{ color: "#b2182b" }}> Lower = greater darkening</span>.
      </p>
    </section>
  );
}
