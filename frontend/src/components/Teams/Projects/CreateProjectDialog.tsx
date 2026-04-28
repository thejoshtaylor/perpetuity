import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { type ReactNode, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import {
  type ApiError,
  type GitHubAppInstallationPublic,
  type ProjectCreate,
  ProjectsService,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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

const formSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, { message: "Name is required" })
    .max(255, { message: "Name must be 255 characters or fewer" }),
  github_repo_full_name: z
    .string()
    .trim()
    .min(1, { message: "Repository is required" })
    .refine((v) => v.includes("/"), {
      message: "Repository must look like owner/repo",
    })
    .refine(
      (v) => {
        const parts = v.split("/")
        return parts.length === 2 && parts[0].length > 0 && parts[1].length > 0
      },
      { message: "Repository must look like owner/repo" },
    ),
  installation_id: z.string().min(1, { message: "Pick a GitHub installation" }),
})

type FormData = z.infer<typeof formSchema>

type Props = {
  teamId: string
  installations: GitHubAppInstallationPublic[]
  trigger?: ReactNode
  disabled?: boolean
  disabledReason?: string
}

function extractDetail(err: unknown): {
  detail?: string
  status?: number
} {
  const apiErr = err as ApiError | undefined
  const status = apiErr?.status
  const body = apiErr?.body as { detail?: unknown } | undefined
  const detail = body?.detail
  if (typeof detail === "string") return { detail, status }
  if (Array.isArray(detail) && detail.length > 0) {
    return { detail: (detail[0] as { msg?: string })?.msg, status }
  }
  if (apiErr?.message) return { detail: apiErr.message, status }
  return { status }
}

const CreateProjectDialog = ({
  teamId,
  installations,
  trigger,
  disabled = false,
  disabledReason,
}: Props) => {
  const [isOpen, setIsOpen] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      name: "",
      github_repo_full_name: "",
      installation_id: "",
    },
  })

  const mutation = useMutation({
    mutationFn: (data: ProjectCreate) =>
      ProjectsService.createTeamProject({ teamId, requestBody: data }),
    onSuccess: () => {
      form.reset()
      setSubmitError(null)
      setIsOpen(false)
      queryClient.invalidateQueries({
        queryKey: ["team", teamId, "projects"],
      })
    },
    onError: (err) => {
      const { detail, status } = extractDetail(err)
      // 409 project_name_taken → inline form error per Q5 contract.
      if (status === 409 && detail === "project_name_taken") {
        form.setError("name", {
          type: "server",
          message: "That project name is already taken on this team",
        })
        return
      }
      // 404 installation_not_in_team → race condition with uninstall in
      // another tab; refresh installations and surface inline.
      if (status === 404 && detail === "installation_not_in_team") {
        queryClient.invalidateQueries({
          queryKey: ["team", teamId, "github", "installations"],
        })
        setSubmitError(
          "That GitHub installation is no longer linked to this team — refresh and pick another.",
        )
        return
      }
      setSubmitError(detail ?? "Could not create project")
    },
  })

  const onSubmit = (data: FormData) => {
    if (mutation.isPending) return
    setSubmitError(null)
    mutation.mutate({
      name: data.name.trim(),
      github_repo_full_name: data.github_repo_full_name.trim(),
      installation_id: Number(data.installation_id),
    })
  }

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(next) => {
        if (mutation.isPending) return
        setIsOpen(next)
        if (!next) {
          form.reset()
          setSubmitError(null)
        }
      }}
    >
      <DialogTrigger asChild disabled={disabled}>
        {trigger ?? (
          <Button
            type="button"
            data-testid="create-project-button"
            disabled={disabled}
            title={disabled ? disabledReason : undefined}
          >
            New Project
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create project</DialogTitle>
          <DialogDescription>
            Link a GitHub repository to this team. Pick an installation that has
            access to the repo.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <div className="grid gap-4 py-4">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      Name <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        data-testid="create-project-name-input"
                        placeholder="api-server"
                        type="text"
                        autoFocus
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="github_repo_full_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      Repository <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        data-testid="create-project-repo-input"
                        placeholder="owner/repo"
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
                name="installation_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      GitHub installation{" "}
                      <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                      >
                        <SelectTrigger
                          className="w-full"
                          data-testid="create-project-installation-select"
                        >
                          <SelectValue placeholder="Choose an installation" />
                        </SelectTrigger>
                        <SelectContent>
                          {installations.map((inst) => (
                            <SelectItem
                              key={inst.installation_id}
                              value={String(inst.installation_id)}
                              data-testid={`create-project-installation-option-${inst.installation_id}`}
                            >
                              {inst.account_login} ({inst.account_type})
                            </SelectItem>
                          ))}
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
                  data-testid="create-project-error"
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
                data-testid="create-project-submit"
              >
                Create
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export default CreateProjectDialog
