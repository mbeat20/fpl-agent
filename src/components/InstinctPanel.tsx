import { useState, type FormEvent } from 'react'
import type { Instinct } from '../types'

const THINKING_RE = /^thinking\b/i

export default function InstinctPanel({ notes: initialNotes }: { notes: Instinct[] }) {
  const [notes, setNotes] = useState(initialNotes)
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = text.trim()
    if (!trimmed || saving) return

    // is_instinct (instincts.py) classifies "remember: X" as an Instinct only
    // when X starts with "thinking" -- prepend it so a plain note like
    // "Rogers to Enzo" still lands here rather than in preferences.md.
    const thought = THINKING_RE.test(trimmed) ? trimmed : `thinking ${trimmed}`

    setSaving(true)
    setError(null)
    try {
      const res = await fetch('/api/recommendations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: `remember: ${thought}` }),
      })
      if (!res.ok) throw new Error(`Request failed: ${res.status}`)
      setNotes((prev) => [...prev, { text: thought, logged_at: new Date().toISOString() }])
      setText('')
    } catch {
      setError("Couldn't log that thought. Check the connection and try again.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-kicker">This Gameweek&rsquo;s Instincts</div>

      <div className="panel-divider" />

      {notes.length === 0 ? (
        <div className="evidence-line">Nothing logged yet this gameweek.</div>
      ) : (
        <div className="instinct-list">
          {notes.map((note, i) => (
            <div className="instinct-row" key={i}>
              <span className="instinct-text">{note.text}</span>
              <span className="instinct-time">
                {new Date(note.logged_at).toLocaleString(undefined, {
                  weekday: 'short',
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </span>
            </div>
          ))}
        </div>
      )}

      <form className="instinct-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="thinking Rogers to Enzo…"
          aria-label="Log an early gameweek thought"
        />
        <button type="submit" className="run-btn" disabled={saving || !text.trim()}>
          {saving ? '…' : 'LOG'}
        </button>
      </form>
      {error && <div className="instinct-error">{error}</div>}
    </div>
  )
}
