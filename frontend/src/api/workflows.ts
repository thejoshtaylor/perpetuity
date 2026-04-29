import {
  type WorkflowPublic,
  type WorkflowRunPublic,
  type WorkflowsPublic,
  WorkflowsService,
} from "@/client"

/**
 * Query helpers for the M005/S02 dashboard surfaces (DirectAIButtons +
 * the `/runs/$runId` polled detail page). Mirrors `api/teamSecrets.ts`:
 * stable query keys + factory `*QueryOptions` so both the route and the
 * components share invalidation handles.
 */

export const teamWorkflowsQueryKey = (teamId: string) =>
  ["team", teamId, "workflows"] as const

export function teamWorkflowsQueryOptions(teamId: string) {
  return {
    queryKey: teamWorkflowsQueryKey(teamId),
    queryFn: async (): Promise<WorkflowsPublic> => {
      const res = await WorkflowsService.listTeamWorkflows({ teamId })
      return res as unknown as WorkflowsPublic
    },
  }
}

export const workflowRunQueryKey = (runId: string) =>
  ["workflow_run", runId] as const

export function workflowRunQueryOptions(runId: string) {
  return {
    queryKey: workflowRunQueryKey(runId),
    queryFn: async (): Promise<WorkflowRunPublic> => {
      const res = await WorkflowsService.getWorkflowRun({ runId })
      return res as unknown as WorkflowRunPublic
    },
  }
}

/** Names of the auto-seeded direct-AI workflows. The dashboard buttons
 * resolve workflow ids by looking these up in the team's workflow list. */
export const DIRECT_AI_WORKFLOW_NAMES = {
  claude: "_direct_claude",
  codex: "_direct_codex",
} as const

export type DirectAIKind = keyof typeof DIRECT_AI_WORKFLOW_NAMES

export function findDirectAIWorkflow(
  workflows: WorkflowPublic[],
  kind: DirectAIKind,
): WorkflowPublic | undefined {
  const name = DIRECT_AI_WORKFLOW_NAMES[kind]
  return workflows.find((w) => w.name === name && w.system_owned)
}

/** Run is still active — keep polling. */
export function isRunInFlight(status: string): boolean {
  return status === "pending" || status === "running"
}
