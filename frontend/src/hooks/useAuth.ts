import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import {
  AuthService,
  type LoginBody,
  type SignupBody,
  type UserPublic,
  UsersService,
} from "@/client"
import { handleError } from "@/utils"
import useCustomToast from "./useCustomToast"

// Sanitize the `?next=` redirect target so a hostile invite/login URL cannot
// bounce the user to an external origin (open-redirect). Only same-origin
// relative paths starting with a single slash followed by a non-slash char
// are honored — anything else falls back to "/".
export function sanitizeNextPath(raw: string | null): string {
  if (!raw) return "/"
  // Reject protocol-relative ("//evil.com"), backslash variants, and absolute URLs.
  if (!/^\/[^/\\]/.test(raw)) return "/"
  return raw
}

function readNextFromLocation(): string {
  if (typeof window === "undefined") return "/"
  const next = new URLSearchParams(window.location.search).get("next")
  return sanitizeNextPath(next)
}

const useAuth = () => {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()

  // Server-truth check: ['currentUser'] is the canonical "am I logged in?" probe.
  // retry:false so a 401 falls straight through to main.tsx onError → /login redirect.
  const { data: user } = useQuery<UserPublic | null, Error>({
    queryKey: ["currentUser"],
    queryFn: UsersService.readUserMe,
    retry: false,
  })

  const signUpMutation = useMutation({
    mutationFn: (data: SignupBody) => AuthService.signup({ requestBody: data }),
    onSuccess: () => {
      // Backend signup issues the session cookie — invalidate to refetch the new user.
      queryClient.invalidateQueries({ queryKey: ["currentUser"] })
      const next = readNextFromLocation()
      navigate({ to: next })
    },
    onError: handleError.bind(showErrorToast),
  })

  const loginMutation = useMutation({
    mutationFn: (data: LoginBody) => AuthService.login({ requestBody: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["currentUser"] })
      const next = readNextFromLocation()
      navigate({ to: next })
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = async () => {
    try {
      await AuthService.logout()
    } catch {
      // Logout is idempotent on the backend — even if the call fails, we still
      // clear local cache and redirect so the user is not stuck in a bad state.
    }
    queryClient.removeQueries()
    navigate({ to: "/login" })
  }

  return {
    signUpMutation,
    loginMutation,
    logout,
    user,
  }
}

export default useAuth
