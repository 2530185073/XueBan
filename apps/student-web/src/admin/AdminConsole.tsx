import { useState } from 'react'
import { StaffAccounts } from './StaffAccounts'
import { ServiceMetrics } from './ServiceMetrics'
import { StudentAccounts } from './StudentAccounts'
import type { AdminClient } from './types'
import './admin.css'

const TABS = { students: '学员账号', staff: '老师账号', metrics: '运行指标' } as const
type Tab = keyof typeof TABS

/** Each tab mounts fresh, so a pending password or retry key never survives a tab switch. */
export function AdminConsole({ client }: { client: AdminClient }) {
  const [tab, setTab] = useState<Tab>('students')
  return <div className="admin-console">
    <nav className="admin-tabs" role="tablist" aria-label="管理功能">
      {(Object.keys(TABS) as Tab[]).map(key => <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{TABS[key]}</button>)}
    </nav>
    {tab === 'students' ? <StudentAccounts client={client} /> : tab === 'staff' ? <StaffAccounts client={client} /> : <ServiceMetrics client={client} />}
  </div>
}
