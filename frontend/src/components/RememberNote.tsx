export default function RememberNote({ text }: { text: string }) {
  return (
    <div className="remember-note">
      <span className="tag">REMEMBERED</span>
      <span>&ldquo;{text}&rdquo;</span>
    </div>
  )
}
