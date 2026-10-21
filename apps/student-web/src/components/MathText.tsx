import { mathPresentation } from '../mathPresentation'
import './mathText.css'

/** Original strings remain the accessible text and are never written back. */
export function MathText({ text, original = false }: { text: string; original?: boolean }) {
  const presentation = original ? null : mathPresentation(text)
  if (!presentation) return <>{text}</>
  return <span className="math-readout" role="math" aria-label={text}>
    <span aria-hidden="true">{presentation.kind === 'fraction'
      ? <span className="math-fraction"><span>{presentation.numerator}</span><span>{presentation.denominator}</span></span>
      : presentation.parts.map((part, index) => part.kind === 'text' ? <span key={index}>{part.text}</span> : <sup key={index}>{part.text}</sup>)}</span>
  </span>
}
