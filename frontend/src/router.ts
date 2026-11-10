import { useSyncExternalStore } from 'react'

/**
 * A ~40-line history router, instead of a dependency.
 *
 * The app has five pages and needs three things from routing: the URL
 * reflects where you are, the back button works, and a page is linkable. That
 * is the whole requirement — no nested routes, no loaders, no route params.
 * react-router would do it, and would also be the frontend's first
 * non-React dependency, which is a poor trade for `useSyncExternalStore` plus
 * a `popstate` listener.
 *
 * Why routing at all, rather than the `useState<View>` this replaces: the
 * dashboard is being split into four pages, and a tab held in component state
 * cannot be linked to, cannot be reloaded into, and loses your place on every
 * refresh — on a page whose whole purpose is a countdown you reload to watch.
 */

export const ROUTES = [
  { path: '/', label: 'Deadline' },
  { path: '/squad', label: 'Squad' },
  { path: '/coach', label: 'Coach' },
  { path: '/plan', label: 'Plan' },
  { path: '/discourse', label: 'Discourse' },
] as const

export type RoutePath = (typeof ROUTES)[number]['path']

const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  window.addEventListener('popstate', listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('popstate', listener)
  }
}

function snapshot(): string {
  return window.location.pathname
}

export function navigate(path: string): void {
  if (path === window.location.pathname) return
  window.history.pushState(null, '', path)
  // pushState fires no event of its own, so in-app navigation has to notify
  // subscribers by hand. Without this the URL changes and nothing re-renders.
  for (const listener of listeners) listener()
  // A new page starts at the top. Browsers restore scroll on a real
  // navigation but not on a pushState, and landing halfway down a page you
  // have never seen reads as a broken render.
  window.scrollTo(0, 0)
}

/**
 * The current path, normalised to a known route.
 *
 * An unknown path resolves to `/` rather than rendering a not-found page.
 * This is a single-user local app: a typo'd URL should land you on the
 * deadline screen, not on an error.
 */
export function useRoute(): RoutePath {
  const path = useSyncExternalStore(subscribe, snapshot, () => '/')
  const known = ROUTES.find((route) => route.path === path)
  return known ? known.path : '/'
}
