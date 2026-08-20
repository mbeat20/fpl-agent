import { useState } from 'react'
import TickerHeader from './components/TickerHeader'
import AskForm from './components/AskForm'
import RecommendationCard from './components/RecommendationCard'
import TransferPlanCard from './components/TransferPlanCard'
import RememberNote from './components/RememberNote'
import TeamDashboard from './components/TeamDashboard'
import { isRememberAck, isTransferPlan, type AskResponse } from './types'

type View = 'ask' | 'team'

export default function App() {
  const [view, setView] = useState<View>('ask')
  const [question, setQuestion] = useState('')
  const [response, setResponse] = useState<AskResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit() {
    if (!question.trim() || loading) return

    setLoading(true)
    setError(null)
    setResponse(null)

    try {
      const res = await fetch('/api/recommendations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      if (!res.ok) throw new Error(`Request failed: ${res.status}`)
      const data: AskResponse = await res.json()
      setResponse(data)
    } catch {
      setError("Couldn't reach the coach. Check the connection and try again.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <TickerHeader />
      <main className="main">
        <div>
          <span className="eyebrow">FPL Coach</span>
          <h1>{view === 'ask' ? 'Ask the coach' : 'My team'}</h1>
        </div>

        <div className="view-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={view === 'ask'}
            className={`view-tab ${view === 'ask' ? 'active' : ''}`}
            onClick={() => setView('ask')}
          >
            Ask
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'team'}
            className={`view-tab ${view === 'team' ? 'active' : ''}`}
            onClick={() => setView('team')}
          >
            My Team
          </button>
        </div>

        {view === 'team' ? (
          <TeamDashboard />
        ) : (
          <>
            <AskForm
              question={question}
              onQuestionChange={setQuestion}
              onSubmit={handleSubmit}
              loading={loading}
            />

            {error && (
              <div className="error-panel">
                <span className="tag">Error</span>
                {error}
              </div>
            )}

            {response && isRememberAck(response) && <RememberNote text={response.remembered} />}
            {response && !isRememberAck(response) && isTransferPlan(response) && (
              <TransferPlanCard plan={response} />
            )}
            {response && !isRememberAck(response) && !isTransferPlan(response) && (
              <RecommendationCard recommendation={response} />
            )}
          </>
        )}
      </main>
    </>
  )
}
