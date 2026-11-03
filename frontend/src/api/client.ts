import type {
  ApiErrorBody,
  OperationResponse,
  Page,
  Quote,
  TokenResponse,
  Transaction,
  TransactionDetail,
  Transfer,
  User,
  Wallet,
  WalletList,
} from './types'

const TOKEN_KEY = 'wallet.token'

export const tokenStore = {
  get: (): string | null => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId?: string
  readonly details?: unknown

  constructor(status: number, body: Partial<ApiErrorBody> | null) {
    super(body?.error?.message ?? `Request failed (${status})`)
    this.status = status
    this.code = body?.error?.code ?? 'UNKNOWN'
    this.requestId = body?.error?.request_id
    this.details = body?.error?.details
  }

  /** Field-level messages from 422 responses, keyed by field name. */
  fieldErrors(): Record<string, string> {
    if (!Array.isArray(this.details)) return {}
    const out: Record<string, string> = {}
    for (const d of this.details as { field?: string; message?: string }[]) {
      if (d.field && d.message) out[d.field] = d.message.replace(/^Value error, /, '')
    }
    return out
  }
}

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

type Query = Record<string, string | number | string[] | undefined | null>

function buildQuery(q?: Query): string {
  if (!q) return ''
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x))
    else p.append(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

interface RequestOptions {
  method?: string
  body?: unknown
  query?: Query
  idempotencyKey?: string
  raw?: boolean
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {}
  const token = tokenStore.get()
  if (token) headers.Authorization = `Bearer ${token}`
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey
  let body: BodyInit | undefined
  if (opts.body instanceof FormData) body = opts.body
  else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }

  let res: Response
  try {
    res = await fetch(`/api${path}${buildQuery(opts.query)}`, {
      method: opts.method ?? 'GET',
      headers,
      body,
    })
  } catch {
    throw new ApiError(0, { error: { code: 'NETWORK_ERROR', message: 'Network error — check your connection' } })
  }

  if (res.status === 401 && token) onUnauthorized?.()
  if (!res.ok) {
    const parsed = await res.json().catch(() => null)
    throw new ApiError(res.status, parsed)
  }
  if (opts.raw) return res as unknown as T
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const newIdempotencyKey = () => crypto.randomUUID()

export const api = {
  signup: (b: { email: string; password: string; full_name: string; default_currency: string }) =>
    request<TokenResponse>('/auth/signup', { method: 'POST', body: b }),
  login: (b: { email: string; password: string }) =>
    request<TokenResponse>('/auth/login', { method: 'POST', body: b }),
  me: () => request<User>('/users/me'),
  updateMe: (b: { full_name?: string; default_currency?: string }) =>
    request<User>('/users/me', { method: 'PATCH', body: b }),
  uploadPhoto: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<User>('/users/me/photo', { method: 'PUT', body: fd })
  },
  deletePhoto: () => request<void>('/users/me/photo', { method: 'DELETE' }),
  photo: () => request<Response>('/users/me/photo', { raw: true }),

  currencies: () => request<string[]>('/fx/currencies'),
  quote: (amount: string, from: string, to: string) =>
    request<Quote>('/fx/quote', { query: { amount, from, to } }),

  wallets: () => request<WalletList>('/wallets'),
  createWallet: (currency: string) =>
    request<Wallet>('/wallets', { method: 'POST', body: { currency } }),
  credit: (b: MovementBody, key: string) =>
    request<OperationResponse>('/wallets/credit', { method: 'POST', body: b, idempotencyKey: key }),
  debit: (b: MovementBody, key: string) =>
    request<OperationResponse>('/wallets/debit', { method: 'POST', body: b, idempotencyKey: key }),
  exchange: (b: { amount: string; from_currency: string; to_currency: string }, key: string) =>
    request<OperationResponse>('/wallets/exchange', { method: 'POST', body: b, idempotencyKey: key }),

  transfer: (b: TransferBody, key: string) =>
    request<Transfer>('/transfers', { method: 'POST', body: b, idempotencyKey: key }),

  transactions: (q: Query) => request<Page<Transaction>>('/transactions', { query: q }),
  transaction: (id: string) => request<TransactionDetail>(`/transactions/${id}`),
}

export interface MovementBody {
  amount: string
  currency: string
  wallet_currency?: string
  description?: string
}

export interface TransferBody {
  recipient_email: string
  amount: string
  currency: string
  source_currency?: string
  note?: string
}
