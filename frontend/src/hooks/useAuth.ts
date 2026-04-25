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
      navigate({ to: "/" })
    },
    onError: handleError.bind(showErrorToast),
  })

  const loginMutation = useMutation({
    mutationFn: (data: LoginBody) => AuthService.login({ requestBody: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["currentUser"] })
      navigate({ to: "/" })
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
