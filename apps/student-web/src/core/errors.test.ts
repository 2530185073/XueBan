import { describe, expect, it, vi } from 'vitest'
import { StudentApi } from './api'
import { safeError, StudentError } from './errors'

interface UploadErrorFixture {
  code: 'unsafe_asset_url' | 'unsafe_upload_header'
  url: string
  headers: Record<string, string>
  message: string
}

describe('upload configuration error messages', () => {
  const fixtures: UploadErrorFixture[] = [
    {
      code: 'unsafe_asset_url',
      url: 'http://127.0.0.1:8000/v1/assets/asset_one/content?ticket=synthetic-sensitive-value',
      headers: { 'Content-Type': 'image/jpeg' },
      message: '照片上传地址与当前页面不匹配或不符合安全要求，已停止上传。请联系管理员检查上传服务地址后重试。',
    },
    {
      code: 'unsafe_upload_header',
      url: 'http://127.0.0.1:5174/v1/assets/asset_one/content',
      headers: { Authorization: 'synthetic-sensitive-value' },
      message: '照片上传请求头不符合安全要求，已停止上传。请联系管理员检查上传服务配置后重试。',
    },
  ]

  it.each(fixtures)('explains $code without exposing upload details or sending bytes', async ({ code, url, headers, message }) => {
    const transport = vi.fn<typeof fetch>()
    const api = new StudentApi('http://127.0.0.1:5174', () => null, () => undefined, transport)
    const error: unknown = await api.upload(url, headers, new Blob(['synthetic-photo'], { type: 'image/jpeg' })).catch(error => error)

    expect(error).toBeInstanceOf(StudentError)
    expect(error).toMatchObject({ code, status: 502, message })
    expect(safeError(error)).toEqual({ code, message })
    expect(JSON.stringify(safeError(error))).not.toContain('synthetic-sensitive-value')
    expect(JSON.stringify(safeError(error))).not.toContain('http://')
    expect(transport).not.toHaveBeenCalled()
  })

  it('keeps the contract error message for an invalid successful JSON response', async () => {
    const transport = vi.fn(async () => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const api = new StudentApi('http://127.0.0.1:5174', () => 'synthetic-token', () => undefined, transport)
    const error: unknown = await api.request('/v1/homeworks/homework_one', 'HomeworkSession').catch(error => error)

    expect(error).toMatchObject({ code: 'invalid_response', status: 502 })
    expect(safeError(error)).toEqual({ code: 'invalid_response', message: '服务返回格式不符合契约，已停止处理。' })
  })
})
