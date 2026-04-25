import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { type ReactNode, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { type TeamCreate, TeamsService } from "@/client"
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
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const formSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, { message: "Name is required" })
    .max(255, { message: "Name must be 255 characters or fewer" }),
})

type FormData = z.infer<typeof formSchema>

type Props = {
  trigger?: ReactNode
}

const CreateTeamDialog = ({ trigger }: Props) => {
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: { name: "" },
  })

  const mutation = useMutation({
    mutationFn: (data: TeamCreate) =>
      TeamsService.createTeam({ requestBody: data }),
    onSuccess: () => {
      showSuccessToast("Team created")
      form.reset()
      setIsOpen(false)
      queryClient.invalidateQueries({ queryKey: ["teams"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const onSubmit = (data: FormData) => {
    if (mutation.isPending) return
    mutation.mutate(data)
  }

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(next) => {
        if (mutation.isPending) return
        setIsOpen(next)
        if (!next) form.reset()
      }}
    >
      <DialogTrigger asChild>
        {trigger ?? (
          <Button data-testid="create-team-button">
            <Plus className="mr-2" />
            Create Team
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create Team</DialogTitle>
          <DialogDescription>
            Name your new team. You'll be added as the admin.
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
                        data-testid="create-team-name-input"
                        placeholder="Acme Engineering"
                        type="text"
                        autoFocus
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  Cancel
                </Button>
              </DialogClose>
              <LoadingButton
                type="submit"
                loading={mutation.isPending}
                data-testid="create-team-submit"
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

export default CreateTeamDialog
