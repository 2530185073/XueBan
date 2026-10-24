import fs from 'node:fs'
import crypto from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '../../..')
const source = path.join(root, 'services/backend/xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-r3-20261021.json')
const output = path.join(root, 'apps/student-web/src/core/showcaseSemester.json')
const raw = fs.readFileSync(source, 'utf8')
const book = JSON.parse(raw)
const skills = book.proposed_pilot_scope.knowledge_nodes
const chapters = book.official_table_of_contents.chapters
const relations = book.proposed_pilot_scope.prerequisite_edges
const cases = [...book.review_cases, ...book.validation_cases]
const caseFor = id => cases.find(item => (item.proposed_node_ids || item.node_ids || []).includes(id))
const extraCasesFor = id => cases.filter(item => (item.proposed_node_ids || item.node_ids || []).includes(id))
const chapterFor = skill => chapters.find(chapter => skill.official_section_ids.some(id => chapter.sections.some(section => section.id === id)))
const chapterShapes = [
  ['stable','stable','stable','stable','stable','stable','consolidating'],
  ['stable','stable','consolidating','stable','needs_help','stable','stable','consolidating','stable','stable','consolidating','needs_help','stable'],
  ['stable','consolidating','stable','stable','needs_help','consolidating','stable','no_judgment','stable'],
  ['stable','stable','consolidating','stable','consolidating','needs_help','stable','no_judgment'],
  ['stable','stable','consolidating','consolidating','needs_help','consolidating','stable','consolidating','stable','consolidating','needs_help','stable','consolidating','no_judgment','stable','needs_help','consolidating','stable','no_judgment'],
  ['stable','consolidating','stable','consolidating','consolidating','stable','consolidating','no_judgment','no_judgment','consolidating','stable','stable','needs_help','consolidating','no_judgment','stable','needs_help','no_judgment','stable'],
]
const chapterDates = [
  ['2025-09-01','2025-09-18'], ['2025-09-21','2025-10-30'], ['2025-10-31','2025-11-20'],
  ['2025-11-21','2025-12-11'], ['2025-12-12','2026-01-08'], ['2026-01-09','2026-01-22'],
]
const DAY = 86400000
const at = (date, days = 0, hours = 0) => {
  const value = new Date(`${date}T08:00:00+08:00`)
  return new Date(value.getTime() + days * DAY + hours * 3600000).toISOString()
}
const dateOnly = value => value.slice(0, 10)
const addDays = (date, days) => dateOnly(new Date(new Date(`${date}T00:00:00Z`).getTime() + days * DAY).toISOString())
const wrongAnswer = (answer, patterns) => {
  const trimmed = answer.trim()
  if (/^-\d+(?:\.\d+)?$/.test(trimmed)) return trimmed.slice(1)
  if (/^\d+(?:\.\d+)?$/.test(trimmed)) return String(Number(trimmed) + 1)
  if (trimmed.includes('；')) return `${trimmed.split('；')[0]}（其余未作答）`
  return `只写出部分过程：${patterns[0] || '关键条件或符号处理不完整'}`
}
const makeTransfer = base => ({
  ...base,
  id: `${base.id}-transfer`,
  prompt: `独立完成并写出关键依据：${base.prompt}`,
  reference_answer: `${base.reference_answer}（依据：${base.review_focus ?? base.expected_behavior ?? '按题目条件检查'}）`,
})
const makeEvidence = ({ skill, shape, base, extras, introduced, chapterName, chapterIndex, itemIndex }) => {
  const rows = []
  const add = ({ kind, days = 0, hours = 0, result, assistance, sourceCase, family }) => {
    const index = rows.length
    const attemptId = `semester-${skill.id}-attempt-${index + 1}`
    const previous = rows[index - 1]
    const occurredAt = at(introduced, days, hours)
    rows.push({
      id: `semester-${skill.id}-evidence-${index + 1}`,
      occurred_at: occurredAt,
      question_text: sourceCase.prompt,
      answer_text: result === 'correct' ? sourceCase.reference_answer : wrongAnswer(sourceCase.reference_answer, skill.possible_error_patterns),
      reference_answer: sourceCase.reference_answer,
      homework_title: kind === 'correction' ? `${chapterName} · 原题订正` : index === 0 ? `${chapterName} · 新知学校作业` : `${chapterName} · 单元变式与复习`,
      result,
      assistance_level: assistance,
      event_type: kind,
      source_role: 'sitting_teacher',
      case_id: `semester-${skill.id}-case-${family}`,
      question_id: `semester-${skill.id}-question-${family}`,
      homework_id: kind === 'correction' ? `semester-${skill.id}-homework-1` : `semester-${skill.id}-homework-${family}`,
      attempt_id: attemptId,
      parent_attempt_id: kind === 'correction' ? previous?.attempt_id ?? null : null,
      decision_id: `semester-${skill.id}-decision-${index + 1}`,
      item_family_id: `family-${skill.id}-${family}`,
      independent: kind === 'school_first_attempt' && assistance === 'independent',
      chapter_index: chapterIndex,
      item_index: itemIndex,
    })
  }
  if (shape === 'no_judgment') {
    add({ kind: 'school_first_attempt', result: 'correct', assistance: 'staff_help', sourceCase: base, family: 1 })
    return rows
  }
  if (shape === 'stable') {
    add({ kind: 'school_first_attempt', result: 'correct', assistance: 'independent', sourceCase: base, family: 1 })
    add({ kind: 'school_first_attempt', days: 4, result: 'correct', assistance: 'independent', sourceCase: extras[0] ?? makeTransfer(base), family: 2 })
    add({ kind: 'school_first_attempt', days: 9, result: 'correct', assistance: 'independent', sourceCase: extras[1] ?? makeTransfer(extras[0] ?? base), family: 3 })
    return rows
  }
  add({ kind: 'school_first_attempt', result: 'incorrect', assistance: 'no_recorded_help', sourceCase: base, family: 1 })
  add({ kind: 'correction', hours: 2, result: 'correct', assistance: 'direction', sourceCase: base, family: 1 })
  if (shape === 'consolidating') add({ kind: 'school_first_attempt', days: 7, result: 'correct', assistance: 'independent', sourceCase: extras[0] ?? makeTransfer(base), family: 2 })
  if (shape === 'needs_help') add({ kind: 'school_first_attempt', days: 6, result: 'incorrect', assistance: 'no_recorded_help', sourceCase: extras[0] ?? makeTransfer(base), family: 2 })
  return rows
}
const computeState = (knowledgeId, evidence) => {
  const rows = [...evidence].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
  const errors = rows.filter(row => row.result !== 'unscorable' && ['incorrect', 'partial'].includes(row.result))
  const lastError = errors.at(-1)?.occurred_at ?? null
  const independent = rows.filter(row => row.independent && row.result !== 'unscorable')
  const wins = independent.filter(row => row.result === 'correct' && (!lastError || row.occurred_at > lastError))
  const dates = new Set(wins.map(row => row.occurred_at.slice(0, 10)))
  const families = new Set(wins.map(row => row.item_family_id))
  const stable = dates.size >= 2 && families.size >= 2 && wins.length > 1 && new Date(wins.at(-1).occurred_at) - new Date(wins[0].occurred_at) >= DAY
  const assistedFix = rows.some(row => row.result === 'correct' && !row.independent && lastError && row.occurred_at > lastError)
  let learning_status = 'no_judgment', reason_codes = ['no_scorable_evidence']
  if (stable) { learning_status = 'stable_for_now'; reason_codes = [] }
  else if (wins.length) { learning_status = 'consolidating'; reason_codes = ['independent_success_awaiting_more_dates'] }
  else if (errors.length && assistedFix) { learning_status = 'consolidating'; reason_codes = ['corrected_with_help_awaiting_new_school_item'] }
  else if (errors.length) { learning_status = 'needs_help_now'; reason_codes = [new Set(errors.map(row => row.attempt_id)).size >= 2 ? 'repeated_errors' : 'single_error_needs_diagnosis'] }
  else if (rows.length) reason_codes = ['assisted_or_unverified_only']
  const independentDates = new Set(independent.map(row => row.occurred_at.slice(0, 10)))
  return {
    knowledge_id: knowledgeId, learning_status, reason_codes, assessment_source: 'staff', model_evidence_count: 0,
    staff_verified_evidence_count: rows.length, valid_evidence_count: rows.length,
    evidence_sufficiency: stable ? 'substantial' : rows.length ? 'limited' : 'insufficient',
    sufficiency_explanation: independent.length ? `来自 ${independentDates.size} 个日期、${new Set(independent.map(row => row.item_family_id)).size} 道题的独立作答` : '只有作业记录，独立完成情况未核实',
  }
}

