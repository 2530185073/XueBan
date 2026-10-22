import { useEffect, useState } from 'react'
import type { Workspace } from '../useStudentWorkspace'
import type { RecognitionProgress } from '../core/recognitionProgress'

export function RecognitionDraftCard({ value }: { value: RecognitionProgress | null }) {
  if (!value?.pages.length || value.state === 'final_available') return null
  return <section className="recognition-recovery" aria-label="AI 识读草稿">
    <h3>AI 识读草稿 · 核对仍在进行</h3>
    <p role="status">{value.state === 'checking' ? '识读服务已返回，正在核对内容。' : '部分识读已保存，正在等待其余核对。'}暂不判断对错，文字可能与原图不一致。</p>
    <p className="muted small">可先对照原图查看；不必重新提交，也不用选择学科。</p>
    {value.pages.map(page => <details key={page.asset_id}><summary>查看这一页的识读草稿（{page.draft.length} 项，非题目总数）</summary>
      {page.draft.map((row, i) => <article key={i} className="hint-card"><strong>{row.label}</strong><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.stem_text ?? '题干文字暂未读清'}</p><span className="muted small">AI 转写，尚未核实</span><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.answer_text || '暂未取得作答文字'}</p></article>)}
    </details>)}
  </section>
}

export function RecognitionProgressPanel({ workspace, jobId, homeworkId, assetIds }: { workspace: Workspace; jobId: string; homeworkId: string; assetIds: string[] }) {
  const [value, setValue] = useState<RecognitionProgress | null>(null)
  const scope = workspace.client.historyScope, assets = JSON.stringify(assetIds)
  useEffect(() => {
    setValue(null)
    if (!workspace.online) return
    let disposed = false, rounds = 0, timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const next = await workspace.client.recognitionProgress(jobId, homeworkId, JSON.parse(assets) as string[])
        if (disposed || scope !== workspace.client.historyScope) return
        setValue(next)
        if (next.state === 'final_available') return
      } catch { if (!disposed) setValue(null) }
      if (!disposed && ++rounds < 60) timer = setTimeout(() => { void load() }, 5000)
    }
    void load()
    return () => { disposed = true; if (timer) clearTimeout(timer) }
  }, [workspace.client, workspace.online, jobId, homeworkId, assets, scope])
  return <RecognitionDraftCard value={value} />
}
