import type { CurriculumBookIdentity, CurriculumBooks, KnowledgeDetail, KnowledgeGraphEdge, KnowledgeGraphNode, KnowledgeMap } from './learning'

// Synthetic learning records for UI validation. Never loaded by the application.
const base: KnowledgeGraphNode = {
  id: 'kp-rational', node_type: 'concept', parent_id: 'ch-rational', name: '认识有理数',
  definition: null, observable_objective: '能结合生活中的例子区分正数、负数与零。', sort_order: 10,
  subject: '数学', chapter_id: 'ch-rational', chapter_name: '有理数', learning_status: 'stable_for_now',
  reason_codes: [], valid_evidence_count: 3, model_evidence_count: 0, staff_verified_evidence_count: 3, assessment_source: 'staff',
}
export const graphNodes: KnowledgeGraphNode[] = [
  base,
  { ...base, id: 'kp-number-line', name: '数轴上的方向与距离', sort_order: 20, learning_status: 'consolidating', valid_evidence_count: 2, model_evidence_count: 1, staff_verified_evidence_count: 1, assessment_source: 'mixed' },
  { ...base, id: 'kp-opposite', name: '相反数与绝对值', sort_order: 30, learning_status: 'no_judgment', valid_evidence_count: 0, model_evidence_count: 0, staff_verified_evidence_count: 0, assessment_source: 'none' },
  { ...base, id: 'kp-add', name: '有理数加减', node_type: 'skill', sort_order: 40, learning_status: 'needs_help_now', reason_codes: ['single_error_needs_diagnosis'], valid_evidence_count: 1, model_evidence_count: 1, staff_verified_evidence_count: 0, assessment_source: 'model' },
  { ...base, id: 'kp-multiply', name: '有理数乘除', node_type: 'skill', sort_order: 50, learning_status: 'no_judgment', valid_evidence_count: 0, model_evidence_count: 0, staff_verified_evidence_count: 0, assessment_source: 'none' },
  { ...base, id: 'kp-expression', name: '用字母表示数量关系', parent_id: 'ch-algebra', chapter_id: 'ch-algebra', chapter_name: '代数式', sort_order: 60, learning_status: 'no_judgment', valid_evidence_count: 0, model_evidence_count: 0, staff_verified_evidence_count: 0, assessment_source: 'none' },
  { ...base, id: 'kp-value', name: '求代数式的值', parent_id: 'ch-algebra', chapter_id: 'ch-algebra', chapter_name: '代数式', sort_order: 70, learning_status: 'consolidating', valid_evidence_count: 2, model_evidence_count: 2, staff_verified_evidence_count: 0, assessment_source: 'model' },
]
export const graphEdges: KnowledgeGraphEdge[] = [
  ['kp-rational', 'kp-number-line'], ['kp-number-line', 'kp-opposite'], ['kp-opposite', 'kp-add'], ['kp-add', 'kp-multiply'], ['kp-expression', 'kp-value'],
].map(([from_id, to_id], index) => ({ id: `edge-${index}`, from_id, to_id, relation_type: 'prerequisite_of', scope: null, rationale: '合成的前置知识关系' }))
export const graphMap: KnowledgeMap = {
  student_id: 'student_one', grade: 7, graph_version: 'synthetic-reviewed-v1', graph_review_status: 'publisher_latest_reviewed',
  projection: { pending: false, projected_at: '2026-10-21T08:00:00Z', evidence_revision: 3, state_version: 3 },
  nodes: graphNodes, edges: graphEdges, states: [], chapters: [],
  notice: '学习状态来自有效作答记录；AI 初判和老师核实会分别标注。没有记录，不代表不会。',
}
export const graphDetail: KnowledgeDetail = {
  node: { id: 'kp-add', name: '有理数加减', definition: null, observable_objective: '能判断符号，再按法则完成有理数的加减。' },
  state: { learning_status: 'needs_help_now', reason_codes: ['single_error_needs_diagnosis'], assessment_source: 'model', model_evidence_count: 1, staff_verified_evidence_count: 0 },
  evidence: [{ id: 'ev-ai', occurred_at: '2026-10-21T08:00:00Z', question_text: '计算：(-3) + 5 = ?', homework_title: '数学 · 今天的学校作业', result: 'incorrect', assistance_level: 'unknown', event_type: 'school_first_attempt', source_role: 'model', case_id: 'case-synthetic', homework_id: 'homework-synthetic' }],
  prerequisites: [{ id: 'kp-opposite', name: '相反数与绝对值', learning_status: 'no_judgment' }],
  graph_version: graphMap.graph_version, projection: graphMap.projection,
  notice: '前置关系是学习上的关联，不会自动成为薄弱点。',
}

export const syntheticBook: CurriculumBookIdentity = {
  graph_version: 'synthetic-pep-2026', title: '数学 七年级上册', subject: '数学', grade: 7, publisher_code: 'PEP', publisher_name: '人民教育出版社',
  isbn: '9787107382468', term: 'up', review_status: 'source_verified_ai_reviewed', review_label: '目录来源已核对，知识点关系由 AI 整理',
  source_note: '合成验证书目：学校教材目录与知识点关系分别标注来源，未作老师逐点确认。',
}
export const syntheticBooks: CurriculumBooks = { student_id: 'student_one', profile_revision: 2, current_binding: null, books: [syntheticBook] }
