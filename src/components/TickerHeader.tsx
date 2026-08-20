const MESSAGE =
  'ASK ANYTHING ABOUT YOUR SQUAD · CAPTAIN CALLS, TRANSFER VERDICTS & LONG-TERM PLANS · ' +
  'WEIGHTED BY TRUSTED CREATOR EVIDENCE · PRICE-RISE WARNINGS BEFORE THE DEADLINE · '

export default function TickerHeader() {
  return (
    <div className="ticker-bar" aria-hidden="true">
      <div className="ticker-track">
        <span>{MESSAGE}</span>
        <span>{MESSAGE}</span>
      </div>
    </div>
  )
}
