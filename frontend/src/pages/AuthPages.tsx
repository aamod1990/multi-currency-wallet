import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { CurrencySelect, ErrorBanner, Field } from '../components/ui'

export function LoginPage() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={submit} noValidate>
        <h1>Welcome back</h1>
        <p className="muted">Log in to manage your wallets.</p>
        <ErrorBanner error={error} />
        <Field label="Email">
          <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </Field>
        <Field label="Password">
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </Field>
        <button className="btn btn-primary btn-block" disabled={busy || !email || !password}>
          {busy ? 'Logging in…' : 'Log in'}
        </button>
        <p className="muted small">
          No account? <Link to="/signup">Sign up</Link>
        </p>
      </form>
    </div>
  )
}

export function SignupPage() {
  const { signup } = useAuth()
  const [form, setForm] = useState({ email: '', password: '', full_name: '', default_currency: 'USD' })
  const [error, setError] = useState<unknown>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof form) => (v: string) => setForm((f) => ({ ...f, [k]: v }))

  function validate(): Record<string, string> {
    const errs: Record<string, string> = {}
    if (!form.full_name.trim()) errs.full_name = 'Full name is required'
    if (!/^\S+@\S+\.\S+$/.test(form.email)) errs.email = 'Enter a valid email'
    if (form.password.length < 8) errs.password = 'At least 8 characters'
    else if (!/[A-Za-z]/.test(form.password) || !/\d/.test(form.password))
      errs.password = 'Use at least one letter and one digit'
    return errs
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const errs = validate()
    setFieldErrors(errs)
    if (Object.keys(errs).length) return
    setBusy(true)
    setError(null)
    try {
      await signup(form)
    } catch (err) {
      if (err instanceof ApiError) setFieldErrors(err.fieldErrors())
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={submit} noValidate>
        <h1>Create your account</h1>
        <p className="muted">Hold, convert and send money in 30+ currencies.</p>
        <ErrorBanner error={error} />
        <Field label="Full name" error={fieldErrors.full_name}>
          <input value={form.full_name} onChange={(e) => set('full_name')(e.target.value)} autoComplete="name" />
        </Field>
        <Field label="Email" error={fieldErrors.email}>
          <input type="email" value={form.email} onChange={(e) => set('email')(e.target.value)} autoComplete="email" />
        </Field>
        <Field label="Password" error={fieldErrors.password}>
          <input type="password" value={form.password} onChange={(e) => set('password')(e.target.value)} autoComplete="new-password" />
        </Field>
        <Field label="Default currency">
          <CurrencySelect name="default_currency" value={form.default_currency} onChange={set('default_currency')} />
        </Field>
        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? 'Creating account…' : 'Sign up'}
        </button>
        <p className="muted small">
          Already registered? <Link to="/login">Log in</Link>
        </p>
      </form>
    </div>
  )
}
