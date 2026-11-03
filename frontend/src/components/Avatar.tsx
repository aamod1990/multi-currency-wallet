import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { User } from '../api/types'

/** Photos are auth-protected, so fetch with the bearer token and show a blob URL. */
export function Avatar({ user, size = 36, version = 0 }: { user: User; size?: number; version?: number }) {
  const [src, setSrc] = useState<string | null>(null)
  useEffect(() => {
    if (!user.has_photo) return
    let url: string | null = null
    let cancelled = false
    api
      .photo()
      .then((r) => r.blob())
      .then((b) => {
        if (cancelled) return
        url = URL.createObjectURL(b)
        setSrc(url)
      })
      .catch(() => setSrc(null))
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [user.has_photo, version])

  const initials = user.full_name
    .split(/\s+/)
    .map((p) => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
  const shown = user.has_photo ? src : null
  return shown ? (
    <img className="avatar" src={shown} alt={user.full_name} style={{ width: size, height: size }} />
  ) : (
    <span className="avatar avatar-fallback" style={{ width: size, height: size, fontSize: size / 2.6 }}>
      {initials}
    </span>
  )
}
