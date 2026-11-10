import { useEffect, useState } from 'react'

export interface Endpoint<T> {
  data: T | null
  /** Still in flight. Distinct from `data === null`, which is a real answer. */
  loading: boolean
  /** The request itself failed. Distinct again: a 500 is not an empty result. */
  failed: boolean
}

/**
 * One endpoint, loaded on its own.
 *
 * This exists to break up `Promise.all`. The dashboard used to fetch all eight
 * panels together behind a single render gate that included
 * `/team/suggestion` — the one endpoint that can make a Claude call — so the
 * free, instant deadline countdown waited on the most expensive request on the
 * page. Measured at around twenty seconds when the cached suggestion's
 * gameweek had rolled over.
 *
 * Splitting them means a panel paints the moment its own data lands, and a
 * panel whose endpoint is slow or broken is the only thing affected. That is
 * also what ADR-0015 implies without saying: if every read is free, there is
 * no reason to batch them for cost, and batching them for tidiness costs the
 * user the fastest panel on the screen.
 *
 * Three states rather than two, for the same reason a `ChecklistItem` has
 * three: "loaded, and the answer is nothing" and "we could not ask" are
 * different facts, and collapsing them makes a broken panel look like an
 * empty one.
 */
export function useEndpoint<T>(path: string): Endpoint<T> {
  const [state, setState] = useState<Endpoint<T>>({
    data: null,
    loading: true,
    failed: false,
  })

  useEffect(() => {
    let cancelled = false
    setState({ data: null, loading: true, failed: false })

    fetch(path)
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status))
        return (await res.json()) as T
      })
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, failed: false })
      })
      .catch(() => {
        if (!cancelled) setState({ data: null, loading: false, failed: true })
      })

    return () => {
      cancelled = true
    }
  }, [path])

  return state
}
