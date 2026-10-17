/** Subjects and publishers chosen per grade; mirrors curriculum/selection.json (checked by test). */
export const TEXTBOOK_PLAN: Record<number, { label: string; subjects: [subject: string, publisher: string][] }> = {
  5: { label: '五年级', subjects: [['语文', '人民教育出版社'], ['数学', '人民教育出版社'], ['英语', '陕西旅游出版社'], ['科学', '教育科学出版社']] },
  6: { label: '六年级', subjects: [['语文', '人民教育出版社'], ['数学', '人民教育出版社'], ['英语', '陕西旅游出版社'], ['科学', '教育科学出版社']] },
  7: { label: '初一', subjects: [['语文', '人民教育出版社'], ['数学', '北京师范大学出版社'], ['英语', '人民教育出版社'], ['道德与法治', '人民教育出版社'], ['历史', '人民教育出版社'], ['地理', '人民教育出版社'], ['生物学', '江苏凤凰科学技术出版社']] },
  8: { label: '初二', subjects: [['语文', '人民教育出版社'], ['数学', '北京师范大学出版社'], ['英语', '人民教育出版社'], ['物理', '江苏凤凰科学技术出版社'], ['道德与法治', '人民教育出版社'], ['历史', '人民教育出版社'], ['地理', '人民教育出版社'], ['生物学', '江苏凤凰科学技术出版社']] },
}

export const PROFILE_GRADES: [grade: number, label: string][] = [[5, '五年级'], [6, '六年级'], [7, '七年级'], [8, '八年级'], [9, '九年级']]

export const TEXTBOOK_REVISIONS = ['2024 新版', '旧版']

export function textbookPlanText(grade: number | null): string {
  if (grade === null) return ''
  const plan = TEXTBOOK_PLAN[grade]
  if (!plan) return '本年级尚未配置教材选用，请在备注写明实际使用的教材。'
  return plan.subjects.map(([subject, publisher]) => `${subject}·${publisher.replace(/出版社$/, '')}`).join('　')
}
