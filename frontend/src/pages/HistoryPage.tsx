import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { TransactionType } from '../api/types'
import { CurrencySelect, ErrorBanner, Pagination } from '../components/ui'
import { TYPE_LABELS, formatDateTime, formatMoney, isOutflow } from '../lib/format'

const EMPTY = { type: '', currency: '', direction: '', date_from: '', date_to: '' }

export function HistoryPage() {
  const [filters, setFilters] = useState(EMPTY)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const set = (k: keyof typeof EMPTY) => (v: string) => { setFilters((f) => ({ ...f, [k]: v })); setPage(1) }

  const query = {
    ...filters,
    date_from: filters.date_from ? new Date(filters.date_from).toISOString() : '',
    date_to: filters.date_to ? new Date(`${filters.date_to}T23:59:59`).toISOString() : '',
    page,
    page_size: 15,
  }
  const txs = useQuery({
    queryKey: ['transactions', query],
    queryFn: () => api.transactions(query),
    placeholderData: keepPreviousData,
  })

  return (
    <div className="stack">
      <h1>Transaction history</h1>
      <div className="card filters">
        <select aria-label="type" value={filters.type} onChange={(e) => set('type')(e.target.value)}>
          <option value="">All types</option>
          {Object.entries(TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <CurrencySelect name="currency" value={filters.currency} onChange={set('currency')} allowEmpty emptyLabel="All currencies" />
        <select aria-label="direction" value={filters.direction} onChange={(e) => set('direction')(e.target.value)}>
          <option value="">In & out</option>
          <option value="in">Money in</option>
          <option value="out">Money out</option>
        </select>
        <input aria-label="from date" type="date" value={filters.date_from} onChange={(e) => set('date_from')(e.target.value)} />
        <input aria-label="to date" type="date" value={filters.date_to} onChange={(e) => set('date_to')(e.target.value)} />
        <button className="btn btn-ghost" onClick={() => { setFilters(EMPTY); setPage(1) }}>Reset</button>
      </div>
      <ErrorBanner error={txs.error} />
      <div className="card table-wrap">
        <table>
          <thead>
            <tr><th>Date</th><th>Type</th><th>Details</th><th className="num">Amount</th><th className="num">Balance</th></tr>
          </thead>
          <tbody>
            {txs.data?.items.map((t) => (
              <tr key={t.id} onClick={() => setSelected(t.id)} className="clickable">
                <td>{formatDateTime(t.created_at)}</td>
                <td><span className={`pill pill-${isOutflow(t.type) ? 'out' : 'in'}`}>{TYPE_LABELS[t.type as TransactionType]}</span></td>
                <td>
                  {t.counterparty ? `${isOutflow(t.type) ? 'To' : 'From'} ${t.counterparty.full_name}` : t.description ?? ''}
                  {t.original_currency !== t.currency && (
                    <div className="muted small">{formatMoney(t.original_amount, t.original_currency)} @ {t.fx_rate}</div>
                  )}
                </td>
                <td className={`num ${isOutflow(t.type) ? 'neg' : 'pos'}`}>
                  {isOutflow(t.type) ? '−' : '+'}{formatMoney(t.amount, t.currency)}
                </td>
                <td className="num muted">{formatMoney(t.balance_after, t.currency)}</td>
              </tr>
            ))}
            {txs.data?.items.length === 0 && (
              <tr><td colSpan={5} className="center muted">No transactions match these filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {txs.data && <Pagination page={txs.data.page} pages={txs.data.pages} total={txs.data.total} onPage={setPage} />}
      {selected && <TransactionDrawer id={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

function TransactionDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, error } = useQuery({ queryKey: ['transaction', id], queryFn: () => api.transaction(id) })
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer card" onClick={(e) => e.stopPropagation()} aria-label="Transaction details">
        <button className="btn btn-ghost drawer-close" onClick={onClose}>✕</button>
        <h2>Transaction details</h2>
        <ErrorBanner error={error} />
        {data && (
          <dl className="details">
            <dt>ID</dt><dd className="mono">{data.id}</dd>
            <dt>Type</dt><dd>{TYPE_LABELS[data.type]}</dd>
            <dt>Amount</dt><dd>{formatMoney(data.amount, data.currency)}</dd>
            <dt>Requested</dt><dd>{formatMoney(data.original_amount, data.original_currency)}</dd>
            <dt>FX rate</dt><dd>{data.fx_rate}</dd>
            <dt>Balance after</dt><dd>{formatMoney(data.balance_after, data.currency)}</dd>
            {data.counterparty && (<><dt>Counterparty</dt><dd>{data.counterparty.full_name} ({data.counterparty.email})</dd></>)}
            {data.description && (<><dt>Note</dt><dd>{data.description}</dd></>)}
            {data.reference_id && (<><dt>Reference</dt><dd className="mono">{data.reference_id}</dd></>)}
            <dt>Date</dt><dd>{formatDateTime(data.created_at)}</dd>
            {data.fx_snapshot && (
              <>
                <dt>Rate source</dt><dd>{data.fx_snapshot.provider}</dd>
                <dt>Rates fetched</dt><dd>{formatDateTime(data.fx_snapshot.fetched_at)}</dd>
                <dt>Snapshot</dt><dd className="mono">{data.fx_snapshot.id}</dd>
              </>
            )}
          </dl>
        )}
      </aside>
    </div>
  )
}
