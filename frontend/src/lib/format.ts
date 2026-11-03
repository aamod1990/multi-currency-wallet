import type { TransactionType } from '../api/types'

export function formatMoney(amount: string | number, currency: string): string {
  const n = typeof amount === 'string' ? Number(amount) : amount
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: 4,
    }).format(n)
  } catch {
    return `${amount} ${currency}`
  }
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

export const TYPE_LABELS: Record<TransactionType, string> = {
  CREDIT: 'Credit',
  DEBIT: 'Debit',
  TRANSFER_IN: 'Transfer received',
  TRANSFER_OUT: 'Transfer sent',
  CONVERSION_IN: 'Exchange in',
  CONVERSION_OUT: 'Exchange out',
}

export const isOutflow = (t: TransactionType) =>
  t === 'DEBIT' || t === 'TRANSFER_OUT' || t === 'CONVERSION_OUT'

/** Client-side mirror of the server rule; the server stays the source of truth. */
export function validateAmount(value: string, currency: string): string | null {
  if (!value.trim()) return 'Amount is required'
  if (!/^\d+(\.\d+)?$/.test(value.trim())) return 'Enter a valid positive number'
  if (Number(value) <= 0) return 'Amount must be greater than zero'
  const decimals = value.split('.')[1]?.length ?? 0
  const allowed = ZERO_DECIMAL.has(currency) ? 0 : THREE_DECIMAL.has(currency) ? 3 : 2
  if (decimals > allowed) return `${currency} allows at most ${allowed} decimal places`
  return null
}

const ZERO_DECIMAL = new Set(['JPY', 'KRW'])
const THREE_DECIMAL = new Set(['KWD', 'BHD'])
