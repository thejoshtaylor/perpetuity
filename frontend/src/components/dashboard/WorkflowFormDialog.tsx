// M005-sqm8et/S03/T05 — dynamic form dialog for custom workflow dispatch

import { useEffect, useState } from "react"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import { Textarea } from "@/components/ui/textarea"
import { Checkbox } from "@/components/ui/checkbox"

type FormSchemaField = {
  name: string
  label: string
  kind: "string" | "text" | "number" | "boolean"
  required: boolean
}

function parseFormSchema(schema: Record<string, unknown> | undefined): FormSchemaField[] {
  if (!schema || !Array.isArray(schema.fields)) return []
  return (schema.fields as Array<Record<string, unknown>>).map((f) => ({
    name: String(f.name ?? ""),
    label: String(f.label ?? ""),
    kind: (f.kind as FormSchemaField["kind"]) ?? "string",
    required: Boolean(f.required),
  }))
}

type WorkflowLike = {
  id: string
  name: string
  description?: string | null
  form_schema?: Record<string, unknown>
}

type Props = {
  workflow: WorkflowLike
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (payload: Record<string, unknown>) => void
  isPending: boolean
  serverError?: string | null
}

export function WorkflowFormDialog({
  workflow,
  open,
  onOpenChange,
  onSubmit,
  isPending,
  serverError,
}: Props) {
  const fields = parseFormSchema(workflow.form_schema as Record<string, unknown>)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) {
      setValues({})
      setTouched(false)
    }
  }, [open])

  const setValue = (name: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [name]: value }))
    if (!touched) setTouched(true)
  }

  const missingRequired = fields
    .filter((f) => f.required)
    .filter((f) => {
      const v = values[f.name]
      return v === undefined || v === null || v === ""
    })
    .map((f) => f.label || f.name)

  const clientError =
    touched && missingRequired.length > 0
      ? `Required: ${missingRequired.join(", ")}`
      : null

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (missingRequired.length > 0) return
    onSubmit(values)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-lg"
        data-testid={`workflow-form-dialog-${workflow.id}`}
      >
        <DialogHeader>
          <DialogTitle>{workflow.name}</DialogTitle>
          <DialogDescription>
            {workflow.description ?? "Fill in the fields to start this workflow."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit}>
          <div className="grid gap-4 py-4">
            {fields.map((field) => (
              <div key={field.name} className="flex flex-col gap-1.5">
                <Label htmlFor={`wf-field-${workflow.id}-${field.name}`}>
                  {field.label || field.name}
                  {field.required && (
                    <span className="text-destructive ml-1">*</span>
                  )}
                </Label>
                {field.kind === "boolean" ? (
                  <Checkbox
                    id={`wf-field-${workflow.id}-${field.name}`}
                    data-testid={`wf-field-${field.name}`}
                    checked={Boolean(values[field.name])}
                    onCheckedChange={(v) => setValue(field.name, Boolean(v))}
                  />
                ) : field.kind === "text" ? (
                  <Textarea
                    id={`wf-field-${workflow.id}-${field.name}`}
                    data-testid={`wf-field-${field.name}`}
                    voice={false}
                    value={String(values[field.name] ?? "")}
                    onChange={(e) => setValue(field.name, e.target.value)}
                    rows={3}
                  />
                ) : (
                  <Input
                    id={`wf-field-${workflow.id}-${field.name}`}
                    data-testid={`wf-field-${field.name}`}
                    type={field.kind === "number" ? "number" : "text"}
                    value={String(values[field.name] ?? "")}
                    onChange={(e) =>
                      setValue(
                        field.name,
                        field.kind === "number"
                          ? Number(e.target.value)
                          : e.target.value,
                      )
                    }
                  />
                )}
              </div>
            ))}

            {(clientError || serverError) && (
              <p
                className="text-destructive text-sm"
                data-testid="workflow-form-dialog-error"
              >
                {clientError ?? serverError}
              </p>
            )}
          </div>

          <DialogFooter>
            <DialogClose asChild>
              <Button
                type="button"
                variant="outline"
                disabled={isPending}
                data-testid={`workflow-form-dialog-cancel-${workflow.id}`}
              >
                Cancel
              </Button>
            </DialogClose>
            <LoadingButton
              type="submit"
              loading={isPending}
              data-testid={`workflow-form-dialog-submit-${workflow.id}`}
            >
              Run
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default WorkflowFormDialog
