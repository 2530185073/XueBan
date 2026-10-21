import { describe, expect, it } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentClient } from './student'
import { DraftStore } from './store'

class StorageMemory implements Storage {
  values = new Map<string,string>(); get length(){return this.values.size};clear(){this.values.clear()};getItem(k:string){return this.values.get(k)??null};key(i:number){return [...this.values.keys()][i]??null};removeItem(k:string){this.values.delete(k)};setItem(k:string,v:string){this.values.set(k,v)}
}
function deferred<T>() { let resolve!:(value:T)=>void; const promise=new Promise<T>(r=>{resolve=r});return {promise,resolve} }
const reply=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}})
const problem=(status:number,code:string)=>reply({type:'urn:xueban:auth',title:'Synthetic auth state',status,category:'authorization',code,detail:'synthetic',request_id:'request_synthetic',current_revision:null,retry_after_seconds:null},status)
const identity=(name:string)=>({actor_id:'actor_'+name,roles:['student'],authorized_students:[{student_id:'student_'+name,campus_id:'campus_one',display_name:'合成学生'}]})
async function until(fn:()=>boolean) { for(let n=0;n<100&&!fn();n++) await new Promise(r=>setTimeout(r,1));expect(fn()).toBe(true) }
function setup(){
 const calls:{path:string;method:string}[]=[],storage=new StorageMemory(),store=new DraftStore(new IDBFactory());let serial=0;
 let meHandler:(token:string)=>Promise<Response>=async token=>reply(identity(token.split('.')[1]));
 let businessHandler:()=>Promise<Response>=async()=>reply({items:[],next_cursor:null});
 const transport=async(input:RequestInfo|URL,init:RequestInit={})=>{
  const path=new URL(String(input)).pathname,method=init.method??'GET';calls.push({path,method});const token=(init.headers as Record<string,string>)?.Authorization?.slice(7)??'';
  if(path==='/v1/auth/login'){const name=JSON.parse(String(init.body)).username.split('.')[1];return reply({access_token:`synthetic.${name}.${++serial}`,expires_at:'2099-01-01T00:00:00Z',must_change_password:false})}
  if(path==='/v1/auth/account')return reply({username:'synthetic.'+token.split('.')[1],student_id:'student_'+token.split('.')[1],must_change_password:false});
  if(path==='/v1/auth/logout')return reply({logged_out:true});
  if(path==='/v1/me')return meHandler(token);
  if(/^\/v1\/students\/student_[ab]\/homeworks$/.test(path))return businessHandler();
  throw new Error('Unexpected synthetic route');
 };
 const client=new StudentClient({origin:'http://localhost',transport:transport as typeof fetch,storage,store});
 return {client,store,calls,storage,setMe:(handler:typeof meHandler)=>{meHandler=handler},setBusiness:(handler:typeof businessHandler)=>{businessHandler=handler},count:()=>calls.filter(c=>c.path==='/v1/me').length,login:(name='a')=>client.login('synthetic.'+name,'synthetic password only')};
}
describe('in-flight student identity authorization',()=>{
 it('coalesces concurrent public operations without caching completed authorization',async()=>{
  const s=setup();await s.login();const before=s.count(),gate=deferred<void>();s.setMe(async()=>{await gate.promise;return reply(identity('a'))});
  const work=Promise.all([s.client.listDrafts(),s.client.listDrafts(),s.client.listDrafts(),s.client.takeDeviceNotice(),s.client.history()]);
  await until(()=>s.count()-before===(1));gate.resolve();await work;
  expect(s.count()-before).toBe(1);
 });
 it('starts a fresh authorization after each completed operation',async()=>{
  const s=setup();await s.login();const before=s.count();await s.client.listDrafts();await s.client.history();
  expect(s.count()-before).toBe(2);
 });
 it('rejects all concurrent consumers on current grant denial and does not keep failed authorization',async()=>{
  const s=setup();await s.login();const gate=deferred<void>();s.setMe(async()=>{await gate.promise;return problem(403,'access_denied')});const before=s.count();
  const work=Promise.allSettled([s.client.listDrafts(),s.client.history()]);await until(()=>s.count()-before===(1));gate.resolve();const results=await work;
  expect(results.every(r=>r.status==='rejected')).toBe(true);expect(s.client.identity).toBeNull();expect(s.calls.filter(c=>c.path.endsWith('/homeworks'))).toHaveLength(0);
  s.setMe(async()=>reply(identity('a')));await s.login();await s.client.history();expect(s.client.selectedStudentId).toBe('student_a');
 });
 it('late success from the old account cannot publish into the new account',async()=>{
  const s=setup();await s.login();const gate=deferred<void>(),before=s.count();s.setMe(async token=>{if(token==='synthetic.a.1'){await gate.promise;return reply(identity('a'))}return reply(identity('b'))});
  const old=s.client.listDrafts().then(()=>null,e=>e.code);await until(()=>s.count()>before);await s.client.logout();await s.login('b');gate.resolve();expect(await old).toBe('session_changed');expect(s.client.selectedStudentId).toBe('student_b');
 });
 it('late 401 from old account must not revoke the new account',async()=>{
  const s=setup();await s.login();const gate=deferred<void>(),before=s.count();s.setMe(async token=>{if(token==='synthetic.a.1'){await gate.promise;return problem(401,'authentication_required')}return reply(identity('b'))});
  const old=s.client.listDrafts().then(()=>null,e=>e.code);await until(()=>s.count()>before);await s.client.logout();await s.login('b');gate.resolve();await old;
  const kept=s.client.selectedStudentId==='student_b';expect(kept).toBe(true);
 });
 it('old async scope purge completion must not clear a newer login',async()=>{
  const s=setup();await s.login();const gate=deferred<void>();let started=false;s.store.purgeScopes=async()=>{started=true;await gate.promise;return {drafts:0,operations:0} as never};
  s.setMe(async token=>token==='synthetic.a.1'?problem(403,'access_denied'):reply(identity('b')));const old=s.client.listDrafts().then(()=>null,e=>e.code);await until(()=>started);await s.login('b');gate.resolve();await old;
  const kept=s.client.selectedStudentId==='student_b';expect(kept).toBe(true);
 });
 it('same-token restore creates a new session object and cannot reuse the previous identity flight',async()=>{
  const s=setup();await s.login();const before=s.count(),gate=deferred<void>();s.setMe(async()=>{await gate.promise;return reply(identity('a'))});
  const old=s.client.listDrafts().then(()=>true,()=>false);await until(()=>s.count()===before+1);const restore=s.client.restore();await until(()=>s.count()===before+2);gate.resolve();await restore;
  expect(await old).toBe(false);expect(s.client.selectedStudentId).toBe('student_a');
 });
 it('a failed identity transport is not retried or cached',async()=>{
  const s=setup();await s.login();const before=s.count();s.setMe(async()=>{throw new TypeError('synthetic network failure')});await expect(s.client.listDrafts()).rejects.toMatchObject({code:'network_unknown'});expect(s.count()-before).toBe(1);
  s.setMe(async()=>reply(identity('a')));await s.client.restore();expect(s.client.selectedStudentId).toBe('student_a');
 });
 it.each(['other_account','same_token_restore'])('late business 401 cannot revoke %s',async mode=>{
  const s=setup();await s.login();const gate=deferred<void>();let requested=false;
  s.setBusiness(async()=>{requested=true;await gate.promise;return problem(401,'authentication_required')});
  const old=s.client.history().catch(error=>error);await until(()=>requested);
  if(mode==='other_account')await s.login('b');else await s.client.restore();
  gate.resolve();await old;expect(s.client.selectedStudentId).toBe(mode==='other_account'?'student_b':'student_a');
 });
 it('current business 401 still revokes the current session',async()=>{
  const s=setup();await s.login();s.setBusiness(async()=>problem(401,'authentication_required'));
  await expect(s.client.history()).rejects.toMatchObject({status:401});expect(s.client.identity).toBeNull();expect(s.client.selectedStudentId).toBeNull();
 });

})
