import { GlobalProvider, useGlobal } from "./state/GlobalContext";
import KPISummary from "./views/KPISummary";
import ResidualChoropleth from "./views/ResidualChoropleth";
import ResidualRanking from "./views/ResidualRanking";
import ConflictTypeDecay from "./views/ConflictTypeDecay";


import RecoveryTradeoffBubble from "./views/RecoveryTradeoffBubble";
import SpilloverNetwork from "./views/SpilloverNetwork";

import TradeSankey from "./views/TradeSankey";
import "./App.css";

function Header() {
  const { selectedCountry, reset } = useGlobal();
  return (
    <header className="app-header">
      <div>
        <h1>War by Night Light</h1>
        <p className="subtitle">The economic footprint of conflict, seen from orbit.</p>
      </div>
      <div className="selection-badge">
        {selectedCountry ? <strong>{selectedCountry}</strong> : <span>Global overview</span>}
        {selectedCountry && (
          <button onClick={reset} className="clear-btn">
            Clear
          </button>
        )}
      </div>
    </header>
  );
}

/**
 * Renders the main dashboard structure of the application.
 * It integrates multiple view components and organizes them into a responsive layout.
 */
function Dashboard() {
  return (
    <main className="dashboard">
      <Header />
      
      <KPISummary />

      {/* Renders the primary geographic map visualization and the corresponding ranking charts. */}
      <section className="row overview-row">
        <ResidualChoropleth />
        <ResidualRanking />
      </section>

      {/* Renders the visualization depicting the relationship between conflict classifications and light emission decay. */}
      <section className="row">
        <ConflictTypeDecay />
      </section>

      <section className="row" style={{ display: "flex", justifyContent: "center" }}>
        <div style={{ width: "100%", maxWidth: "800px" }}>
          <RecoveryTradeoffBubble />
        </div>
      </section>

      <section className="row" style={{ display: "flex", justifyContent: "center" }}>
        <div style={{ width: "100%", maxWidth: "800px" }}>
          <SpilloverNetwork />
        </div>
      </section>

      <section className="row">
        <TradeSankey />
      </section>
    </main>
  );
}

export default function App() {
  return (
    <GlobalProvider>
      <Dashboard />
    </GlobalProvider>
  );
}
