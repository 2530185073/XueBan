/** Kept separate from sample data so production builds can omit the entire demo. */
export function isKnowledgeDemoEnabled({ development, hostname, requestedStudent, currentStudent, grade }: {
  development: boolean
  hostname: string
  requestedStudent: string | null | undefined
  currentStudent: string | null | undefined
  grade: number | null | undefined
}): boolean {
  if (!development || grade !== 7 || !requestedStudent?.trim() || requestedStudent !== currentStudent) return false
  return ['localhost', '127.0.0.1', '::1', '[::1]'].includes(hostname)
}
