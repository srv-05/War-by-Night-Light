import { useEffect, useState } from "react";

/**
 * Runs `fetcher()` whenever `deps` changes and exposes `{data, loading, error}`.
 * Every view uses this so loading/error states are handled consistently (§7)
 * without each component reimplementing the same three `useState`s.
 */
export function useFetch(fetcher, deps) {
  const [state, setState] = useState({ data: null, loading: true, error: null });

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));

    fetcher()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
