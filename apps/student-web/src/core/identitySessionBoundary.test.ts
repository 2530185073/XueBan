import { describe, it, expect, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentClient } from './student'
import { DraftStore } from './store'

class MemoryStorage implements Storage {
  values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(k: string) { return this.values.get(k) ?? null }
  setItem(k: string, v: string) { this.values.set(k, v) }
  removeItem(k: string) { this.values.delete(k) }
  key(i: number) { return [...this.values.keys()][i] ?? null }
}
function deferred<T>() { let resolve!: (v: T) => void; const promise = new Promise<T>(r => { resolve = r }); return { promise, resolve } }
const reply = (data: unknown, status=200) => new Response(JSON.stringify(data), { status })
const problem = (status: number) => reply({ type:'urn:xueban:auth',title:'Synthetic',status,category:status===401?'authorization':'validation',code:status===401?'authentication_required':'access_denied',detail:'Synthetic',request_id:'request_auth',current_revision:null,retry_after_seconds:null },status)

function setup(realStore?: DraftStore) {
  const calls: { path: string, token: string }[] = [], pending: ReturnType<typeof deferred<Response>>[] = [], business: ReturnType<typeof deferred<Response>>[] = []
  const storage = new MemoryStorage()
  let hold = false, holdBusiness = false, purgeBarrier: ReturnType<typeof deferred<void>> | null = null
  const store = realStore ?? {
    purgeExpired: vi.fn(async () => null),
    purgeScopes: vi.fn(async () => { if(purgeBarrier) await purgeBarrier.promise; return {drafts:0,journal:0} }),
    drafts: vi.fn(async () => []),
    takeNotice: vi.fn(async () => null),
  }
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input)).pathname
    const token = String((init.headers as Record<string,string>)?.Authorization ?? '').replace('Bearer ','')
    calls.push({path,token})
    if(path==='/v1/auth/login') { const user=JSON.parse(init.body as string).username; return reply({access_token:user,expires_at:'2099-01-01T00:00:00Z',must_change_password:false}) }
    if(path==='/v1/auth/account') return reply({username:token,must_change_password:false,student_id:'student_'+token})
    if(path==='/v1/auth/change-password') return reply({access_token:token,expires_at:'2099-01-01T00:00:00Z',must_change_password:false})
    if(path==='/v1/auth/logout') return reply({logged_out:true})
    if(path==='/v1/me') { if(hold) { const d=deferred<Response>();pending.push(d);return d.promise } return reply(identity(token)) }
    if(path.endsWith('/homeworks')) {if(holdBusiness) {const d=deferred<Response>();business.push(d);return d.promise} return reply({items:[],next_cursor:null})}
    throw new Error('Unexpected synthetic route '+path)
  })
  const client = new StudentClient({origin:'http://localhost',storage,store:store as any,transport:transport as typeof fetch})
  return {client,calls,store,storage,pending,business,hold:()=>{hold=true},unhold:()=>{hold=false},holdBusiness:()=>{holdBusiness=true},pausePurge:()=>{purgeBarrier=deferred<void>();return purgeBarrier}}
}
function identity(token:string) { return {actor_id:'actor_'+token,roles:['student'],authorized_students:[{student_id:'student_'+token,campus_id:'campus_one',display_name:'Synthetic'}]} }
const tick = async () => { for(let i=0;i<15;i++) await Promise.resolve() }
const scope = (user: string) => JSON.stringify(['http://localhost','actor_'+user,'student_'+user])
const draft = (user:string,id:string) => ({scope:scope(user),id,format:2,pages:[],title:'Synthetic',stage:'local',createdAt:'2026-10-21T00:00:00Z',updatedAt:'2026-10-21T00:00:00Z'}) as any

