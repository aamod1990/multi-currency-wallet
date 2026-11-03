import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

export function useQuote(amount: string, from: string, to: string, enabled: boolean) {
  const a = useDebounced(amount)
  return useQuery({
    queryKey: ['quote', a, from, to],
    queryFn: () => api.quote(a, from, to),
    enabled: enabled && !!a && from !== to,
    staleTime: 30_000,
    retry: false,
  })
}
