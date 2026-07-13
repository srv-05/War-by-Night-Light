import React from "react";
import { api } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useGlobal } from "../state/GlobalContext";
import "./KPISummary.css";

/*
 * This React component displays a summary of Key Performance Indicators (KPIs).
 * It fetches the summary data on component mount and renders a series of cards.
 */
export default function KPISummary() {
  const { year } = useGlobal();
  const { data, loading, error } = useFetch(() => api.kpiSummary(year), [year]);

  /*
   * Returns fallback UI structures depending on the state of the data request.
   */
  if (loading) return <div className="kpi-summary loading">Loading metrics...</div>;
  if (error) return <div className="kpi-summary error">Error loading metrics</div>;

  const metrics = data || {};

  /*
   * Renders the individual metrics such as conflict count, light loss, and recovery time.
   * If a particular metric is undefined or null, it falls back to a double dash ("--").
   */
  return (
    <section className="kpi-summary">
      <div className="kpi-card">
        <span className="kpi-label">Countries in conflict:</span>
        <span className="kpi-value highlight"> {metrics.countries_in_conflict ?? "--"}</span>
      </div>
      <div className="kpi-card">
        <span className="kpi-label">Average light loss:</span>
        <span className="kpi-value warning"> {metrics.avg_light_loss_pct != null ? `${metrics.avg_light_loss_pct}%` : "--"}</span>
      </div>
      <div className="kpi-card">
        <span className="kpi-label">Average recovery:</span>
        <span className="kpi-value highlight"> {metrics.avg_recovery_pct != null ? `${metrics.avg_recovery_pct}%` : "--"}</span>
      </div>
      <div className="kpi-card">
        <span className="kpi-label">Median recovery time:</span>
        <span className="kpi-value highlight"> {metrics.median_recovery_time_months != null ? `${metrics.median_recovery_time_months} months` : "--"}</span>
      </div>
      <div className="kpi-card">
        <span className="kpi-label">Countries with spillovers:</span>
        <span className="kpi-value highlight"> {metrics.countries_with_spillovers ?? "--"}</span>
      </div>
    </section>
  );
}
