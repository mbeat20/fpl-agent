import { useCallback, useEffect, useRef, useState } from 'react'
import {
  SOURCE_META,
  SUMMARY_LABELS,
  ageLabel,
  overallFreshness,
  type DataFreshness,
  type IngestionKind,
  type IngestionRun,
  type IngestionStatus,
} from '../types'

/**
 * The refresh control: how old the data is, and the button that fixes it.
 *
 * Ingestion stays on demand (ADR-0002) — this schedules nothing. What it
 * changes is that "refresh my data" no longer means opening a terminal, and
 * that every page can finally say how old what it is showing is.
 *
 * Three things here are deliberate rather than incidental.
 *
 * **The two sources are separate buttons.** FPL data is ~100 requests to two
 * public APIs and no model calls: free, pressable as often as you like.
 * Creator content bills Grok, Claude and Voyage per run. Rendering them
 * identically is how someone spends money by reflex, so the billed one holds
 * a confirmation step and states its cost before the click, not after.
 *
 * **The header badge reports the WORST source, not the newest.** A stamp
 * exists to make you open the panel when something is wrong; a fresh FPL run
 * masking three-week-old creator content would defeat it entirely.
 *
 * **Polling stops.** While a run is in flight the status is re-read every few
 * seconds and then the interval is cleared. The endpoint is free (ADR-0015),
 * but a permanent timer is still a request every few seconds forever, and the
 * only reason to poll is that something is moving.
 */
export default function RefreshControl({ onRefreshed }: { onRefreshed?: () => void }) {
  const [status, setStatus] = useState<IngestionStatus | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<IngestionKind | null>(null)
  const [confirming, setConfirming] = useState<IngestionKind | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Whether a run was in flight last time we looked. The transition from
  // running to not-running is what tells the pages around us that their own
  // data is now behind — without it a finished refresh leaves a dashboard
  // showing pre-refresh numbers until someone reloads by hand.
  const wasRunning = useRef(false)
  const root = useRef<HTMLDivElement | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/ingest/status')
      if (!res.ok) throw new Error(String(res.status))
      const data: IngestionStatus = await res.json()
      setStatus(data)

      if (wasRunning.current && data.running.length === 0) {
        onRefreshed?.()
      }
      wasRunning.current = data.running.length > 0
    } catch {
      // A status read failing is not worth an error panel: it means the
      // backend is down, which every other panel on the page is already
      // saying more usefully. The badge simply shows nothing.
      setStatus(null)
    }
  }, [onRefreshed])

  useEffect(() => {
    load()
  }, [load])

  // A popover has to be dismissable the two ways every popover is, or it
  // reads as stuck: Escape, and a click anywhere else. Without these the only
  // way out is finding the ✕ again, which on the Team view means the panel
  // sits over the dashboard you were trying to look at.
  useEffect(() => {
    if (!open) return

    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    function onPointerDown(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }

    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onPointerDown)
    }
  }, [open])

  // Only while something is actually running.
  useEffect(() => {
    if (!status || status.running.length === 0) return
    const id = setInterval(load, 4000)
    return () => clearInterval(id)
  }, [status, load])

  async function start(kind: IngestionKind) {
    setBusy(kind)
    setError(null)
    setConfirming(null)
    try {
      const res = await fetch(`/api/ingest/${kind}`, { method: 'POST' })
      if (res.status === 409) {
        // Not really an error: someone (or the CLI) already has it running.
        // Reloading the status shows them that run rather than a complaint.
        await load()
        return
      }
      if (!res.ok) throw new Error(String(res.status))
      // Mark it as in flight immediately so the poll effect starts without
      // waiting for the next status read to notice.
      wasRunning.current = true
      await load()
      setOpen(true)
    } catch {
      setError(`Couldn't start the ${SOURCE_META[kind].label.toLowerCase()} refresh.`)
    } finally {
      setBusy(null)
    }
  }

  const overall = overallFreshness(status)

  return (
    <div className="refresh" ref={root}>
      <button
        type="button"
        className={`refresh-badge refresh-${overall.tone}`}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title="Data freshness and refresh"
      >
        <span className={`refresh-dot ${overall.tone === 'running' ? 'refresh-spin' : ''}`}>
          {overall.tone === 'running' ? '◌' : '●'}
        </span>
        <span className="refresh-badge-label">{overall.label}</span>
      </button>

      {open && (
        <div className="refresh-panel" role="dialog" aria-label="Data freshness">
          <div className="refresh-panel-head">
            <span className="eyebrow">Data</span>
            <button type="button" className="refresh-close" onClick={() => setOpen(false)}>
              ✕
            </button>
          </div>

          {status === null ? (
            <div className="refresh-empty">
              Can&rsquo;t reach the backend, so there is nothing to say about the data&rsquo;s
              age.
            </div>
          ) : (
            (['fpl', 'creators'] as IngestionKind[]).map((kind) => (
              <SourceRow
                key={kind}
                kind={kind}
                freshness={status.freshness.find((f) => f.kind === kind)}
                run={status.runs.find((r) => r.kind === kind)}
                running={status.running.includes(kind)}
                busy={busy === kind}
                confirming={confirming === kind}
                onAsk={() => setConfirming(kind)}
                onCancel={() => setConfirming(null)}
                onStart={() => start(kind)}
              />
            ))
          )}

          {error && <div className="refresh-error">{error}</div>}

          {/* The CLI is not deprecated by this panel — it is the same run,
              through the same ledger, so a terminal refresh shows up here
              too. Worth saying, because otherwise the button looks like it
              replaced something. */}
          <div className="refresh-foot">
            Same runs as <code>fpl-ingest</code> and <code>fpl-ingest-creators</code> — either
            way it is recorded here.
          </div>
        </div>
      )}
    </div>
  )
}

