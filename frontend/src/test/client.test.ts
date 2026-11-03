import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, request, setUnauthorizedHandler, tokenStore } from '../api/client'

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), { status }),
  )
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('sends bearer token, idempotency key and JSON body', async () => {
    tokenStore.set('tok')
    const fetchFn = mockFetch(201, { transactions: [], wallets: [] })
    await api.credit({ amount: '10', currency: 'USD' }, 'idem-123456')
    const [url, init] = fetchFn.mock.calls[0]
    expect(url).toBe('/api/wallets/credit')
    expect(init.headers.Authorization).toBe('Bearer tok')
    expect(init.headers['Idempotency-Key']).toBe('idem-123456')
    expect(JSON.parse(init.body)).toEqual({ amount: '10', currency: 'USD' })
  })

  it('builds query strings with arrays and skips empty values', async () => {
    const fetchFn = mockFetch(200, { items: [], total: 0, page: 1, page_size: 20, pages: 1 })
    await request('/transactions', { query: { type: ['CREDIT', 'DEBIT'], currency: '', page: 2 } })
    expect(fetchFn.mock.calls[0][0]).toBe('/api/transactions?type=CREDIT&type=DEBIT&page=2')
  })

  it('maps error envelopes to ApiError with field errors', async () => {
    mockFetch(422, {
      error: { code: 'VALIDATION_ERROR', message: 'Request validation failed', request_id: 'r1',
        details: [{ field: 'email', message: 'Value error, bad email' }] },
    })
    const err = await api.login({ email: 'x', password: 'y' }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe('VALIDATION_ERROR')
    expect(err.requestId).toBe('r1')
    expect(err.fieldErrors()).toEqual({ email: 'bad email' })
  })

  it('invokes the unauthorized handler on 401 when logged in', async () => {
    tokenStore.set('expired')
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    mockFetch(401, { error: { code: 'TOKEN_EXPIRED', message: 'Token expired' } })
    await expect(api.me()).rejects.toBeInstanceOf(ApiError)
    expect(handler).toHaveBeenCalled()
    setUnauthorizedHandler(null)
  })

  it('reports network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    const err = await api.me().catch((e) => e)
    expect(err.code).toBe('NETWORK_ERROR')
  })
})