describe('independent late identity boundary',()=>{
  it('late old 401 cannot clear the new logged-in account',async()=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.hold()
    const old=s.client.history().catch(e=>e);await tick();expect(s.pending).toHaveLength(1)
    s.unhold();await s.client.login('user_b','synthetic password')
    s.pending[0].resolve(problem(401));await old
    expect(s.client.identity?.actor_id).toBe('actor_user_b')
    expect(s.client.selectedStudentId).toBe('student_user_b')
    expect(s.storage.getItem('xueban:http://localhost:session')).toContain('user_b')
  })
  it('old 403 purge finishing after new login cannot disconnect it',async()=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.hold()
    const barrier=s.pausePurge();const old=s.client.history().catch(e=>e);await tick()
    s.pending[0].resolve(problem(403));await vi.waitFor(()=>expect(s.store.purgeScopes).toHaveBeenCalledTimes(1))
    s.unhold();await s.client.login('user_b','synthetic password')
    barrier.resolve();await old
    expect(s.client.identity?.actor_id).toBe('actor_user_b')
    expect(s.client.selectedStudentId).toBe('student_user_b')
  })
  it('late old 401 after same-account relogin does not delete new drafts',async()=>{
    const store=new DraftStore(new IDBFactory()); const s=setup(store)
    await s.client.login('user_a','synthetic password');s.hold()
    const old=s.client.history().catch(e=>e);await tick();s.unhold()
    await s.client.login('user_a','synthetic password');await store.saveDraft(draft('user_a','fresh'))
    s.pending[0].resolve(problem(401));await old
    expect((await store.drafts(scope('user_a'))).map(d=>d.id)).toEqual(['fresh'])
    expect(s.client.selectedStudentId).toBe('student_user_a')
  })
  it.each([401,403])('current %s still purges owned data but not another account',async(status)=>{
    const store=new DraftStore(new IDBFactory()); const s=setup(store)
    await s.client.login('user_a','synthetic password')
    await store.saveDraft(draft('user_a','own'));await store.saveDraft(draft('user_b','foreign'))
    s.hold();const old=s.client.history().catch(e=>e);await tick();s.pending[0].resolve(problem(status));await old
    expect(await store.drafts(scope('user_a'))).toEqual([])
    expect((await store.drafts(scope('user_b'))).map(d=>d.id)).toEqual(['foreign'])
    expect(s.client.identity).toBeNull()
  })
  it('already-created purge transaction cannot delete later fresh write',async()=>{
    const store=new DraftStore(new IDBFactory());await store.saveDraft(draft('user_a','old'))
    const purging=store.purgeScopes(k=>k===scope('user_a'))
    const writing=store.saveDraft(draft('user_a','fresh'))
    await Promise.all([purging,writing])
    expect((await store.drafts(scope('user_a'))).map(d=>d.id)).toEqual(['fresh'])
  })
  it('shares only an inflight request and checks again after it settles',async()=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.hold()
    const first=s.client.history(),second=s.client.listDrafts();await tick();expect(s.pending).toHaveLength(1)
    s.pending[0].resolve(reply(identity('user_a')));await Promise.all([first,second])
    const next=s.client.history();await tick();expect(s.pending).toHaveLength(2)
    s.pending[1].resolve(reply(identity('user_a')));await next
  })
  it('old flight cleanup cannot clear a new account inflight identity read',async()=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.hold()
    const old=s.client.history().catch(e=>e);await tick();s.unhold();await s.client.login('user_b','synthetic password');s.hold()
    const fresh=s.client.history();await tick();expect(s.pending).toHaveLength(2)
    s.pending[0].resolve(reply(identity('user_a')));await old
    const second=s.client.listDrafts();await tick();expect(s.pending).toHaveLength(2)
    s.pending[1].resolve(reply(identity('user_b')));await Promise.all([fresh,second])
    expect(s.client.selectedStudentId).toBe('student_user_b')
  })
  it('password change retires old identity work even when synthetic token is equal',async()=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.hold()
    const old=s.client.history().catch(e=>e);await tick();s.unhold()
    await s.client.changePassword('synthetic password','changed123')
    s.pending[0].resolve(problem(401));await old
    expect(s.client.selectedStudentId).toBe('student_user_a')
    await s.client.history()
  })
  it('synchronous changed callback shares inflight verification without recursion',async()=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.hold()
    let nested: Promise<unknown> | undefined
    const unsubscribe=s.client.subscribe(()=>{nested=s.client.listDrafts()})
    const first=s.client.history();await tick()
    const updated=identity('user_a');updated.authorized_students[0].display_name='Updated synthetic'
    s.pending[0].resolve(reply(updated));await first;expect(nested).toBeDefined();await nested
    unsubscribe();expect(s.pending).toHaveLength(1)
  })
  it('synchronous identity listener logout cannot authorize the completed response',async()=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.hold()
    const unsubscribe=s.client.subscribe(()=>{unsubscribe();s.client.disconnect()})
    const first=s.client.history().catch(e=>e);await tick()
    const updated=identity('user_a');updated.authorized_students[0].display_name='Updated synthetic'
    s.pending[0].resolve(reply(updated));expect(await first).toMatchObject({code:'session_changed'})
    expect(s.calls.filter(c=>c.path.endsWith('/homeworks'))).toHaveLength(0)
  })
  it.each([401,502])('concurrent %s failure settles everyone and later restore rechecks',async(status)=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.hold()
    const all=Promise.allSettled([s.client.history(),s.client.listDrafts()]);await tick();expect(s.pending).toHaveLength(1)
    s.pending[0].resolve(status===401?problem(401):reply({},502));expect((await all).every(v=>v.status==='rejected')).toBe(true)
    expect(s.calls.filter(c=>c.path.endsWith('/homeworks'))).toHaveLength(0)
    s.unhold();await s.client.login('user_a','synthetic password');await s.client.history()
    expect(s.client.selectedStudentId).toBe('student_user_a')
  })
  it.each(['user_a','user_b'])('late old business GET 401 cannot clear relogin %s',async(nextUser)=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.holdBusiness()
    const old=s.client.history().catch(e=>e);await vi.waitFor(()=>expect(s.business).toHaveLength(1))
    await s.client.login(nextUser,'synthetic password');s.business[0].resolve(problem(401));await old
    expect(s.client.identity?.actor_id).toBe('actor_'+nextUser)
    expect(s.client.selectedStudentId).toBe('student_'+nextUser)
  })
  it('current business GET 401 still disconnects current session',async()=>{
    const s=setup();await s.client.login('user_a','synthetic password');s.holdBusiness()
    const old=s.client.history().catch(e=>e);await vi.waitFor(()=>expect(s.business).toHaveLength(1))
    s.business[0].resolve(problem(401));await old
    expect(s.client.identity).toBeNull();expect(s.client.selectedStudentId).toBeNull()
  })
})
