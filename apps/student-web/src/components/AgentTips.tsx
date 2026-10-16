import { useEffect, useState } from 'react'
import { HeartHandshake } from 'lucide-react'
import type { AgentContext } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { visibleTips } from '../learning'

export function AgentTips({ context }: { context: AgentContext | null }) {
  const tips = visibleTips(context)
  if (!context || !tips.length) return null
  return <aside className="agent-tips" aria-labelledby={`${context.case_id}-remember`}>
    <div className="section-label"><HeartHandshake size={18} /><h3 id={`${context.case_id}-remember`}>学伴记得</h3></div>
    <ul>{tips.map(tip => <li key={tip.memory_id}>{tip.display}</li>)}</ul>
    <p className="muted small">{context.notice}</p>
  </aside>
}

/** Read-only reminders for one question. Any failure just hides the panel; the correction flow never waits on it. */
export function AgentTipsPanel({ caseId, workspace }: { caseId: string; workspace: Workspace }) {
  const [context, setContext] = useState<AgentContext | null>(null)
  const { client } = workspace
  useEffect(() => {
    let live = true; setContext(null)
    client.agentContext(caseId).then(value => { if (live) setContext(value) }, (e: unknown) => { if (live) workspace.report(e) })
    return () => { live = false }
  }, [caseId, client])
  return <AgentTips context={context?.case_id === caseId ? context : null} />
}
