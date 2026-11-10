import { useEffect, useState } from 'react'
import AskForm from '../components/AskForm'
import RememberNote from '../components/RememberNote'
import Transcript from '../components/Transcript'
import { isRememberAck, type AskResponse, type ConversationTurn } from '../types'

/**
 * The conversation (issue 27).
 *
 * Lifted out of App.tsx unchanged in behaviour. The transcript is server-held
 * and gameweek-scoped, so it survives a refresh and the model and the screen
 * cannot disagree about what was said.
 *
 * The only page that keeps the narrow measure. Everything here is prose being
 * read at length, and a 1100px line is unreadable for that; the other four
 * pages are boards of facts and want the width.
 */
export default function CoachPage() {
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState<ConversationTurn[]>([])
  // A remembered Preference/Instinct is acknowledged but never enters the
  // transcript — it is a standing note, not a turn of an argument.
  const [remembered, setRemembered] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/conversation')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && data) setTurns(data.turns)
      })
      .catch(() => {
        /* an empty transcript is a fine starting state */
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSubmit() {
    if (!question.trim() || loading) return

    const asked = question
    setLoading(true)
    setError(null)
    setRemembered(null)
    // Shown immediately rather than waiting for the round trip: a reasoning
    // call runs for many seconds, and a question that vanishes from the input
    // without appearing anywhere reads as a dropped request.
    setTurns((prev) => [
      ...prev,
      { role: 'user', text: asked, recommendation: null, plan: null, created_at: '' },
    ])
    setQuestion('')

    try {
      const res = await fetch('/api/recommendations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: asked }),
      })
      if (!res.ok) throw new Error(`Request failed: ${res.status}`)
      const data: AskResponse = await res.json()

      if (isRememberAck(data)) {
        setRemembered(data.remembered)
        // Not a turn: drop the optimistic user bubble again.
        setTurns((prev) => prev.slice(0, -1))
        return
      }

      // Re-read rather than appending locally: the server is the authority on
      // the transcript, and it has just written this turn.
      const refreshed = await fetch('/api/conversation')
      if (refreshed.ok) setTurns((await refreshed.json()).turns)
    } catch {
      setError("Couldn't reach the coach. Check the connection and try again.")
      setTurns((prev) => prev.slice(0, -1))
      setQuestion(asked)
    } finally {
      setLoading(false)
    }
  }

  async function handleReset() {
    setRemembered(null)
    setError(null)
    try {
      const res = await fetch('/api/conversation', { method: 'DELETE' })
      if (res.ok) setTurns((await res.json()).turns)
    } catch {
      setError("Couldn't clear the conversation.")
    }
  }

  return (
    <div className="coach-column">
      <Transcript turns={turns} />

      {error && (
        <div className="error-panel">
          <span className="tag">Error</span>
          {error}
        </div>
      )}

      {remembered && <RememberNote text={remembered} />}

      <AskForm
        question={question}
        onQuestionChange={setQuestion}
        onSubmit={handleSubmit}
        loading={loading}
        placeholder={turns.length > 0 ? 'Push back, or ask something else…' : undefined}
      />

      {turns.length > 0 && (
        <button type="button" className="reset-chat" onClick={handleReset}>
          Start a new conversation
        </button>
      )}
    </div>
  )
}
