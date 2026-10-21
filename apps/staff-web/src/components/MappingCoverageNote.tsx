import type { MappingCoverage } from '../api/learningTypes'

const stateText = { no_direct_match: '暂未找到直接对应的教材知识点', mapping_pending: '知识点归类尚未完成', not_linked: '尚未形成有效的知识点关联' }
export default function MappingCoverageNote({ coverage, onOpenCase, snapshot = false }: { coverage?: MappingCoverage; onOpenCase?: (id: string) => void; snapshot?: boolean }) {
  if (!coverage?.unlinked) return null
  return <section className="mapping-gap-note" aria-label="尚未关联的错题">
    <h3>还有 {coverage.unlinked} 道错题尚未关联知识点</h3>
    <p>{snapshot ? '生成这份报告时，本期' : '当前教材范围内，'}当前作答仍判错或部分正确的 {coverage.total} 道题中，{coverage.linked} 道已关联，{coverage.unlinked} 道尚未关联。</p>
    <p className="subtle">AI 初判 {coverage.model_count} 道 · 老师核实 {coverage.staff_count} 道。未关联题目仍保留在原作业中，不计为新的薄弱点。</p>
    <details open={snapshot || undefined}><summary>{snapshot ? "本期当前尚未关联的错题" : "查看原题与归类状态"}</summary><ul>{coverage.items.map(item => <li key={item.case_id}><div><strong>{item.subject}{item.label ? ` · ${item.label}` : ''}</strong><p>{item.stem_preview}</p>
      <small>{item.source_role === 'model' ? 'AI 初判，尚未经老师核实' : '老师已核实'}{!snapshot && ` · ${stateText[item.state ?? 'not_linked']}`}</small></div>
      {onOpenCase && <button className="secondary" onClick={() => onOpenCase(item.case_id)}>查看原题</button>}</li>)}</ul>
    {coverage.truncated && <p className="subtle">这里只列最近 30 道，总数包含全部符合范围的题目。</p>}
    <p className="subtle">{coverage.notice}{snapshot && '这是报告生成时的记录，后续订正请以新版本为准。'}</p></details>
  </section>
}
