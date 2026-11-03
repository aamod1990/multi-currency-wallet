import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App'
import { AuthProvider } from '../auth/AuthContext'

function renderApp(path = '/login') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

afterEach(() => vi.unstubAllGlobals())

it('redirects anonymous users to login', async () => {
  vi.stubGlobal('fetch', vi.fn(() => json(200, [])))
  renderApp('/history')
  expect(await screen.findByText('Welcome back')).toBeInTheDocument()
})

it('shows server error on failed login', async () => {
  vi.stubGlobal('fetch', vi.fn(() =>
    json(401, { error: { code: 'INVALID_CREDENTIALS', message: 'Invalid email or password' } })))
  renderApp()
  await userEvent.type(screen.getByLabelText('Email'), 'a@b.co')
  await userEvent.type(screen.getByLabelText('Password'), 'wrongpass1')
  await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password')
})

it('validates signup form client-side before calling the API', async () => {
  const fetchFn = vi.fn((_url: string) => json(200, ['USD', 'EUR']))
  vi.stubGlobal('fetch', fetchFn)
  renderApp('/signup')
  await userEvent.type(screen.getByLabelText('Password'), 'short')
  await userEvent.click(screen.getByRole('button', { name: 'Sign up' }))
  expect(await screen.findByText('Full name is required')).toBeInTheDocument()
  expect(screen.getByText('At least 8 characters')).toBeInTheDocument()
  await waitFor(() =>
    expect(fetchFn.mock.calls.every(([u]) => !String(u).includes('/auth/signup'))).toBe(true))
})

it('logs in and lands on the wallets dashboard', async () => {
  const user = { id: '1', email: 'a@b.co', full_name: 'Ann Lee', default_currency: 'USD', has_photo: false, created_at: '' }
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    if (url.includes('/auth/login')) return json(200, { access_token: 't', token_type: 'bearer', expires_in: 3600, user })
    if (url.includes('/wallets')) return json(200, { wallets: [{ id: 'w', currency: 'USD', balance: '12.5', updated_at: '' }], total: '12.5', total_currency: 'USD', rates_as_of: null })
    return json(200, ['USD', 'EUR'])
  }))
  renderApp()
  await userEvent.type(screen.getByLabelText('Email'), 'a@b.co')
  await userEvent.type(screen.getByLabelText('Password'), 'Passw0rd!')
  await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
  expect(await screen.findByText('Ann Lee')).toBeInTheDocument()
  expect((await screen.findAllByText('$12.50')).length).toBeGreaterThan(0)
})
