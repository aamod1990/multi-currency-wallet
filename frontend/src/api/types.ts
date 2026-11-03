export type TransactionType =
  | 'CREDIT'
  | 'DEBIT'
  | 'TRANSFER_OUT'
  | 'TRANSFER_IN'
  | 'CONVERSION_OUT'
  | 'CONVERSION_IN'

export interface User {
  id: string
  email: string
  full_name: string
  default_currency: string
  has_photo: boolean
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface Wallet {
  id: string
  currency: string
  balance: string
  updated_at: string
}

export interface WalletList {
  wallets: Wallet[]
  total: string | null
  total_currency: string
  rates_as_of: string | null
}

export interface Counterparty {
  id: string
  full_name: string
  email: string
}

export interface Transaction {
  id: string
  wallet_id: string
  type: TransactionType
  amount: string
  currency: string
  balance_after: string
  original_amount: string
  original_currency: string
  fx_rate: string
  fx_snapshot_id: string | null
  reference_id: string | null
  counterparty: Counterparty | null
  description: string | null
  created_at: string
}

export interface FxSnapshot {
  id: string
  provider: string
  base_currency: string
  provider_updated_at: string | null
  fetched_at: string
}

export interface TransactionDetail extends Transaction {
  fx_snapshot: FxSnapshot | null
}

export interface OperationResponse {
  transactions: Transaction[]
  wallets: Wallet[]
}

export interface Transfer {
  id: string
  sender: Counterparty
  recipient: Counterparty
  amount: string
  currency: string
  source_amount: string
  source_currency: string
  source_rate: string
  target_amount: string
  target_currency: string
  target_rate: string
  fx_snapshot_id: string | null
  status: string
  note: string | null
  created_at: string
}

export interface Quote {
  amount: string
  from_currency: string
  to_currency: string
  rate: string
  converted_amount: string
  snapshot_id: string | null
  rates_as_of: string | null
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    request_id?: string
    details?: unknown
  }
}