function SourceRow({
  kind,
  freshness,
  run,
  running,
  busy,
  confirming,
  onAsk,
  onCancel,
  onStart,
}: {
  kind: IngestionKind
  freshness: DataFreshness | undefined
  run: IngestionRun | undefined
  running: boolean
  busy: boolean
  confirming: boolean
  onAsk: () => void
  onCancel: () => void
  onStart: () => void
}) {
  const meta = SOURCE_META[kind]
  const tone =
    freshness === undefined
      ? 'unknown'
      : freshness.source === 'none'
        ? 'unknown'
        : freshness.is_stale
          ? 'warn'
          : 'ok'

  return (
    <div className="refresh-row">
      <div className="refresh-row-head">
        <div>
          <div className="refresh-row-title">
            {meta.label}
            {meta.billed && <span className="refresh-billed">costs money</span>}
          </div>
          <div className="refresh-row-blurb">{meta.blurb}</div>
        </div>
        <div className={`refresh-age refresh-${tone}`}>
          {running
            ? 'running…'
            : freshness?.source === 'none'
              ? 'never'
              : (ageLabel(freshness?.age_seconds ?? null) ?? '—')}
        </div>
      </div>

      {/* The note carries the part a timestamp cannot: whether that figure
          came from a recorded run or from the data's own newest row, and
          whether it is old enough to caveat what rests on it. */}
      {freshness?.note && <div className="refresh-note">{freshness.note}</div>}

      <LastRun run={run} running={running} />

      {confirming ? (
        <div className="refresh-confirm">
          <div className="refresh-confirm-text">{meta.expect}</div>
          <div className="refresh-actions">
            <button type="button" className="refresh-go refresh-go-billed" onClick={onStart}>
              Yes, refresh anyway
            </button>
            <button type="button" className="refresh-cancel" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="refresh-go"
          disabled={running || busy}
          onClick={meta.billed ? onAsk : onStart}
        >
          {running ? 'Refreshing…' : busy ? 'Starting…' : meta.action}
        </button>
      )}
    </div>
  )
}

/**
 * The last attempt, whatever became of it.
 *
 * A failure is shown rather than hidden behind the age above, because those
 * two facts point in opposite directions: the data can be four hours old
 * *and* the last three refreshes can have failed, and only the second one
 * tells you the number is going to keep getting worse.
 */
function LastRun({ run, running }: { run: IngestionRun | undefined; running: boolean }) {
  if (!run || running) return null

  if (run.status === 'failed' || run.status === 'abandoned') {
    return (
      <div className="refresh-run refresh-run-bad">
        <span className="tag">{run.status === 'failed' ? 'Failed' : 'Interrupted'}</span>
        <span className="refresh-run-detail">{run.error ?? 'No detail recorded.'}</span>
      </div>
    )
  }

  if (run.status !== 'succeeded') return null

  return (
    <div className="refresh-run">
      <span className="refresh-run-detail">
        {summaryLine(run)}
        {run.duration_seconds !== null && ` · took ${Math.round(run.duration_seconds)}s`}
        {run.trigger === 'cli' && ' · from the CLI'}
      </span>
    </div>
  )
}

/**
 * The summary as a sentence, built from whatever counters the run actually
 * reported.
 *
 * Generic rather than a hand-written line per pipeline: both summaries grow a
 * field per ticket, and a renderer that names them individually goes stale
 * silently — it keeps showing an out-of-date subset while looking complete.
 * Zeroes are dropped, because "0 outcomes recorded" is noise on a run whose
 * interesting news was 15 players and 380 fixtures.
 */
function summaryLine(run: IngestionRun): string {
  if (!run.summary) return 'Completed.'
  const parts = Object.entries(run.summary)
    .filter((entry): entry is [string, number] => typeof entry[1] === 'number' && entry[1] > 0)
    // Biggest first, so the headline of the run leads. A run's most
    // interesting number is rarely the one whose key sorts first.
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${v.toLocaleString()} ${SUMMARY_LABELS[k] ?? k.replace(/_/g, ' ')}`)

  if (parts.length === 0) return 'Completed, nothing new.'
  // Capped: the FPL summary reports eleven counters and the full list reads
  // as a wall. The rest are still in the row's own `summary` if anything ever
  // needs them.
  const shown = parts.slice(0, 4)
  const rest = parts.length - shown.length
  return shown.join(', ') + (rest > 0 ? `, +${rest} more` : '')
}
