import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export function useCurrencies() {
  return useQuery({ queryKey: ['currencies'], queryFn: api.currencies, staleTime: 3_600_000 })
}
