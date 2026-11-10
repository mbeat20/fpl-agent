import type { Evidence } from '../types'
import { timestampLabel } from '../types'

/**
 * Issue 17: one evidence line, plus who said it and a link to where.
 *
 * Shared by RecommendationCard and TransferPlanCard so a plan step's evidence
 * can never drift into a second, weaker rendering of the same thing. The href
 * comes straight from the backend, which built it from the tool result -- this
 * component must never assemble a url of its own.
 */
export default function EvidenceLine({ item }: { item: Evidence }) {
  const { citation } = item
  const stamp = citation ? timestampLabel(citation.start_seconds) : null

  return (
    <div className="evidence-line">
      {/* Wrapped so the flex row treats prose + citation as one item and the
          citation wraps with the sentence rather than being pushed out to its
          own column. */}
      <span>
        {item.text}
        {citation && (
          <a
            className="citation"
            href={citation.url}
            target="_blank"
            rel="noreferrer"
            title={`${citation.creator_name} — ${citation.trust_tier} trust`}
          >
            {citation.creator_name}
            {stamp && <span className="citation-stamp">{stamp}</span>}
          </a>
        )}
      </span>
    </div>
  )
}
