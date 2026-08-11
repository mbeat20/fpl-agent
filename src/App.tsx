import { useState } from 'react'

interface RecommendationPick {
  player_id: number
  player_name: string
}

interface Alternative {
  player_id: number
  player_name: string
  reason: string
}

interface Recommendation {
  pick: RecommendationPick
  confidence: number
  evidence: string[]
  risks: string[]
  alternative: Alternative
}

interface RememberAck {
  remembered: string
}

function isRememberAck(value: Recommendation | RememberAck): value is RememberAck {
  return 'remembered' in value
}

export default function App() {
  const [question, setQuestion] = useState('')
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null)
  const [savedPreference, setSavedPreference] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!question.trim() || loading) return

    setLoading(true)
    setError(null)
    setRecommendation(null)
    setSavedPreference(null)

    try {
      const res = await fetch('/api/recommendations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      if (!res.ok) throw new Error(`Request failed: ${res.status}`)
      const data: Recommendation | RememberAck = await res.json()
      if (isRememberAck(data)) {
        setSavedPreference(data.remembered)
      } else {
        setRecommendation(data)
      }
    } catch {
      setError('Something went wrong asking the coach. Try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <h1>FPL Coach</h1>

      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Who should I captain? (or “remember: I dislike knee-jerk transfers”)"
          style={{
            flex: 1,
            padding: '10px 12px',
            borderRadius: 6,
            border: '1px solid var(--border)',
            background: 'var(--bg)',
            color: 'var(--text-h)',
            font: 'inherit',
          }}
        />
        <button
          type="submit"
          disabled={loading || !question.trim()}
          style={{
            padding: '10px 16px',
            borderRadius: 6,
            border: 'none',
            background: 'var(--accent)',
            color: '#fff',
            font: 'inherit',
            cursor: loading ? 'default' : 'pointer',
          }}
        >
          {loading ? 'Asking…' : 'Ask'}
        </button>
      </form>

      {error && <p style={{ color: 'var(--danger)' }}>{error}</p>}

      {savedPreference && (
        <p style={{ color: 'var(--text)' }}>Remembered: “{savedPreference}”</p>
      )}

      {recommendation && (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: 20,
            textAlign: 'left',
          }}
        >
          <h2>{recommendation.pick.player_name}</h2>
          <p style={{ color: 'var(--text)', marginBottom: 16 }}>
            Confidence: {Math.round(recommendation.confidence * 100)}%
          </p>

          <h2>Evidence</h2>
          <ul style={{ marginTop: 0, marginBottom: 16 }}>
            {recommendation.evidence.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>

          <h2>Risks</h2>
          <ul style={{ marginTop: 0, marginBottom: 16 }}>
            {recommendation.risks.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>

          <h2>Alternative</h2>
          <p>
            {recommendation.alternative.player_name} — {recommendation.alternative.reason}
          </p>
        </div>
      )}
    </>
  )
}
