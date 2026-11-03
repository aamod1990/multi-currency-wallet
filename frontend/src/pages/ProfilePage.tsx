import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Avatar } from '../components/Avatar'
import { CurrencySelect, ErrorBanner, Field, Success } from '../components/ui'

const MAX_BYTES = 2 * 1024 * 1024

export function ProfilePage() {
  const { user, setUser } = useAuth()
  const qc = useQueryClient()
  const [name, setName] = useState(user?.full_name ?? '')
  const [currency, setCurrency] = useState(user?.default_currency ?? 'USD')
  const [photoVersion, setPhotoVersion] = useState(0)
  const [photoError, setPhotoError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const save = useMutation({
    mutationFn: () => api.updateMe({ full_name: name, default_currency: currency }),
    onSuccess: (u) => { setUser(u); qc.invalidateQueries({ queryKey: ['wallets'] }) },
  })
  const upload = useMutation({
    mutationFn: (f: File) => api.uploadPhoto(f),
    onSuccess: (u) => { setUser(u); setPhotoVersion((v) => v + 1) },
  })
  const remove = useMutation({
    mutationFn: api.deletePhoto,
    onSuccess: () => user && setUser({ ...user, has_photo: false }),
  })
  if (!user) return null

  function onFile(f: File | undefined) {
    setPhotoError(null)
    if (!f) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(f.type)) return setPhotoError('Use a JPEG, PNG or WEBP image')
    if (f.size > MAX_BYTES) return setPhotoError('Image must be 2 MB or smaller')
    upload.mutate(f)
  }

  return (
    <div className="stack narrow">
      <h1>Profile</h1>
      <section className="card profile-photo">
        <Avatar user={user} size={96} version={photoVersion} />
        <div className="stack-sm">
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={(e) => onFile(e.target.files?.[0])} aria-label="photo" />
          <button className="btn btn-primary" onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
            {upload.isPending ? 'Uploading…' : user.has_photo ? 'Change photo' : 'Upload photo'}
          </button>
          {user.has_photo && <button className="btn btn-ghost" onClick={() => remove.mutate()}>Remove</button>}
          <span className="muted small">JPEG, PNG or WEBP, up to 2 MB.</span>
          {photoError && <div className="field-error">{photoError}</div>}
          <ErrorBanner error={upload.error || remove.error} />
        </div>
      </section>
      <form className="card form-grid" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate() }}>
        <Field label="Email"><input value={user.email} disabled /></Field>
        <Field label="Full name" error={!name.trim() ? 'Full name is required' : null}>
          <input value={name} onChange={(e) => { setName(e.target.value); save.reset() }} maxLength={120} />
        </Field>
        <Field label="Default currency">
          <CurrencySelect name="default_currency" value={currency} onChange={(v) => { setCurrency(v); save.reset() }} />
        </Field>
        <div className="muted small">Incoming transfers are credited in your default currency; totals are shown in it.</div>
        <div className="form-actions">
          <button className="btn btn-primary" disabled={save.isPending || !name.trim()}>{save.isPending ? 'Saving…' : 'Save changes'}</button>
        </div>
        <ErrorBanner error={save.error} />
        {save.isSuccess && <Success>Profile updated.</Success>}
      </form>
    </div>
  )
}
