// M005-sqm8et/S03/T05 — shared editor form for create + update workflow

import { useEffect, useState } from "react"
import { toast } from "sonner"
import type { ApiError, WorkflowWithStepsPublic } from "@/client"
import { WorkflowsService, TeamsService } from "@/client"
import { teamWorkflowsQueryKey } from "@/api/workflows"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  FormSchemaEditor,
  type FormField,
} from "@/components/workflows/FormSchemaEditor"
import {
  StepsEditor,
  type WorkflowStep,
} from "@/components/workflows/StepsEditor"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

type Scope = "user" | "team_specific" | "round_robin"

function extractDetail(err: unknown): string | undefined {
  const apiErr = err as ApiError | undefined
  const body = apiErr?.body as { detail?: unknown } | undefined
  const detail = body?.detail
  if (typeof detail === "string") return detail
  if (detail && typeof detail === "object") {
    const inner = detail as { detail?: unknown; reason?: unknown }
    if (typeof inner.detail === "string") return inner.detail
    if (typeof inner.reason === "string") return inner.reason
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return (detail[0] as { msg?: string })?.msg
  }
  return (err as Error)?.message
}

function formFieldsToSchema(fields: FormField[]): Record<string, unknown> {
  if (fields.length === 0) return {}
  return {
    fields: fields.map((f) => ({
      name: f.name,
      label: f.label,
      kind: f.kind,
      required: f.required,
    })),
  }
}

function schemaToFormFields(schema: Record<string, unknown> | undefined): FormField[] {
  if (!schema || !Array.isArray(schema.fields)) return []
  return (schema.fields as Array<Record<string, unknown>>).map((f) => ({
    name: String(f.name ?? ""),
    label: String(f.label ?? ""),
    kind: (f.kind as FormField["kind"]) ?? "string",
    required: Boolean(f.required),
  }))
}

type Props = {
  teamId: string
  existingWorkflow?: WorkflowWithStepsPublic
  onSaved?: (wf: WorkflowWithStepsPublic) => void
}

export function WorkflowEditor({ teamId, existingWorkflow, onSaved }: Props) {
  const isEdit = Boolean(existingWorkflow)
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [name, setName] = useState(existingWorkflow?.name ?? "")
  const [description, setDescription] = useState(
    existingWorkflow?.description ?? "",
  )
  const [scope, setScope] = useState<Scope>(
    (existingWorkflow?.scope as Scope | undefined) ?? "user",
  )
  const [targetUserId, setTargetUserId] = useState<string>(
    existingWorkflow?.target_user_id ?? "",
  )
  const [formFields, setFormFields] = useState<FormField[]>(
    schemaToFormFields(existingWorkflow?.form_schema as Record<string, unknown>),
  )
  const [steps, setSteps] = useState<WorkflowStep[]>(
    (existingWorkflow?.steps ?? []).map((s) => ({
      step_index: s.step_index,
      action: s.action as WorkflowStep["action"],
      config: (s.config as Record<string, unknown>) ?? {},
      target_container: "user_workspace",
    })),
  )

  // Load team members when scope=team_specific so we can render the selector
  const membersQuery = useQuery({
    queryKey: ["team", teamId, "members"],
    queryFn: async () => TeamsService.readTeamMembers({ teamId }),
    enabled: scope === "team_specific",
  })

  // Reset target_user_id when scope changes away from team_specific
  useEffect(() => {
    if (scope !== "team_specific") setTargetUserId("")
  }, [scope])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const form_schema = formFieldsToSchema(formFields)
      const stepsPayload = steps.map((s) => ({
        step_index: s.step_index,
        action: s.action,
        config: s.config,
        target_container: s.target_container,
      }))
      if (isEdit && existingWorkflow) {
        return WorkflowsService.updateWorkflow({
          workflowId: existingWorkflow.id,
          requestBody: {
            name,
            description: description || null,
            scope,
            target_user_id: targetUserId || null,
            form_schema,
            steps: stepsPayload,
          },
        })
      }
      return WorkflowsService.createWorkflow({
        teamId,
        requestBody: {
          name,
          description: description || null,
          scope,
          target_user_id: targetUserId || null,
          form_schema,
          steps: stepsPayload,
        },
      })
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: teamWorkflowsQueryKey(teamId) })
      toast.success(isEdit ? "Workflow updated" : "Workflow created")
      onSaved?.(data)
      navigate({
        to: "/workflows",
        search: { teamId, admin: "true" },
      })
    },
    onError: (err) => {
      toast.error(isEdit ? "Save failed" : "Create failed", {
        description: extractDetail(err) ?? "Unknown error",
      })
    },
  })

  const members =
    (membersQuery.data as unknown as { data: Array<{ user_id: string; full_name?: string | null; email?: string }> } | undefined)
      ?.data ?? []

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        saveMutation.mutate()
      }}
      className="flex flex-col gap-5"
      data-testid="workflow-editor-form"
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="workflow-name">
          Name <span className="text-destructive">*</span>
        </Label>
        <Input
          id="workflow-name"
          data-testid="workflow-name-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-workflow"
          required
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="workflow-description">Description</Label>
        <Textarea
          id="workflow-description"
          data-testid="workflow-description-input"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What does this workflow do?"
          rows={3}
          voice={false}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="workflow-scope">Scope</Label>
        <Select
          value={scope}
          onValueChange={(v) => setScope(v as Scope)}
        >
          <SelectTrigger
            id="workflow-scope"
            data-testid="workflow-scope-select"
            className="w-48"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="user">user</SelectItem>
            <SelectItem value="team_specific">team_specific</SelectItem>
            <SelectItem value="round_robin">round_robin</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {scope === "team_specific" && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="workflow-target-user">Target member</Label>
          <Select
            value={targetUserId}
            onValueChange={setTargetUserId}
          >
            <SelectTrigger
              id="workflow-target-user"
              data-testid="workflow-target-user-select"
              className="w-64"
            >
              <SelectValue placeholder="Select team member…" />
            </SelectTrigger>
            <SelectContent>
              {members.map((m) => (
                <SelectItem key={m.user_id} value={m.user_id}>
                  {m.full_name ?? m.email ?? m.user_id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-medium">Form fields</h2>
        <p className="text-xs text-muted-foreground">
          Fields shown to the user before dispatch. Leave empty for direct
          dispatch (no modal).
        </p>
        <FormSchemaEditor fields={formFields} onChange={setFormFields} />
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-medium">Steps</h2>
        <StepsEditor steps={steps} onChange={setSteps} />
      </section>

      <div className="flex gap-3 pt-2">
        <LoadingButton
          type="submit"
          loading={saveMutation.isPending}
          data-testid="workflow-save-button"
        >
          {isEdit ? "Save changes" : "Create workflow"}
        </LoadingButton>
        <Button
          type="button"
          variant="outline"
          data-testid="workflow-cancel-button"
          onClick={() =>
            navigate({ to: "/workflows", search: { teamId, admin: "true" } })
          }
        >
          Cancel
        </Button>
      </div>
    </form>
  )
}

export default WorkflowEditor
