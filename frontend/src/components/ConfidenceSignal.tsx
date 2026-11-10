const BAR_HEIGHTS = [10, 16, 22, 28, 14]

export default function ConfidenceSignal({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100)
  const litBars = Math.round((confidence * BAR_HEIGHTS.length) / 1)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 28 }}>
        {BAR_HEIGHTS.map((height, i) => (
          <div
            key={i}
            style={{
              width: 4,
              height,
              borderRadius: 1,
              background: i < litBars ? 'var(--accent)' : 'var(--border)',
            }}
          />
        ))}
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-dim)' }}>
        <strong style={{ color: 'var(--text)', fontSize: 13 }}>{pct}%</strong> conf.
      </div>
    </div>
  )
}
