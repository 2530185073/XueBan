import { describe, expect, it } from 'vitest'
import type { CaseDetail } from './types'
import { orderRowsByPhoto } from './cases'

const row = (id: string, assetId: string, y: number, x = 0): CaseDetail => ({
  case: { id, lifecycle: 'active' } as CaseDetail['case'],
  question: {
    question_id: `question_${id}`,
    revision: 1,
    homework_id: 'homework_test',
    stem_text: id,
    question_type: 'unknown',
    options: [],
    blank_count: 0,
    recognition_uncertainties: [],
    regions: [{ asset_id: assetId, x, y, width: 100, height: 40, coordinate_space: 'original_pixels' }],
    source: 'recognizer',
    source_actor_id: null,
    bank_question_id: null,
    knowledge_point_ids: null,
    created_at: '2026-10-18T00:00:00Z',
    lifecycle: 'active',
    replacement_question_ids: [],
  } as CaseDetail['question'],
  attempt: {} as CaseDetail['attempt'],
})

describe('orderRowsByPhoto', () => {
  it('preserves server reading order when whole-page questions share the same source region', () => {
    const rows = [row('z9', 'page_1', 0), row('a1', 'page_1', 0), row('k3', 'page_1', 0)]
    rows.forEach((item, index) => { item.question.label = ['1', '三', '1'][index] })
    expect(orderRowsByPhoto(rows, ['page_1']).map(item => item.case.id)).toEqual(['z9', 'a1', 'k3'])
  })
  it('reads the left question first when the right column starts slightly higher', () => {
    const rows = [row('right', 'page_1', 95, 120), row('left', 'page_1', 100), row('next', 'page_1', 150)]
    expect(orderRowsByPhoto(rows, ['page_1']).map(item => item.case.id)).toEqual(['left', 'right', 'next'])
  })
  it('orders rows by page and coordinates instead of case IDs or printed labels', () => {
    const rows = [row('case_4', 'page_2', 20), row('case_3', 'page_1', 180), row('case_1', 'page_1', 20), row('case_2', 'page_1', 90)]
    expect(orderRowsByPhoto(rows, ['page_1', 'page_2']).map(item => item.case.id)).toEqual([
      'case_1', 'case_2', 'case_3', 'case_4',
    ])
  })
})
