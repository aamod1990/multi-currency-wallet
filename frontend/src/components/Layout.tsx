import { NavLink, Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { Avatar } from './Avatar'

export function ProtectedLayout() {
  const { user, loading, logout } = useAuth()
  if (loading) return <div className="center muted">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">◎ Multi-Currency Wallet</div>
        <nav>
          <NavLink to="/" end>Wallets</NavLink>
          <NavLink to="/transfer">Transfer</NavLink>
          <NavLink to="/history">History</NavLink>
          <NavLink to="/profile">Profile</NavLink>
        </nav>
        <div className="user-menu">
          <Avatar user={user} size={32} />
          <span className="hide-sm">{user.full_name}</span>
          <button className="btn btn-ghost" onClick={logout}>
            Log out
          </button>
        </div>
      </header>
      <main className="container">
        <Outlet />
      </main>
    </div>
  )
}

export function PublicOnly({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <div className="center muted">Loading…</div>
  return user ? <Navigate to="/" replace /> : <>{children}</>
}
