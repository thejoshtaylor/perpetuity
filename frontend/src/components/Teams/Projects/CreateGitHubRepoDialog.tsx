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
        throw new Error(body.detail || "Failed to create repository")
      }
      return res.json()
    },
    onSuccess: (data) => {
      const repoFullName = data.full_name
      form.reset()
      setSubmitError(null)
      onOpenChange(false)
      onSuccess(repoFullName)
    },
    onError: (err) => {
      const message =
        err instanceof Error ? err.message : "Failed to create repository"
      setSubmitError(message)
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
              <LoadingButton
                type="submit"
                loading={mutation.isPending}
                data-testid="create-repo-submit"
              >
                Create Repository
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
