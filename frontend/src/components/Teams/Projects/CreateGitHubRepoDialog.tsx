import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// ---------------------------------------------------------------------------
// Typed error for the 409 github_user_token_required path
// ---------------------------------------------------------------------------

export class GitHubUserTokenRequiredError extends Error {
  installationId: number
  reason: string

  constructor(installationId: number, reason: string) {
    super("GitHub user token required")
    this.name = "GitHubUserTokenRequiredError"
    this.installationId = installationId
    this.reason = reason
  }
}

// ---------------------------------------------------------------------------
// ReinstallCta — colocated inline component
// ---------------------------------------------------------------------------

type ReinstallCtaProps = {
  teamId: string
}

const ReinstallCta = ({ teamId }: ReinstallCtaProps) => {
  const [installUrlError, setInstallUrlError] = useState<string | null>(null)

  const installUrlMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`/api/v1/teams/${teamId}/github/install-url`)
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(
          (body as { detail?: string }).detail || "Failed to get install URL",
        )
      }
      return res.json() as Promise<{ install_url: string }>
    },
    onSuccess: (data) => {
      setInstallUrlError(null)
      window.open(data.install_url, "_blank", "noopener,noreferrer")
    },
    onError: (err) => {
      const message =
        err instanceof Error ? err.message : "Failed to get install URL"
      setInstallUrlError(message)
    },
  })

  return (
    <div
      className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm dark:border-amber-800 dark:bg-amber-950"
      data-testid="create-repo-reinstall-cta"
    >
      <p className="font-medium text-amber-900 dark:text-amber-100">
        GitHub access required
      </p>
      <p className="mt-1 text-amber-800 dark:text-amber-200">
        Perpetuity needs permission to create repositories on your behalf.
        Reinstall the Perpetuity App on GitHub to grant repo creation access.
      </p>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="mt-3"
        disabled={installUrlMutation.isPending}
        onClick={() => {
          setInstallUrlError(null)
          installUrlMutation.mutate()
        }}
        data-testid="create-repo-reinstall-button"
      >
        {installUrlMutation.isPending ? "Opening…" : "Reinstall on GitHub"}
      </Button>
      {installUrlError && (
        <p
          className="mt-2 text-destructive text-sm"
          data-testid="create-repo-reinstall-error"
          role="alert"
        >
          {installUrlError}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Form schema
// ---------------------------------------------------------------------------

const repoFormSchema = z.object({
  repo_name: z
    .string()
    .trim()
    .min(1, { message: "Repository name is required" })
    .max(255, { message: "Repository name must be 255 characters or fewer" })
    .regex(/^[a-zA-Z0-9._-]+$/, {
      message:
        "Repository name can only contain letters, numbers, dots, hyphens, and underscores",
    }),
  description: z
    .string()
    .trim()
    .max(255, { message: "Description must be 255 characters or fewer" })
    .optional(),
  visibility: z.enum(["public", "private"]),
})

type RepoFormData = z.infer<typeof repoFormSchema>

type CreateGitHubRepoDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  installationId: string
  teamId: string
  accountLogin: string
  onSuccess: (repoFullName: string) => void
}

export const CreateGitHubRepoDialog = ({
  open,
  onOpenChange,
  installationId,
  teamId,
  accountLogin,
  onSuccess,
}: CreateGitHubRepoDialogProps) => {
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [tokenRequired, setTokenRequired] = useState(false)

  const form = useForm<RepoFormData>({
    resolver: zodResolver(repoFormSchema),
    mode: "onBlur",
    defaultValues: {
      repo_name: "",
      description: "",
      visibility: "private",
    },
  })

  const mutation = useMutation({
    mutationFn: async (data: RepoFormData) => {
      const res = await fetch(
        `/api/v1/teams/${teamId}/github/installations/${installationId}/create-repository`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            repo_name: data.repo_name,
            description: data.description || null,
            private: data.visibility === "private",
          }),
        },
      )
      if (!res.ok) {
        const body = await res.json()
        if (
          res.status === 409 &&
          body.detail?.code === "github_user_token_required"
        ) {
          console.warn("github_user_token_required", {
            installationId: body.detail?.installation_id,
            reason: body.detail?.reason,
          })
          throw new GitHubUserTokenRequiredError(
            body.detail?.installation_id,
            body.detail?.reason,
          )
        }
        if (
          res.status === 502 &&
          body.detail === "github_token_refresh_transient"
        ) {
          throw new Error(
            "GitHub had a temporary problem. Try again in a moment.",
          )
        }
        if (
          res.status === 503 &&
          body.detail === "github_user_token_decrypt_failed"
        ) {
          throw new Error(
            "A configuration error prevented repo creation. The operator has been notified.",
          )
        }
        throw new Error(body.detail || "Failed to create repository")
      }
      return res.json()
    },
    onSuccess: (data) => {
      const repoFullName = data.full_name
      form.reset()
      setSubmitError(null)
      setTokenRequired(false)
      onOpenChange(false)
      onSuccess(repoFullName)
    },
    onError: (err) => {
      if (err instanceof GitHubUserTokenRequiredError) {
        setTokenRequired(true)
        setSubmitError(null)
      } else {
        setTokenRequired(false)
        const message =
          err instanceof Error ? err.message : "Failed to create repository"
        setSubmitError(message)
      }
    },
  })

  const onSubmit = (data: RepoFormData) => {
    if (mutation.isPending) return
    setSubmitError(null)
    mutation.mutate(data)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (mutation.isPending) return
        onOpenChange(next)
        if (!next) {
          form.reset()
          setSubmitError(null)
          setTokenRequired(false)
        }
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create new GitHub repository</DialogTitle>
          <DialogDescription>
            Create a new repository on {accountLogin} and add it to your
            project.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <div className="grid gap-4 py-4">
              <FormField
                control={form.control}
                name="repo_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      Repository name{" "}
                      <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        data-testid="create-repo-name-input"
                        placeholder="my-repository"
                        type="text"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="description"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Description</FormLabel>
                    <FormControl>
                      <Input
                        data-testid="create-repo-description-input"
                        placeholder="Repository description (optional)"
                        type="text"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="visibility"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      Visibility <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                      >
                        <SelectTrigger
                          className="w-full"
                          data-testid="create-repo-visibility-select"
                        >
                          <SelectValue placeholder="Choose visibility" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem
                            value="private"
                            data-testid="create-repo-visibility-private"
                          >
                            Private
                          </SelectItem>
                          <SelectItem
                            value="public"
                            data-testid="create-repo-visibility-public"
                          >
                            Public
                          </SelectItem>
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {tokenRequired && <ReinstallCta teamId={teamId} />}

              {submitError && (
                <p
                  className="text-destructive text-sm"
                  data-testid="create-repo-error"
                  role="alert"
                >
                  {submitError}
                </p>
              )}
            </div>

            <DialogFooter>
              <DialogClose asChild>
                <Button
                  type="button"
                  variant="outline"
                  disabled={mutation.isPending}
                >
                  Cancel
                </Button>
              </DialogClose>
              {!tokenRequired ? (
                <LoadingButton
                  type="submit"
                  loading={mutation.isPending}
                  data-testid="create-repo-submit"
                >
                  Create Repository
                </LoadingButton>
              ) : null}
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
