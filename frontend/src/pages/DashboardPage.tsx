import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, newIdempotencyKey } from '../api/client'
import type { OperationResponse } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { CurrencySelect, ErrorBanner, Field, Success } from '../components/ui'
import { formatDateTime, formatMoney, validateAmount } from '../lib/format'
import { useQuote } from '../lib/useQuote'

type Mode = 'credit' | 'debit' | 'exchange'

export function DashboardPage() {
  const { user } = useAuth()
  const wallets = useQuery({ queryKey: ['wallets'], queryFn: api.wallets })
  const [mode, setMode] = useState<Mode>('credit')

  return (
    <div className="stack">
      <section className="card hero">
        <div>
          <div className="muted small">Total balance ({wallets.data?.total_currency ?? user?.default_currency})</div>
          <div className="hero-amount">
            {wallets.data?.total != null ? formatMoney(wallets.data.total, wallets.data.total_currency) : '—'}
          </div>
          {wallets.data?.rates_as_of && (
            <div className="muted small">Rates as of {formatDateTime(wallets.data.rates_as_of)}</div>
          )}
        </div>
      </section>

      <ErrorBanner error={wallets.error} />
      <section className="wallet-grid">
        {wallets.isLoading && <div className="muted">Loading wallets…</div>}
        {wallets.data?.wallets.map((w) => (
          <div key={w.id} className={`card wallet ${w.currency === user?.default_currency ? 'wallet-default' : ''}`}>
            <div className="wallet-currency">
              {w.currency}
              {w.currency === user?.default_currency && <span className="badge">default</span>}
            </div>
            <div className="wallet-balance">{formatMoney(w.balance, w.currency)}</div>
          </div>
        ))}
      </section>

      <section className="card">
        <div className="tabs" role="tablist">
          {(['credit', 'debit', 'exchange'] as Mode[]).map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? 'tab active' : 'tab'} onClick={() => setMode(m)}>
              {m === 'credit' ? 'Add money' : m === 'debit' ? 'Withdraw' : 'Exchange'}
            </button>
          ))}
        </div>
        {mode === 'exchange' ? (
          <ExchangeForm walletCurrencies={wallets.data?.wallets.map((w) => w.currency) ?? []} />
        ) : (
          <MovementForm key={mode} mode={mode} defaultCurrency={user?.default_currency ?? 'USD'}
            walletCurrencies={wallets.data?.wallets.map((w) => w.currency) ?? []} />
        )}
      </section>
    </div>
  )
}

function useOperation(fn: (key: string) => Promise<OperationResponse>) {
  const qc = useQueryClient()
  // One key per logical submission: retries/double-clicks reuse it, success rotates it.
  const key = useRef(newIdempotencyKey())
  return useMutation({
    mutationFn: () => fn(key.current),
    onSuccess: () => {
      key.current = newIdempotencyKey()
      qc.invalidateQueries({ queryKey: ['wallets'] })
      qc.invalidateQueries({ queryKey: ['transactions'] })
    },
  })
}

