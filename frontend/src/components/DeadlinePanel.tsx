import { useEffect, useState } from 'react'
import type { ChecklistItem, DeadlineChecklist } from '../types'
import { URGENT_SECONDS, countdownLabel } from '../types'

/**
 * Issue 23: the reason to open this page on a Friday.
 *
 * The suggestion card below answers "what does the AI think", which is the
 * question the Coach page already answers. This answers the one the user
 * actually has: is anything about my team broken, and how long do I have?
 *
 * It reports and stops. Every item states a fact -- "Two players in your XI
 * are flagged" -- and none of them says what to do about it; preferences.md is
 * emphatic that the final call is the user's, and a checklist that instructed
 * would be a second, worse recommendation engine sitting above the real one.
 * `attention` is styled as "look here", never as an error.
 *
 * The countdown ticks locally off `seconds_remaining` rather than re-fetching.
 * The endpoint is free to call, but a poll every second would still be a
 * request every second, and the clock is arithmetic the browser can do.
 *
 * LAYOUT: the clock and the checklist sit side by side rather than stacked.
 * They are the two halves of one question -- how long, and is anything wrong
 * -- and stacking them meant the checklist began below the fold on the one
 * screen designed to be taken in at a glance. Price movement used to be a
 * third section in here and is now its own card (PriceRiskPanel): three
 * unrelated jobs in one panel is what made this the longest thing on the
 * dashboard.
 */
export default function DeadlinePanel({ checklist }: { checklist: DeadlineChecklist }) {
  const { countdown } = checklist
  const [remaining, setRemaining] = useState<number | null>(countdown.seconds_remaining)

  useEffect(() => {
    setRemaining(countdown.seconds_remaining)
    if (countdown.seconds_remaining === null) return
    const id = setInterval(() => {
      setRemaining((s) => (s === null ? null : s - 1))
    }, 1000)
    return () => clearInterval(id)
  }, [countdown.seconds_remaining])

  const label = countdownLabel(remaining)
  const urgent = remaining !== null && remaining > 0 && remaining <= URGENT_SECONDS

  return (
    <div className="panel deadline-panel">
      <div className="deadline-split">
        <div className="deadline-clock-col">
          <div className="panel-kicker">
            {countdown.gameweek !== null ? `GW${countdown.gameweek} deadline` : 'Next deadline'}
          </div>
          <div className={`countdown-clock ${urgent ? 'countdown-urgent' : ''}`}>
            {label ?? 'Not known'}
          </div>
          {countdown.deadline !== null && (
            <div className="countdown-stamp">{formatDeadline(countdown.deadline)}</div>
          )}

          {/* Set when the countdown is running on a proxy boundary, or when a
              gameweek is already under way and its team is locked. Both change
              what the number above means, so neither is optional. */}
          {countdown.note && <div className="countdown-note">{countdown.note}</div>}
        </div>

        <div className="deadline-check-col">
          <div className="deadline-check-head">
            <span className="panel-kicker">Your squad as it stands</span>
            {checklist.attention_count > 0 && (
              <span className="countdown-flag">{checklist.attention_count} to look at</span>
            )}
          </div>
          <div className="checklist">
            {checklist.items.map((item) => (
              <ChecklistRow key={item.key} item={item} />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function ChecklistRow({ item }: { item: ChecklistItem }) {
  return (
    <div className={`check-row check-${item.state}`}>
      <span className={`check-mark check-mark-${item.state}`} aria-hidden="true">
        {item.state === 'ok' ? '✓' : item.state === 'attention' ? '!' : '?'}
      </span>
      <div className="check-body">
        <div className="check-title">{item.title}</div>
        <div className="check-detail">{item.detail}</div>
      </div>
    </div>
  )
}

// The absolute time, alongside the countdown. A relative figure alone is hard
// to plan around -- "2d 4h" does not tell you whether that lands before or
// after Saturday morning.
function formatDeadline(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}