const nodes = [], details = {}, facts = {}, allEvidence = []
skills.forEach(skill => {
  const chapter = chapterFor(skill)
  if (!chapter) throw new Error(`No chapter for ${skill.id}`)
  const chapterIndex = chapter.number - 1
  const chapterSkills = skills.filter(item => chapterFor(item)?.id === chapter.id)
  const itemIndex = chapterSkills.findIndex(item => item.id === skill.id)
  const shape = chapterShapes[chapterIndex][itemIndex]
  if (!shape) throw new Error(`No story shape for ${skill.id}`)
  const base = caseFor(skill.id)
  if (!base) throw new Error(`No reviewed question for ${skill.id}`)
  const extras = extraCasesFor(skill.id).filter(item => item.id !== base.id)
  const [start, end] = chapterDates[chapterIndex]
  const duration = Math.round((new Date(end) - new Date(start)) / DAY)
  const introduced = addDays(start, chapterSkills.length === 1 ? 0 : Math.floor(itemIndex * Math.max(1, duration - 10) / (chapterSkills.length - 1)))
  const evidence = makeEvidence({ skill, shape, base, extras, introduced, chapterName: chapter.title, chapterIndex, itemIndex })
  const state = computeState(skill.id, evidence)
  const expectedStatus = { stable: 'stable_for_now', consolidating: 'consolidating', needs_help: 'needs_help_now', no_judgment: 'no_judgment' }[shape]
  if (state.learning_status !== expectedStatus) throw new Error(`State mismatch for ${skill.id}: ${shape} -> ${state.learning_status}`)
  const sort_order = chapter.number * 1000 + itemIndex * 10
  nodes.push({
    id: skill.id, node_type: 'skill', parent_id: chapter.id, name: skill.name, definition: null,
    observable_objective: skill.observable_objective, sort_order, subject: '数学', chapter_id: chapter.id, chapter_name: chapter.title,
    learning_status: state.learning_status, reason_codes: state.reason_codes, valid_evidence_count: evidence.length,
    model_evidence_count: 0, staff_verified_evidence_count: evidence.length, assessment_source: 'staff',
  })
  details[skill.id] = {
    node: { id: skill.id, name: skill.name, definition: null, observable_objective: skill.observable_objective },
    state, evidence, prerequisites: [], leads_to: [], projection: { pending: false, projected_at: '2026-01-29T16:00:00+08:00', evidence_revision: 1, state_version: 1 },
    graph_version: 'pep-math-g7-up-9787107382468-r3-semester-demo',
    notice: '前置关系来自项目复核的知识结构，不传播掌握或薄弱。学生与作业为基于真实教材构建的演示档案。',
  }
  facts[skill.id] = {
    chapter_id: chapter.id, chapter_name: chapter.title,
    sections: chapter.sections.filter(section => skill.official_section_ids.includes(section.id)).map(section => `${section.number} ${section.title}`),
    introduced_at: at(introduced), first_record_at: evidence[0].occurred_at, last_record_at: evidence.at(-1).occurred_at,
    possible_error_patterns: skill.possible_error_patterns, boundary_rules: skill.boundary_rules ?? [],
    independent_count: evidence.filter(row => row.independent).length,
    correction_count: evidence.filter(row => row.event_type === 'correction').length,
    error_count: evidence.filter(row => ['incorrect', 'partial'].includes(row.result)).length,
    item_families_count: new Set(evidence.map(row => row.item_family_id)).size,
    reviewed_question_count: extraCasesFor(skill.id).length,
  }
  allEvidence.push(...evidence)
})
const edges = relations.filter(edge => edge.relation_type === 'prerequisite_of').map(edge => ({ id: edge.id, from_id: edge.from_id, to_id: edge.to_id, relation_type: edge.relation_type, scope: null, rationale: edge.rationale }))
for (const edge of edges) {
  const source = nodes.find(node => node.id === edge.from_id), target = nodes.find(node => node.id === edge.to_id)
  if (!source || !target) throw new Error(`Unknown edge endpoint ${edge.id}`)
  details[edge.to_id].prerequisites.push({ id: source.id, name: source.name, learning_status: source.learning_status })
  details[edge.from_id].leads_to.push({ id: target.id, name: target.name, learning_status: target.learning_status })
}
const statusCounts = Object.fromEntries(['stable_for_now','consolidating','needs_help_now','no_judgment'].map(status => [status, nodes.filter(node => node.learning_status === status).length]))
const chapterRollups = chapters.map(chapter => ({
  chapter_id: chapter.id, name: chapter.title, objectives: nodes.filter(node => node.chapter_id === chapter.id).length,
  status_counts: Object.fromEntries(['stable_for_now','consolidating','needs_help_now','no_judgment'].map(status => [status, nodes.filter(node => node.chapter_id === chapter.id && node.learning_status === status).length])),
}))
const map = {
  student_id: 'student_pep_g7_semester_demo', grade: 7, graph_version: 'pep-math-g7-up-9787107382468-r3-semester-demo',
  graph_review_status: 'source_verified_ai_reviewed',
  book_identity: {
    graph_version: 'pep-math-g7-up-9787107382468-r3-semester-demo', title: '义务教育教科书·数学七年级上册', subject: '数学', grade: 7,
    publisher_code: 'PEP', publisher_name: '人民教育出版社', isbn: book.book_identity.isbn, term: 'up',
    review_status: 'source_verified_ai_reviewed', review_label: '官方书目与目录已核对，项目知识结构和题目经独立复核',
    source_note: '教材身份来自国家平台公开记录；75 个技能和关系为项目整理，不是出版社官方逐节点图谱。',
  },
  projection: { pending: false, projected_at: '2026-01-29T16:00:00+08:00', evidence_revision: 1, state_version: 1 },
  nodes, edges,
  states: nodes.map(node => ({ ...details[node.id].state, name: node.name, chapter_id: node.chapter_id, chapter_name: node.chapter_name, sort_order: node.sort_order })),
  chapters: chapterRollups,
  source_note: '人教版七年级上册，ISBN 9787107382468；项目 R3 知识结构。学生及作业为一学期演示档案，不是实际学生数据。',
  notice: '真实教材书目与项目复核知识结构为依据；学生、作业和教师核实记录均为完整演示数据，不用于真实学生评价。',
}
const correctionLinks = allEvidence.filter(row => row.event_type === 'correction')
for (const row of correctionLinks) {
  const siblings = details[Object.keys(details).find(id => row.id.startsWith(`semester-${id}-evidence-`))].evidence
  const parent = siblings.find(item => item.attempt_id === row.parent_attempt_id)
  if (!parent || parent.result !== 'incorrect' || parent.occurred_at >= row.occurred_at) throw new Error(`Invalid correction chain ${row.id}`)
}
if (nodes.length !== 75 || chapters.length !== 6 || relations.length !== 71 || edges.length !== 54) throw new Error('Curriculum totals changed')
if (allEvidence.some(row => row.occurred_at < at('2025-09-01') || row.occurred_at > at('2026-01-29', 23))) throw new Error('Evidence outside semester')
const data = {
  meta: {
    title: '人教版七年级上册数学 · 一学期学习档案', period: '2025 学年第一学期', dateRange: '2025.09.01—2026.01.29',
    studentLabel: '七年级学生 · 完整学期演示档案', source: '人民教育出版社 · ISBN 9787107382468',
    sourceSha256: crypto.createHash('sha256').update(raw).digest('hex'), skillCount: 75, chapterCount: 6,
    relationCount: 71, prerequisiteCount: 54, evidenceCount: allEvidence.length, correctionCount: correctionLinks.length,
    statusCounts, generatedBy: 'apps/student-web/scripts/generate-showcase-semester.mjs',
    honesty: '真实教材与项目复核知识结构为依据；学生及作业为虚构演示，不冒充真实学生记录。',
  }, map, details, facts,
}
fs.writeFileSync(output, `${JSON.stringify(data, null, 2)}\n`)
console.log(JSON.stringify({ output, skills: nodes.length, relations: relations.length, evidence: allEvidence.length, corrections: correctionLinks.length, statusCounts }, null, 2))
