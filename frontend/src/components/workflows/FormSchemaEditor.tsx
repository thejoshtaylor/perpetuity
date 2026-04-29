// M005-sqm8et/S03/T05 — repeating-row editor for workflow form_schema fields

import { Plus, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export type FormField = {
  name: string
  label: string
  kind: "string" | "text" | "number" | "boolean"
  required: boolean
}

type Props = {
  fields: FormField[]
  onChange: (fields: FormField[]) => void
}

export function FormSchemaEditor({ fields, onChange }: Props) {
  const addField = () => {
    onChange([
      ...fields,
      { name: "", label: "", kind: "string", required: false },
    ])
  }

  const removeField = (index: number) => {
    onChange(fields.filter((_, i) => i !== index))
  }

  const updateField = (index: number, patch: Partial<FormField>) => {
    onChange(
      fields.map((f, i) => (i === index ? { ...f, ...patch } : f)),
    )
  }

  return (
    <div className="flex flex-col gap-3" data-testid="form-schema-editor">
      {fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No form fields — trigger will dispatch without a modal.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {fields.map((field, i) => (
            <li
              key={i}
              className="flex flex-wrap items-end gap-2 rounded border p-3"
              data-testid={`form-field-row-${i}`}
            >
              <div className="flex flex-col gap-1 flex-1 min-w-[8rem]">
                <Label htmlFor={`field-name-${i}`}>Name</Label>
                <Input
                  id={`field-name-${i}`}
                  data-testid={`field-name-${i}`}
                  value={field.name}
                  placeholder="branch"
                  onChange={(e) => updateField(i, { name: e.target.value })}
                />
              </div>
              <div className="flex flex-col gap-1 flex-1 min-w-[8rem]">
                <Label htmlFor={`field-label-${i}`}>Label</Label>
                <Input
                  id={`field-label-${i}`}
                  data-testid={`field-label-${i}`}
                  value={field.label}
                  placeholder="Branch name"
                  onChange={(e) => updateField(i, { label: e.target.value })}
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor={`field-kind-${i}`}>Kind</Label>
                <Select
                  value={field.kind}
                  onValueChange={(v) =>
                    updateField(i, {
                      kind: v as FormField["kind"],
                    })
                  }
                >
                  <SelectTrigger
                    id={`field-kind-${i}`}
                    data-testid={`field-kind-${i}`}
                    className="w-28"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="string">string</SelectItem>
                    <SelectItem value="text">text</SelectItem>
                    <SelectItem value="number">number</SelectItem>
                    <SelectItem value="boolean">boolean</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center gap-1.5 pb-1">
                <Checkbox
                  id={`field-required-${i}`}
                  data-testid={`field-required-${i}`}
                  checked={field.required}
                  onCheckedChange={(v) =>
                    updateField(i, { required: Boolean(v) })
                  }
                />
                <Label htmlFor={`field-required-${i}`} className="text-sm">
                  Required
                </Label>
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                data-testid={`field-remove-${i}`}
                onClick={() => removeField(i)}
                aria-label="Remove field"
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        data-testid="add-form-field"
        onClick={addField}
        className="self-start"
      >
        <Plus className="h-4 w-4" />
        Add field
      </Button>
    </div>
  )
}

export default FormSchemaEditor
