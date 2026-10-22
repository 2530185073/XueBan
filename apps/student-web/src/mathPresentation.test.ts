import { expect, it } from 'vitest'
import { mathPresentation } from './mathPresentation'
it.each(['(-5)^4','-2^5','(-2)^5','10^6的底数','(-1)^(2n+1)','-1^{2n+1}','x^(2)+1','x^{2}/3','2^−3','x^(3)!','x^(2),5','x^(2):3'])('formats only scoped powers with reversible source spans: %s',text=>{
 const value=mathPresentation(text);expect(value?.kind).toBe('powers')
 if(value?.kind==='powers'){expect(value.source).toBe(text);expect(value.parts.map(p=>p.kind==='exponent'?p.source:p.text).join('')).toBe(text)}
})
it.each(['-1^2n+1','2^2.5','2^2 n','2^2π','2^3^4','2^(3)^4','x^(2','x^{2)','2^3+1','x^2/3','2^1234567','x^{a^b}','2^3 和 -1^2n','2^3 和 x^2.5','[x^2','x^2]','[x^2)}','x^3!','x^3！','x^2,5','x^2:3','x^2，5','x^2：3','x^2, n','x^2，５','x^2：３','(-1/2)^3，结果','x^2,α','x^2:字','x^2,𝟝','x^2,','x^2：'])('never formats a valid prefix of an ambiguous field: %s',text=>{expect(mathPresentation(text)).toBeNull()})
it.each([['-2/7','-2','7'],['2/-7','2','-7'],['−2/7','−2','7'],['+02/007','+02','007'],['1/0','1','0']])('preserves all signs/digits in an atomic fraction without evaluation: %s',(text,numerator,denominator)=>{
 expect(mathPresentation(text)).toEqual({kind:'fraction',numerator,denominator,source:text})
})
it.each(['1/2x','÷4/8','2 1/3','1/2 米','1/2+1/3','(1/2)','1.5/2','1 / 2','\\frac{1}{2}','$x^2$','原稿：2，改为3','√(2/3)'])('keeps other notation as text: %s',text=>{expect(mathPresentation(text)).toBeNull()})
it('bounds work and rejects unmatched groups',()=>{
 for(const text of ['x^2 '+'字'.repeat(4000),'x^{'+ 'x'.repeat(81)+'}',Array(34).fill('x^2').join('，'),'x^2，旁注('])expect(mathPresentation(text)).toBeNull()
})
