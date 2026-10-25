import type { CurriculumBooks, StudentCurriculum } from './learning'
import { StudentError } from './errors'

export function homeworkSetupIssue(curriculum: StudentCurriculum | null | undefined, books: CurriculumBooks | null | undefined): string | null {
  if (!curriculum || !books) return '正在确认年级和教材，请稍等后再开始检查。'
  if (curriculum.student_id !== books.student_id) return '课程设置已变化，请刷新后再开始检查。'
  if (!curriculum.grade) return '先选择你现在的年级，再开始检查作业。'
  if (!curriculum.subjects.some(subject => subject.catalog_status === 'available')) return '当前年级的教材范围尚未就绪，请老师确认课程设置后再检查。'
  if (books.current_binding && !books.books.some(book => book.graph_version === books.current_binding?.graph_version)) return '当前教材暂不可用，请重新选择已就绪的教材。'
  if (books.books.length && !books.current_binding) return '先确认学校使用的教材，再开始检查作业。'
  return null
}

interface SetupClient {
  readonly selectedStudentId: string | null
  studentCurriculum(): Promise<StudentCurriculum>
  curriculumBooks(): Promise<CurriculumBooks>
}

/** Re-read immediately before the first upload; cached configuration is only a UI hint. */
export async function checkHomeworkSetup(client: SetupClient) {
  const student = client.selectedStudentId
  const [curriculum, books] = await Promise.all([client.studentCurriculum(), client.curriculumBooks()])
  if (!student || client.selectedStudentId !== student || curriculum.student_id !== student || books.student_id !== student) throw new StudentError('session_changed', 401)
  const issue = homeworkSetupIssue(curriculum, books)
  if (issue) throw new StudentError('learning_setup_required', 409, issue)
  return { curriculum, books }
}
