import { describe, expect, it } from 'vitest'
import { formatMoney, isOutflow, validateAmount } from '../lib/format'

describe('validateAmount', () => {
  it('requires a value', () => expect(validateAmount('', 'USD')).toMatch(/required/))
  it('rejects non-numeric and negative input', () => {
    expect(validateAmount('abc', 'USD')).toMatch(/valid/)
    expect(validateAmount('-5', 'USD')).toMatch(/valid/)
    expect(validateAmount('0', 'USD')).toMatch(/greater/)
  })
  it('enforces currency precision', () => {
    expect(validateAmount('1.234', 'USD')).toMatch(/2 decimal/)
    expect(validateAmount('1.5', 'JPY')).toMatch(/0 decimal/)
    expect(validateAmount('1.234', 'KWD')).toBeNull()
    expect(validateAmount('10.50', 'EUR')).toBeNull()
  })
})

describe('formatMoney', () => {
  it('formats with currency', () => expect(formatMoney('1234.5', 'USD')).toContain('1,234.5'))
  it('falls back for unknown codes', () => expect(formatMoney('1', 'ZZZZ')).toBe('1 ZZZZ'))
})

it('classifies outflows', () => {
  expect(isOutflow('DEBIT')).toBe(true)
  expect(isOutflow('TRANSFER_IN')).toBe(false)
})
