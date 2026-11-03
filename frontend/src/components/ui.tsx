import type { ReactNode } from 'react'
import { ApiError } from '../api/client'
import { useCurrencies } from '../lib/useCurrencies'

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null
  const msg = error instanceof Error ? error.message : 'Something went wrong'
  const rid = error instanceof ApiError ? error.requestId : undefined
  return (
    <div className="alert alert-error" role="alert">
      {msg}
      {rid && <span className="muted small"> (ref: {rid.slice(0, 8)})</span>}
    </div>
  )
}

export function Success({ children }: { children: ReactNode }) {
  return (
    <div className="alert alert-success" role="status">
      {children}
    </div>
  )
}

export function Field({
  label,
  error,
  children,
}: {
  label: string
  error?: string | null
  children: ReactNode
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {error && <span className="field-error">{error}</span>}
    </label>
  )
}

export function CurrencySelect({
  value,
  onChange,
  name,
  allowEmpty,
  emptyLabel = 'Any',
  options,
}: {
  value: string
  onChange: (v: string) => void
  name?: string
  allowEmpty?: boolean
  emptyLabel?: string
  options?: string[]
}) {
  const { data } = useCurrencies()
  const list = options ?? data ?? [value].filter(Boolean)
  return (
    <select name={name} aria-label={name} value={value} onChange={(e) => onChange(e.target.value)}>
      {allowEmpty && <option value="">{emptyLabel}</option>}
      {list.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  )
}

export function Pagination({
  page,
  pages,
  total,
  onPage,
}: {
  page: number
  pages: number
  total: number
  onPage: (p: number) => void
}) {
  return (
    <div className="pagination">
      <button className="btn btn-ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ← Prev
      </button>
      <span className="muted">
        Page {page} of {pages} · {total} transactions
      </span>
      <button className="btn btn-ghost" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next →
      </button>
    </div>
  )
}
