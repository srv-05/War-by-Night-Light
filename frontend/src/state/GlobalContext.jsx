import { createContext, useContext, useMemo, useState } from "react";

/**
 * Creates a React Context to manage and share application-wide state.
 * This context stores global variables such as the currently selected country and the active time period.
 */
const GlobalContext = createContext(null);

// Defines the initial state for the time window selection.
// This is used across components before a specific timeframe is explicitly chosen by the user.
export const DEFAULT_TIME_WINDOW = { start: "", end: "" };

export function GlobalProvider({ children }) {
  const [selectedCountry, setSelectedCountry] = useState(null); // Holds the ISO3 code of the currently selected country, or null if no country is selected.
  const [timeWindow, setTimeWindow] = useState(DEFAULT_TIME_WINDOW);
  const [year, setYear] = useState(2023); // Represents the selected year for rendering map data.

  const value = useMemo(
    () => ({
      selectedCountry,
      setSelectedCountry,
      timeWindow,
      setTimeWindow,
      year,
      setYear,
      reset: () => setSelectedCountry(null),
    }),
    [selectedCountry, timeWindow, year]
  );

  return <GlobalContext.Provider value={value}>{children}</GlobalContext.Provider>;
}

export function useGlobal() {
  const ctx = useContext(GlobalContext);
  if (!ctx) throw new Error("useGlobal must be used within a <GlobalProvider>");
  return ctx;
}
