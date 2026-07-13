# `frontend/` — interactive dashboard

React + Vite, with D3.js and Plotly.js for the visualizations and a single
`GlobalContext` (React context/hooks) for the shared country + time-window
selection that links every view.

```bash
npm install
npm run dev                    # http://localhost:5173 (proxies /api -> :5050 in dev, see vite.config.js)
```

| View | Component(s) |
|---|---|
| Overview & KPIs | `views/KPISummary.jsx`, `views/ResidualChoropleth.jsx`, `views/ResidualRanking.jsx` |
| Conflict Types vs Decay | `views/ConflictTypeDecay.jsx` |
| Shock vs Recovery | `views/RecoveryTradeoffBubble.jsx` |
| Conflict vs Spillover | `views/SpilloverNetwork.jsx` |
| Trade vs Conflict | `views/TradeTimeline.jsx`, `views/TradeSankey.jsx` |
| Severity Ranking | `views/SeverityRanking.jsx` |

All views read/write `state/GlobalContext.jsx`, so selecting a country or
brushing a time window anywhere updates every other view (overview-first,
filter, details-on-demand). `api/client.js` centralizes fetches; `api/useFetch.js`
gives every view consistent loading/error handling.