function MovementForm({ mode, defaultCurrency, walletCurrencies }: {
  mode: 'credit' | 'debit'; defaultCurrency: string; walletCurrencies: string[]
}) {
  const [amount, setAmount] = useState('')
  const [currency, setCurrency] = useState(defaultCurrency)
  const [walletCurrency, setWalletCurrency] = useState(defaultCurrency)
  const [description, setDescription] = useState('')
  const [touched, setTouched] = useState(false)
  const amountError = validateAmount(amount, currency)
  const quote = useQuote(amount, currency, walletCurrency, !amountError)

  const op = useOperation((key) =>
    (mode === 'credit' ? api.credit : api.debit)(
      { amount, currency, wallet_currency: walletCurrency, description: description || undefined }, key))

  function submit(e: FormEvent) {
    e.preventDefault()
    setTouched(true)
    if (!amountError) op.mutate(undefined, { onSuccess: () => { setAmount(''); setDescription(''); setTouched(false) } })
  }
  const tx = op.data?.transactions[0]
  return (
    <form className="form-grid" onSubmit={submit} noValidate>
      <Field label="Amount" error={touched ? amountError : null}>
        <input inputMode="decimal" placeholder="0.00" value={amount} onChange={(e) => { setAmount(e.target.value); op.reset() }} />
      </Field>
      <Field label="Currency">
        <CurrencySelect name="currency" value={currency} onChange={setCurrency} />
      </Field>
      <Field label={mode === 'credit' ? 'Into wallet' : 'From wallet'}>
        <CurrencySelect name="wallet_currency" value={walletCurrency} onChange={setWalletCurrency}
          options={mode === 'debit' ? walletCurrencies : undefined} />
      </Field>
      <Field label="Description (optional)">
        <input maxLength={140} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <QuoteLine quote={quote.data} error={quote.error} same={currency === walletCurrency} />
      <div className="form-actions">
        <button className="btn btn-primary" disabled={op.isPending}>
          {op.isPending ? 'Processing…' : mode === 'credit' ? 'Add money' : 'Withdraw'}
        </button>
      </div>
      <ErrorBanner error={op.error} />
      {tx && (
        <Success>
          {mode === 'credit' ? 'Credited' : 'Debited'} {formatMoney(tx.amount, tx.currency)}
          {tx.original_currency !== tx.currency && ` (from ${formatMoney(tx.original_amount, tx.original_currency)} @ ${tx.fx_rate})`}
          . New balance {formatMoney(tx.balance_after, tx.currency)}.
        </Success>
      )}
    </form>
  )
}

function ExchangeForm({ walletCurrencies }: { walletCurrencies: string[] }) {
  const [amount, setAmount] = useState('')
  const [from, setFrom] = useState(walletCurrencies[0] ?? 'USD')
  const [to, setTo] = useState('EUR')
  const [touched, setTouched] = useState(false)
  const amountError = validateAmount(amount, from) ?? (from === to ? 'Choose two different currencies' : null)
  const quote = useQuote(amount, from, to, !amountError)
  const op = useOperation((key) => api.exchange({ amount, from_currency: from, to_currency: to }, key))

  return (
    <form className="form-grid" noValidate onSubmit={(e) => { e.preventDefault(); setTouched(true); if (!amountError) op.mutate(undefined, { onSuccess: () => { setAmount(''); setTouched(false) } }) }}>
      <Field label="Amount" error={touched ? amountError : null}>
        <input inputMode="decimal" placeholder="0.00" value={amount} onChange={(e) => { setAmount(e.target.value); op.reset() }} />
      </Field>
      <Field label="From wallet">
        <CurrencySelect name="from" value={from} onChange={setFrom} options={walletCurrencies} />
      </Field>
      <Field label="To">
        <CurrencySelect name="to" value={to} onChange={setTo} />
      </Field>
      <QuoteLine quote={quote.data} error={quote.error} same={from === to} />
      <div className="form-actions">
        <button className="btn btn-primary" disabled={op.isPending}>{op.isPending ? 'Exchanging…' : 'Exchange'}</button>
      </div>
      <ErrorBanner error={op.error} />
      {op.data && (
        <Success>
          Exchanged {formatMoney(op.data.transactions[0].amount, op.data.transactions[0].currency)} →{' '}
          {formatMoney(op.data.transactions[1].amount, op.data.transactions[1].currency)}
        </Success>
      )}
    </form>
  )
}

export function QuoteLine({ quote, error, same }: {
  quote?: { converted_amount: string; to_currency: string; rate: string; from_currency: string }
  error: unknown; same: boolean
}) {
  if (same) return null
  if (error) return <div className="quote muted small">Live quote unavailable</div>
  if (!quote) return null
  return (
    <div className="quote">
      ≈ <strong>{formatMoney(quote.converted_amount, quote.to_currency)}</strong>
      <span className="muted small"> · 1 {quote.from_currency} = {quote.rate} {quote.to_currency}</span>
    </div>
  )
}
