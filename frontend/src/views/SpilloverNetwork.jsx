import { useEffect, useRef, useState } from "react";
import Plot from "react-plotly.js";
import * as d3 from "d3";
import { api } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useGlobal } from "../state/GlobalContext";

/* Renders a visualization mapping out spillover effects caused by conflicts, supporting both geo-mapping and force-directed graph modes. */
export default function SpilloverNetwork() {
  /* Subscribe to global state to get the current country and year selections. */
  const { selectedCountry, year } = useGlobal();

  /* Fetch the spillover network for the selected country and year (Plot 4 is
     computed per (epicenter, year); the backend falls back to the country's most
     recent available year if this one has no network). */
  const { data: envelope, loading, error } = useFetch(
    () => (selectedCountry ? api.spillover(selectedCountry, year) : Promise.resolve(null)),
    [selectedCountry, year]
  );
  
  /* Maintain local component state for the layout view and significance filtering settings. */
  const [view, setView] = useState("map");
  const [sigOnly, setSigOnly] = useState(false);
  const svgRef = useRef(null);

  /* Extract network structure definitions like nodes and edges from the API response payload. */
  const nodes = envelope?.data?.nodes ?? [];
  const allEdges = envelope?.data?.edges ?? [];
  const edges = allEdges.filter((e) => (!sigOnly || e.significant));
  const epi = nodes.find((n) => n.role === "epicenter");
  const lonlat = Object.fromEntries(nodes.filter((n) => n.lonlat).map((n) => [n.iso3, n.lonlat]));

  /* Execute a D3 force-directed simulation to layout the nodes dynamically when the force view mode is enabled. */
  useEffect(() => {
    if (view !== "force" || !envelope || !svgRef.current) return;
    const width = 480, height = 380;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("viewBox", `0 0 ${width} ${height}`);
    
    const simNodes = nodes.map((n) => ({ ...n }));
    const simEdges = edges.map((e) => ({ source: e.source, target: e.target, weight: e.weight, significant: e.significant }));
    if (!simNodes.length) return;
    
    const color = d3.scaleSequential(d3.interpolateRdBu).domain([1, -1]);
    const lossOf = Object.fromEntries(nodes.map((n) => [n.iso3, n.own_light_loss ?? 0]));
    
    /* Initialize forces driving node repulsion and link attraction to create an organic layout shape. */
    const sim = d3.forceSimulation(simNodes)
      .force("link", d3.forceLink(simEdges).id((d) => d.iso3).distance(120))
      .force("charge", d3.forceManyBody().strength(-280))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide(28));
      
    /* Generate visual SVG link elements to symbolize connections in the spillover graph. */
    const link = svg.append("g").selectAll("line").data(simEdges).join("line")
      .attr("stroke", (d) => color(d.weight)).attr("stroke-width", (d) => 1.5 + Math.abs(d.weight) * 7)
      .attr("stroke-dasharray", (d) => (d.significant ? null : "3,3")).attr("stroke-opacity", 0.85);
      
    /* Setup SVG circle markers and attach drag handlers to make the force graph interactive. */
    const node = svg.append("g").selectAll("circle").data(simNodes).join("circle")
      .attr("r", (d) => (d.role === "epicenter" ? 22 : 14))
      .attr("fill", (d) => (d.role === "epicenter" ? "#111" : d3.interpolateReds(Math.min((lossOf[d.iso3] || 0) * 1.5, 1))))
      .attr("stroke", "#fff").attr("stroke-width", 1.5)
      .call(d3.drag()
        .on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
        .on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
        
    /* Bind simple text tags displaying country codes directly on the generated nodes. */
    const label = svg.append("g").selectAll("text").data(simNodes).join("text")
      .text((d) => d.iso3).attr("text-anchor", "middle").attr("dy", 3).attr("font-size", 10)
      .attr("fill", "#fff").attr("pointer-events", "none");
      
    /* Update layout attributes continuously as the simulation computes the next tick. */
    sim.on("tick", () => {
      link.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y).attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
      node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
      label.attr("x", (d) => d.x).attr("y", (d) => d.y);
    });
    return () => sim.stop();
  }, [view, envelope, sigOnly]);

  const title = "Conflict Spillover Network";
  
  /* Fallback views for various loading, empty, or error states to guarantee graceful component degradation. */
  if (!selectedCountry) return <section className="view"><h3>{title}</h3><p className="status">Select an epicenter above.</p></section>;
  if (loading || !envelope) return <section className="view"><h3>{title} — {selectedCountry}</h3><p className="status">Loading…</p></section>;
  if (error) return <section className="view"><h3>{title} — {selectedCountry}</h3><p className="status error">Failed to load: {error.message}</p></section>;

  if (!allEdges.length) {
    return (
      <section className="view"><h3>{title} — {selectedCountry}</h3>
        <p className="status">No land-border spillover detected (this country may not be a conflict epicenter, or has no neighbors with a measurable light response).</p>
      </section>
    );
  }

  const spilloverIndex = envelope.meta?.spillover_index;

  /* Configure geographical layout traces mapping network connections directly onto geographic coordinates. */
  const edgeTraces = edges.filter((e) => lonlat[e.source] && lonlat[e.target]).map((e) => ({
    type: "scattergeo", mode: "lines",
    lon: [lonlat[e.source][0], lonlat[e.target][0]],
    lat: [lonlat[e.source][1], lonlat[e.target][1]],
    line: { width: 1 + Math.abs(e.weight) * 6, color: e.weight >= 0 ? "#e63946" : "#457b9d", dash: e.significant ? "solid" : "dot" },
    opacity: e.significant ? 0.9 : 0.45, hoverinfo: "skip", showlegend: false,
  }));
  
  const neighborNodes = nodes.filter((n) => n.role === "neighbor" && lonlat[n.iso3]);
  
  /* Plot the adjacent neighboring locations with varying marker styles corresponding to specific properties. */
  const nodeTrace = {
    type: "scattergeo", mode: "markers+text",
    lon: neighborNodes.map((n) => lonlat[n.iso3][0]), lat: neighborNodes.map((n) => lonlat[n.iso3][1]),
    text: neighborNodes.map((n) => n.iso3), textposition: "top center", textfont: { size: 9 },
    marker: {
      size: 14,
      color: neighborNodes.map((n) => n.own_light_loss || 0), colorscale: "Reds", cmin: 0, cmax: 0.6,
      line: { color: "#7a1420", width: 0.5 }, colorbar: { title: "own light loss", thickness: 8, x: 1 },
    },
    text2: neighborNodes.map((n) => n.name),
    hovertext: neighborNodes.map((n) => `${n.name}: ${(n.own_light_loss * 100 || 0).toFixed(0)}% own light loss`),
    hoverinfo: "text", showlegend: false,
  };
  
  /* Generate a standout marker specific to the conflict epicenter. */
  const epiTrace = epi && lonlat[epi.iso3] ? {
    type: "scattergeo", mode: "markers+text", lon: [lonlat[epi.iso3][0]], lat: [lonlat[epi.iso3][1]],
    text: [epi.iso3], textposition: "top center", textfont: { size: 11, color: "#111" },
    marker: { size: 20, color: "#111", symbol: "star", line: { color: "#fff", width: 1 } },
    hovertext: [`${epi.name} (epicenter)`], hoverinfo: "text", showlegend: false,
  } : null;

  /* Output the overall interface embedding form controls to toggle mapping mechanisms and overlay settings. */
  const yearUsed = envelope.meta?.year;
  const yearMismatch = yearUsed != null && year != null && yearUsed !== year;
  return (
    <section className="view">
      <h3>{title} — {epi?.name ?? selectedCountry}{yearUsed != null ? ` (${yearUsed})` : ""}</h3>
      {yearMismatch && (
        <p className="hint" style={{ marginTop: 0 }}>No conflict spillover for {year}; showing the country's most recent active year, {yearUsed}.</p>
      )}
      <div className="controls" style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
        <label>
          <select value={view} onChange={(e) => setView(e.target.value)}>
            <option value="map">map layout</option>
            <option value="force">force-directed</option>
          </select>
        </label>
        <label style={{ fontSize: "0.85rem" }}>
          <input type="checkbox" checked={sigOnly} onChange={(e) => setSigOnly(e.target.checked)} /> significant only
        </label>
        {spilloverIndex != null && (
          <span style={{ fontSize: "0.85rem" }}>spillover index: <strong>{spilloverIndex.toFixed(2)}</strong></span>
        )}
      </div>

      {view === "map" ? (
        <Plot
          data={[...edgeTraces, nodeTrace, ...(epiTrace ? [epiTrace] : [])]}
          layout={{
            geo: { fitbounds: "locations", showcountries: true, countrycolor: "#ccc", showland: true, landcolor: "#f2f2f2", projection: { type: "natural earth" } },
            margin: { t: 0, b: 0, l: 0, r: 0 }, height: 380,
          }}
          useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false }}
        />
      ) : (
        <svg ref={svgRef} style={{ width: "100%", height: 380 }} />
      )}
      <p className="hint">
        Red edges = neighbor dimmed *with* the epicenter, blue = moved opposite; thickness = strength, dotted = not
        significant. Node color = the neighbor's own light loss. Edges account for shared regional trends
        (partialled out) and real land borders. Toggle "significant only" to drop noisy links.
      </p>
    </section>
  );
}
