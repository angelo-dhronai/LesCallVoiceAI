import { useQuery } from '@tanstack/react-query'
import { api } from '../client'

export type Me = {
  mode: 'single-tenant' | 'multi-tenant'
  email?: string
  name?: string
  picture?: string
  is_admin?: boolean
  login_required?: boolean
}

/** The signed-in identity from /api/auth/me. In single-tenant (no OAuth)
 * mode the backend reports is_admin:true so all nav shows. */
export function useMe() {
  return useQuery<Me>({
    queryKey: ['auth', 'me'],
    queryFn: () => api<Me>('/auth/me'),
    staleTime: 5 * 60_000,
    retry: false,
  })
}
