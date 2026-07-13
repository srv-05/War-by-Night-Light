import React from "react";
import Plot from "react-plotly.js";
import { api } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useGlobal } from "../state/GlobalContext";

/* Formats RGBA strings representing price changes with conditional color assignments. */
function getShockColor(pct, alpha = 1) {
  /* Provides a fallback style for inputs that denote an unchanged state or missing data. */
  if (pct === null || pct === undefined || isNaN(pct) || pct === 0) {
    return `rgba(122, 133, 146, ${alpha})`;
  }
  if (pct > 0) {
    /* Computes interpolation between neutral and warm colors highlighting increases. */
    const ratio = Math.min(pct / 45, 1);
    const r = Math.round(122 + (230 - 122) * ratio);
    const g = Math.round(133 + (57 - 133) * ratio);
    const b = Math.round(146 + (70 - 146) * ratio);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  } else {
    /* Calculates a gradient between neutral and cool tones mapped to percentage drops. */
    const ratio = Math.min(Math.abs(pct) / 20, 1);
    const r = Math.round(122 + (67 - 122) * ratio);
    const g = Math.round(133 + (97 - 133) * ratio);
    const b = Math.round(146 + (238 - 146) * ratio);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
}

/* Exports an interactive Sankey diagram plotting trade destinations and impacts. */
export default function TradeSankey() {
  /* Reads global configurations checking the currently focused country state. */
  const { selectedCountry } = useGlobal();
  
  /* Queries the API service to get all relevant trade dependencies for that specific context. */
  const { data: envelope, loading, error } = useFetch(
    () => (selectedCountry ? api.trade(selectedCountry) : Promise.resolve(null)),
    [selectedCountry]
  );

  const title = "Commodity World-Price Change";
  
  /* Short circuits the render tree showing empty states or active API transaction statuses. */
  if (!selectedCountry) {
    return <section className="view"><h3>{title}</h3><p className="status">Select a country above to view the trade impact.</p></section>;
  }
  
  if (loading || !envelope) {
    return <section className="view"><h3>{title} — {selectedCountry}</h3><p className="status">Loading…</p></section>;
  }
  
  if (error) {
    return <section className="view"><h3>{title} — {selectedCountry}</h3><p className="status error">Failed to load: {error.message}</p></section>;
  }

  const { commodities = [] } = envelope.data;

  /* Defers rendering early if the given country exposes absolutely no trading data points. */
  if (commodities.length === 0) {
    return (
      <section className="view">
        <h3>{title} — {selectedCountry}</h3>
        <p className="status">No commodity export data available for this country.</p>
      </section>
    );
  }

  const nodes = [];
  const nodeMap = new Map();
  
  /* Starts registering primary root nodes defining where exports originate. */
  nodes.push({ name: selectedCountry, color: "#f4a261" });
  nodeMap.set(selectedCountry, 0);

  /* Identifies unique destination locations registering individual node entries for each. */
  const destSet = new Set();
  commodities.forEach(c => (c.destinations || []).forEach(d => destSet.add(d.destination)));
  Array.from(destSet).forEach(dest => {
    nodes.push({ name: dest, color: "#4a5568" });
    nodeMap.set(dest, nodes.length - 1);
  });

  /* Accumulates commodity items translating numeric differences to display strings. */
  commodities.forEach(c => {
    const diff = c.price_change_pct;
    const diffStr = diff > 0 ? `+${diff}% world price` : (diff < 0 ? `${diff}% world price` : `no world price change`);
    const label = `${c.name}<br>${diffStr}`;
    
    nodes.push({ name: label, color: getShockColor(diff, 0.9), originalName: c.name, diffStr: diffStr });
    nodeMap.set(c.name, nodes.length - 1);
  });

  const links = [];

  /* Populates graph edges linking origin roots through intermediate commodity nodes towards final destinations. */
  commodities.forEach(c => {
    const cIdx = nodeMap.get(c.name);
    const diff = c.price_change_pct;
    const linkColor = getShockColor(diff, 0.4);
    
    /* Configures the direct relations stretching between origin points and related commodities. */
    links.push({
      source: 0,
      target: cIdx,
      value: Math.max(1, c.trade_value_kusd),
      color: linkColor,
      customdata: `Total Export: $${c.trade_value_kusd.toLocaleString()}k<br>${nodes[cIdx].diffStr}`
    });
    
    /* Loops and models all the subsequent paths out towards discrete destination locations. */
    (c.destinations || []).forEach(d => {
      const dIdx = nodeMap.get(d.destination);
      links.push({
        source: cIdx,
        target: dIdx,
        value: Math.max(0.1, d.trade_value_kusd),
        color: linkColor,
        customdata: `Export to ${d.destination}: $${d.trade_value_kusd.toLocaleString()}k<br>${nodes[cIdx].diffStr}`
      });
    });
  });

  /* Aggregates structured chart configurations wrapping the assembled components into standard Plotly shapes. */
  const plotData = [{
    type: "sankey",
    orientation: "h",
    node: {
      pad: 25,
      thickness: 20,
      line: { color: "transparent", width: 0 },
      label: nodes.map(n => n.name),
      color: nodes.map(n => n.color)
    },
    link: {
      source: links.map(l => l.source),
      target: links.map(l => l.target),
      value: links.map(l => l.value),
      color: links.map(l => l.color),
      customdata: links.map(l => l.customdata),
      hovertemplate: "%{source.label} → %{target.label}<br>%{customdata}<extra></extra>"
    }
  }];

  /* Maps standard layout properties to dictate text styles and scaling strategies. */
  const layout = {
    font: { size: 11, color: "#fff" },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    margin: { t: 40, l: 20, r: 20, b: 20 },
    height: 600
  };

  /* Returns JSX components outlining interactive diagram frames and supporting visual legends. */
  return (
    <section className="view" style={{ minHeight: "750px" }}>
      <h3 style={{ textAlign: "center", marginBottom: "0.5rem", letterSpacing: "2px", fontSize: "0.85rem", color: "#adb5bd", textTransform: "uppercase" }}>
        ORIGIN &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; COMMODITY — WORLD-PRICE CHANGE &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; DESTINATION
      </h3>
      <div style={{ height: "600px", width: "100%", color: "black" }}>
        <Plot
          data={plotData}
          layout={layout}
          config={{ responsive: true, displayModeBar: false }}
          style={{ width: "100%", height: "100%" }}
          useResizeHandler={true}
        />
      </div>
      
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "1rem", marginTop: "2rem", fontSize: "0.85rem", color: "#adb5bd" }}>
        <span>−20%</span>
        <div style={{
          width: "250px", height: "12px", borderRadius: "6px",
          background: "linear-gradient(to right, rgb(67, 97, 238), rgb(122, 133, 146), rgb(230, 57, 70))"
        }}></div>
        <span>+45%</span>
      </div>
      
      <div style={{ display: "flex", justifyContent: "center", gap: "2rem", marginTop: "1rem", fontSize: "0.85rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <div style={{ width: "12px", height: "12px", backgroundColor: "rgb(67, 97, 238)", borderRadius: "2px" }}></div>
          <span>price fell</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <div style={{ width: "12px", height: "12px", backgroundColor: "rgb(122, 133, 146)", borderRadius: "2px" }}></div>
          <span>flat / no world price</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <div style={{ width: "12px", height: "12px", backgroundColor: "rgb(230, 57, 70)", borderRadius: "2px" }}></div>
          <span>price rose</span>
        </div>
      </div>
    </section>
  );
}
