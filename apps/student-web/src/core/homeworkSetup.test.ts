import { describe, expect, it, vi } from 'vitest'
import { checkHomeworkSetup, homeworkSetupIssue } from './homeworkSetup'
import type { CurriculumBooks, StudentCurriculum } from './learning'
const curriculum = { student_id: 'student_one', grade: 7, stage: 'junior_middle', subjects: [{ catalog_status: 'available' }], notice: '' } as StudentCurriculum
const book = { graph_version: 'reviewed-math', grade: 7 } as CurriculumBooks['books'][number]
const books: CurriculumBooks = { student_id: 'student_one', profile_revision: 2, current_binding: book, books: [book] }
describe('homework setup before upload', () => {
  it('requires grade, usable scope, and explicit selection when a reviewed book is offered', () => {
    expect(homeworkSetupIssue({ ...curriculum, grade: null }, books)).toContain('年级')
    expect(homeworkSetupIssue({ ...curriculum, subjects: [] }, books)).toContain('尚未就绪')
    expect(homeworkSetupIssue(curriculum, { ...books, current_binding: null })).toContain('学校使用的教材')
    expect(homeworkSetupIssue(curriculum, { ...books, books: [] })).toContain('暂不可用')
    expect(homeworkSetupIssue(curriculum, books)).toBeNull()
  })
  it('allows a verified publisher range when that grade has no separately selectable book', () => {
    expect(homeworkSetupIssue({ ...curriculum, grade: 6 }, { ...books, current_binding: null, books: [] })).toBeNull()
  })
  it('rejects an account transition before returning permission to upload', async () => {
    const client = { selectedStudentId: 'student_one', studentCurriculum: vi.fn(async () => curriculum), curriculumBooks: vi.fn(async () => { client.selectedStudentId = 'student_two'; return books }) }
    await expect(checkHomeworkSetup(client)).rejects.toMatchObject({ code: 'session_changed' })
  })
  it('reads live configuration instead of accepting a cached selection', async () => {
    const client = { selectedStudentId: 'student_one', studentCurriculum: vi.fn(async () => curriculum), curriculumBooks: vi.fn(async () => ({ ...books, current_binding: null })) }
    await expect(checkHomeworkSetup(client)).rejects.toMatchObject({ code: 'learning_setup_required' })
    expect(client.studentCurriculum).toHaveBeenCalledOnce(); expect(client.curriculumBooks).toHaveBeenCalledOnce()
  })
})
