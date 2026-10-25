import {renderToStaticMarkup} from 'react-dom/server'
import {expect,it} from 'vitest'
import examples from '../../../../contracts/phase1/examples.json'
import type {CaseDetail,Decision} from '../core/types'
import {QuestionCard} from './RecognitionResults'
import {MathText} from './MathText'
const fixture=<T,>(name:string)=>structuredClone(examples.entries.find(x=>x.name===name)!.value) as T
function sample():CaseDetail{
 const row:CaseDetail={case:fixture('resolved_case'),question:fixture('question_revision_2'),attempt:fixture('correction_across_question_revision')}
 const d=fixture<Decision>('model_candidate');Object.assign(d,{verdict:'incorrect',validity:'candidate',origin:'model',answer_checks:[],correct_answer:'-2/7',input_versions:{...d.input_versions,question_id:row.case.question_id,question_revision:row.case.current_question_revision,attempt_id:row.case.current_attempt_id,attempt_recognition_revision:row.attempt.recognition_revision}})
 row.case.current_decision=d;row.question.stem_text='计算：(-5)^4';row.question.options=[];row.attempt.answer_text='2/7';row.attempt.answer_status='present';row.attempt.recognition_status='ready';return row
}
const card=(row:CaseDetail,open=true)=>renderToStaticMarkup(<QuestionCard row={row} index={0} open={open} onToggle={()=>{}}/>)
it('only expanded body has math and a single toggle; raw header and source remain unchanged',()=>{
 const row=sample(),before=structuredClone(row),html=card(row)
 expect(html).toContain('<sup>4</sup>');expect(html.match(/class="math-fraction"/g)).toHaveLength(1)
 expect(html.match(/>查看原文</g)).toHaveLength(1);expect(html.match(/>复制本题原文</g)).toHaveLength(1)
 const header=html.slice(html.indexOf('<button class="recognition-card-head"'),html.indexOf('</button>'))
 expect(header).toContain('(-5)^4');expect(header).not.toContain('<sup>');expect(header).not.toContain('查看原文')
 expect(card(row,false)).not.toContain('math-view-tools');expect(row).toEqual(before)
})
it('original mode preserves whitespace and ambiguous text exactly',()=>{
 expect(renderToStaticMarkup(<MathText text={'  -2/7\n'} original/>)).toBe('  -2/7\n')
 expect(renderToStaticMarkup(<MathText text="-1^2n+1"/>)).toBe('-1^2n+1')
})
it('plain or ambiguous-only cards get no toolbar',()=>{
 const row=sample();row.question.stem_text='-1^2n+1';row.attempt.answer_text='1/2x';row.case.current_decision!.correct_answer='不确定'
 expect(card(row)).not.toContain('math-view-tools');expect(card(row)).not.toContain('<sup>')
})
it('position prompts/answers/corrections preserve source and result labels',()=>{
 const row=sample();row.question.blank_count=2;row.case.current_decision!.answer_checks=[{index:1,prompt:'(-2)^3',student_answer:'2/7',verdict:'incorrect',correct_answer:'-2/7',explanation:'原说明'},{index:2,prompt:'x^(2n+1)',student_answer:'2^3',verdict:'correct',correct_answer:null,explanation:'原说明'}] as unknown as NonNullable<Decision['answer_checks']>
 const before=structuredClone(row),html=card(row);expect(html).toContain('逐空判定');expect(html).toContain('<sup>2n+1</sup>');expect(html).toContain('✕ 错误');expect(html).toContain('✓ 正确');expect(row).toEqual(before)
})
it('AI incomplete remains a draft and exposes no correct answer or new subject control',()=>{
 const row=sample(),d=row.case.current_decision!;d.verdict='unable_to_judge';d.validity='needs_review';d.verification={mode:'two_model',status:'degraded',independent_readback:false,reason_codes:['provider_unavailable']};row.question.subject=null;row.attempt.recognition_status='needs_revision'
 const html=card(row);expect(html).toContain('AI 识读草稿');expect(html).toContain('AI 核对暂未完成');expect(html).toContain('可能与照片不同');expect(html).not.toContain('正确答案</span>');expect(html).not.toContain('选择题目学科')
})
it('script-shaped text is escaped and commands stay literal',()=>{
 const html=renderToStaticMarkup(<MathText text={'<img src=x onerror="globalThis.bad=1"> 2^3'}/>);expect(html).toContain('&lt;img');expect(html).not.toContain('<img');expect(html).not.toContain('<script')
 expect(renderToStaticMarkup(<MathText text={'\\href{javascript:bad}{x^2}'}/>)).not.toContain('<sup>')
})
it('stored options retain their original order/labels even if type is unknown, with no mutation',()=>{
 const row=sample();row.question.question_type='unknown';row.question.options=[{label:'D',text:'(-2)^3'},{label:'B',text:'-2^3'},{label:'A',text:'-1^2n+1'}]
 const before=structuredClone(row),html=card(row);expect(html).toContain('aria-label="原题选项"');expect(html.indexOf('<strong>D</strong>')).toBeLessThan(html.indexOf('<strong>B</strong>'));expect(html.indexOf('<strong>B</strong>')).toBeLessThan(html.indexOf('<strong>A</strong>'));expect(html).toContain('-1^2n+1');expect(row).toEqual(before)
})
it('empty options produce no placeholder or invented choice',()=>{expect(card(sample())).not.toContain('recognition-original-options')})
