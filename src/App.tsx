import { useState } from 'react'
import TickerHeader from './components/TickerHeader'
import AskForm from './components/AskForm'
import RecommendationCard from './components/RecommendationCard'
import TransferPlanCard from './components/TransferPlanCard'
import RememberNote from './components/RememberNote'
import { isRememberAck, isTransferPlan, type AskResponse } from './types'

export default function App() {
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
          <h1>Ask the coach</h1>
        </div>

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
      </main>
    </>
  )
}
