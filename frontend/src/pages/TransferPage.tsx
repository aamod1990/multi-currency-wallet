import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api, newIdempotencyKey } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { CurrencySelect, ErrorBanner, Field, Success } from '../components/ui'
import { formatMoney, validateAmount } from '../lib/format'
import { useQuote } from '../lib/useQuote'
import { QuoteLine } from './DashboardPage'

export function TransferPage() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const wallets = useQuery({ queryKey: ['wallets'], queryFn: api.wallets })
  const walletCurrencies = wallets.data?.wallets.map((w) => w.currency) ?? []
  const [form, setForm] = useState({
    recipient_email: '', amount: '', currency: user?.default_currency ?? 'USD',
    source_currency: user?.default_currency ?? 'USD', note: '',
  })
  const [touched, setTouched] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const key = useRef(newIdempotencyKey())
  const set = (k: keyof typeof form) => (v: string) => { setForm((f) => ({ ...f, [k]: v })); setConfirming(false) }

  const errors = {
    recipient_email: /^\S+@\S+\.\S+$/.test(form.recipient_email) ? null
      : form.recipient_email === user?.email ? 'You cannot send money to yourself' : 'Enter the recipient email',
    amount: validateAmount(form.amount, form.currency),
  }
  if (form.recipient_email.toLowerCase() === user?.email) errors.recipient_email = 'You cannot send money to yourself'
  const valid = !errors.recipient_email && !errors.amount
  const quote = useQuote(form.amount, form.currency, form.source_currency, !errors.amount)
  const balance = wallets.data?.wallets.find((w) => w.currency === form.source_currency)?.balance

  const send = useMutation({
    mutationFn: () => api.transfer({ ...form, note: form.note || undefined }, key.current),
    onSuccess: () => {
      key.current = newIdempotencyKey()
      setConfirming(false)
      setTouched(false)
      setForm((f) => ({ ...f, amount: '', note: '' }))
      qc.invalidateQueries({ queryKey: ['wallets'] })
      qc.invalidateQueries({ queryKey: ['transactions'] })
    },
    onError: (e) => {
      // Business rejections are final for this payload; allow a fresh attempt.
      if (e instanceof ApiError && e.status < 500) key.current = newIdempotencyKey()
      setConfirming(false)
    },
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    setTouched(true)
    if (!valid) return
    if (!confirming) { setConfirming(true); return }
    send.mutate()
  }

  const t = send.data
  return (
    <div className="stack narrow">
      <h1>Send money</h1>
      <form className="card form-grid" onSubmit={submit} noValidate>
        <Field label="Recipient email" error={touched ? errors.recipient_email : null}>
          <input type="email" value={form.recipient_email} onChange={(e) => set('recipient_email')(e.target.value)} />
        </Field>
        <Field label="Amount" error={touched ? errors.amount : null}>
          <input inputMode="decimal" placeholder="0.00" value={form.amount} onChange={(e) => set('amount')(e.target.value)} />
        </Field>
        <Field label="Currency of amount">
          <CurrencySelect name="currency" value={form.currency} onChange={set('currency')} />
        </Field>
        <Field label="Pay from wallet">
          <CurrencySelect name="source_currency" value={form.source_currency} onChange={set('source_currency')} options={walletCurrencies.length ? walletCurrencies : undefined} />
        </Field>
        <Field label="Note (optional)">
          <input maxLength={140} value={form.note} onChange={(e) => set('note')(e.target.value)} />
        </Field>
        <div className="muted small">
          Available: {balance != null ? formatMoney(balance, form.source_currency) : '—'} · The recipient is credited in their default currency.
        </div>
        <QuoteLine quote={quote.data} error={quote.error} same={form.currency === form.source_currency} />
        {confirming && (
          <div className="alert alert-info">
            Confirm sending {formatMoney(form.amount, form.currency)} to <strong>{form.recipient_email}</strong>?
          </div>
        )}
        <div className="form-actions">
          {confirming && <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>Cancel</button>}
          <button className="btn btn-primary" disabled={send.isPending}>
            {send.isPending ? 'Sending…' : confirming ? 'Confirm & send' : 'Review transfer'}
          </button>
        </div>
        <ErrorBanner error={send.error} />
        {t && (
          <Success>
            Sent {formatMoney(t.source_amount, t.source_currency)} to {t.recipient.full_name}; they received{' '}
            {formatMoney(t.target_amount, t.target_currency)}.
          </Success>
        )}
      </form>
    </div>
  )
}
