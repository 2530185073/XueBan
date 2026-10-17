import { useState } from 'react'
import { Ban, CheckCheck } from 'lucide-react'
import type { ShareLink } from '../api/learningTypes'
import { dateTime, shareState } from '../learning'
import type { LearningContext } from './LearningCommon'

const shareLabels = { active: '有效', revoked: '已撤销', expired: '已过期' }

/** Every guardian link with what staff did with it; "确认已发送" records the teacher's word, not delivery. */
export function ShareLinkList({ ctx, items }: { ctx: LearningContext; items: ShareLink[] }) {
  const [confirming, setConfirming] = useState<string | null>(null)
  const [note, setNote] = useState('')
  if (items.length === 0) return <p className="subtle">还没有生成过链接。</p>
  const ordered = [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))

  async function confirm(link: ShareLink) {
    const done = await ctx.act(() => ctx.learning.confirmMessageSent(link.id, note.trim() || null), '已记录：老师确认已把消息发给家长。')
    if (done) { setConfirming(null); setNote('') }
  }

  return <ul className="hint-list">{ordered.map(link => {
    const state = shareState(link, Date.now())
    return <li key={link.id}>
      <div className="help-row wrap"><span className={`tag ${state === 'active' ? '' : 'muted'}`}>{shareLabels[state]}</span>
        <span className={`tag ${link.sent_confirmed_at ? '' : link.message_copied_at ? 'amber' : 'muted'}`}>{link.sent_confirmed_at ? '老师已确认发送' : link.message_copied_at ? '已复制，未确认发送' : '未复制'}</span>
        {link.view_count > 0 && <span className="tag">家长已打开</span>}
        <time className="subtle">创建于 {dateTime(link.created_at)}</time></div>
      <small>有效至 {dateTime(link.expires_at)} · 打开 {link.view_count} 次{link.last_viewed_at && ` · 最近 ${dateTime(link.last_viewed_at)}`}
        {link.sent_confirmed_at && ` · 确认发送于 ${dateTime(link.sent_confirmed_at)}`}{link.sent_note && `（${link.sent_note}）`}{link.revoked_at && ` · 撤销于 ${dateTime(link.revoked_at)}`}</small>
      {confirming === link.id ? <div className="tool-form">
        <label>发送方式（可选，最多 200 字）<input value={note} maxLength={200} disabled={ctx.busy} placeholder="例如：已发妈妈微信" onChange={event => setNote(event.target.value)} /></label>
        <div className="button-row"><button className="button primary" disabled={ctx.busy} onClick={() => void confirm(link)}><CheckCheck size={14} />确认已发送</button>
          <button className="text-link" onClick={() => { setConfirming(null); setNote('') }}>取消</button></div>
      </div> : <div className="button-row">
        {state === 'active' && !link.sent_confirmed_at && <button className="button" disabled={ctx.busy} onClick={() => setConfirming(link.id)}><CheckCheck size={14} />我已发给家长</button>}
        {state === 'active' && <button className="button" disabled={ctx.busy} onClick={() => void ctx.act(() => ctx.learning.revokeShareLink(link.id), '链接已撤销，家长将无法再打开。')}><Ban size={14} />撤销</button>}
      </div>}
    </li>
  })}</ul>
}
