import type { FormEvent } from 'react'

interface AskFormProps {
  question: string
  onQuestionChange: (value: string) => void
  onSubmit: () => void
  loading: boolean
}

export default function AskForm({ question, onQuestionChange, onSubmit, loading }: AskFormProps) {
  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    onSubmit()
  }

  return (
    <form className="ask-form" onSubmit={handleSubmit}>
      <span className="eyebrow">Ask the coach</span>
      <div className="ask-row">
        <input
          type="text"
          value={question}
          onChange={(e) => onQuestionChange(e.target.value)}
          placeholder="Who should I captain this week?"
          aria-label="Question for the coach"
        />
        <button type="submit" className="run-btn" disabled={loading || !question.trim()}>
          {loading ? 'THINKING…' : 'RUN →'}
        </button>
      </div>
    </form>
  )
}
