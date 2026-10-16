import type { SafeError } from './types'
const messages: Record<number, string> = {
  0: '网络中断或等待超时。结果尚未确认，请重试原操作；不会重复创建请求。',
  401: '配对已失效，请请老师重新配对。', 403: '当前身份没有访问这份作业的权限。',
  404: '记录不存在或当前身份无权访问。', 409: '记录已更新，请刷新后再试。',
  422: '提交内容无法处理，请检查图片或请老师协助。', 502: '服务返回格式不符合契约，已停止处理。',
  503: '服务暂不可用，稍后可继续原操作。',
}
export class StudentError extends Error {
  constructor(public code: string, public status = 0, message = messages[status] ?? '本次操作未完成，请稍后重试。') { super(message); this.name = 'StudentError' }
}
export function safeError(error: unknown): SafeError {
  return error instanceof StudentError ? { code: error.code, message: error.message } : { code: 'local_failure', message: '本机处理未完成，请检查浏览器存储空间并重试。' }
}
