import { CalendarDays, GitBranch, ListChecks } from 'lucide-react'
import { dayLabel } from '../learning'

export interface SemesterNodeFact {
  chapter_id: string; chapter_name: string; sections: string[]
  introduced_at: string; first_record_at: string; last_record_at: string
  possible_error_patterns: string[]; boundary_rules: string[]
  independent_count: number; correction_count: number; error_count: number
  item_families_count: number; reviewed_question_count: number
}

/** Rich curriculum and student-history facts for the full semester showcase. */
export function SemesterNodeFacts({ facts }: { facts: SemesterNodeFact }) {
  return <section className="semester-node-facts" aria-label="知识点详细档案">
    <div className="semester-fact-metrics">
      <div><strong>{facts.independent_count}</strong><span>独立作答</span></div>
      <div><strong>{facts.error_count}</strong><span>原错误</span></div>
      <div><strong>{facts.correction_count}</strong><span>订正</span></div>
      <div><strong>{facts.item_families_count}</strong><span>题族</span></div>
    </div>
    <div className="semester-fact-dates"><span><CalendarDays size={14} />首次学习 {dayLabel(facts.introduced_at)}</span><span>最近记录 {dayLabel(facts.last_record_at)}</span></div>
    <div className="semester-fact-section"><GitBranch size={14} /><div><strong>教材位置</strong><p>{facts.chapter_name} · {facts.sections.join('、')}</p><small>{facts.reviewed_question_count} 道项目复核题用于档案构建</small></div></div>
    <details className="semester-fact-details" open>
      <summary><ListChecks size={15} />常见错误与判读边界</summary>
      <div><strong>这个知识点容易卡在哪里</strong><ul>{facts.possible_error_patterns.map(item => <li key={item}>{item}</li>)}</ul>
        {facts.error_count === 0 && <p className="semester-no-error">当前没有错误记录；这不等于以后不需要复习。</p>}
        <strong>判断时要注意</strong>{facts.boundary_rules.length ? <ul>{facts.boundary_rules.map(item => <li key={item}>{item}</li>)}</ul> : <p className="semester-no-error">这个节点没有额外边界说明，仍须按原题和实际作答判断。</p>}
      </div>
    </details>
  </section>
}
