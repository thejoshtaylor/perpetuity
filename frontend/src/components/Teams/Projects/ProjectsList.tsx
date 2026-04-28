import { useQuery } from "@tanstack/react-query"
import { Github, Plus } from "lucide-react"
import { useState } from "react"

import {
  type ApiError,
  type GitHubAppInstallationPublic,
  GithubService,
  type ProjectPublic,
  ProjectsService,
} from "@/client"
import CreateProjectDialog from "@/components/Teams/Projects/CreateProjectDialog"
import OpenProjectButton from "@/components/Teams/Projects/OpenProjectButton"
import PushRuleForm from "@/components/Teams/Projects/PushRuleForm"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

type Props = {
  teamId: string
  callerIsAdmin: boolean
}

type ProjectsEnvelope = {
  data: ProjectPublic[]
  count: number
}

type InstallationsEnvelope = {
  data: GitHubAppInstallationPublic[]
  count: number
}

export function projectsQueryOptions(teamId: string) {
  return {
    queryKey: ["team", teamId, "projects"] as const,
    queryFn: async (): Promise<ProjectsEnvelope> => {
      const res = await ProjectsService.listTeamProjects({ teamId })
      return res as unknown as ProjectsEnvelope
    },
  }
}

function installationsQueryOptions(teamId: string) {
  return {
    queryKey: ["team", teamId, "github", "installations"] as const,
    queryFn: async (): Promise<InstallationsEnvelope> => {
      const res = await GithubService.listGithubInstallations({ teamId })
      return res as unknown as InstallationsEnvelope
    },
  }
}

function extractDetail(err: unknown): string | undefined {
  const apiErr = err as ApiError | undefined
  const body = apiErr?.body as { detail?: unknown } | undefined
  const detail = body?.detail
  if (typeof detail === "string") return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return (detail[0] as { msg?: string })?.msg
  }
  if (apiErr?.message) return apiErr.message
  return undefined
}

function statusBadgeVariant(
  status: string | null | undefined,
): "default" | "secondary" | "destructive" | "outline" {
  if (status === "ok") return "default"
  if (status === "failed") return "destructive"
  return "outline"
}

function statusBadgeLabel(status: string | null | undefined): string {
  if (!status) return "no pushes"
  return status
}

function ProjectRow({
  project,
  callerIsAdmin,
}: {
  project: ProjectPublic
  callerIsAdmin: boolean
}) {
  const [pushRuleOpen, setPushRuleOpen] = useState(false)

  return (
    <li
      data-testid={`project-row-${project.id}`}
      data-project-id={project.id}
      className="flex flex-col gap-3 p-3"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <Github className="h-4 w-4 shrink-0 text-muted-foreground" />
          <div className="flex flex-col min-w-0">
            <span className="font-medium truncate" title={project.name}>
              {project.name}
            </span>
            <span className="text-muted-foreground text-xs truncate">
              {project.github_repo_full_name}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant={statusBadgeVariant(project.last_push_status)}
            data-testid={`project-status-${project.id}`}
            data-status={project.last_push_status ?? "none"}
            title={project.last_push_error ?? undefined}
          >
            {statusBadgeLabel(project.last_push_status)}
          </Badge>
          {project.created_at && (
            <span className="text-muted-foreground text-xs whitespace-nowrap">
              created {new Date(project.created_at).toLocaleString()}
            </span>
          )}
          {callerIsAdmin && <OpenProjectButton projectId={project.id} />}
          {callerIsAdmin && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              data-testid={`push-rule-button-${project.id}`}
              aria-expanded={pushRuleOpen}
              onClick={() => setPushRuleOpen((prev) => !prev)}
            >
              {pushRuleOpen ? "Hide push rule" : "Configure push rule"}
            </Button>
          )}
        </div>
      </div>

      {callerIsAdmin && pushRuleOpen && <PushRuleForm projectId={project.id} />}
    </li>
  )
}

const ProjectsList = ({ teamId, callerIsAdmin }: Props) => {
  const projectsQuery = useQuery(projectsQueryOptions(teamId))
  const installationsQuery = useQuery(installationsQueryOptions(teamId))

  if (projectsQuery.isLoading) {
    return (
      <div
        className="flex flex-col gap-2 rounded-lg border p-3"
        data-testid="projects-loading"
      >
        {Array.from({ length: 2 }).map((_, i) => (
          <Skeleton key={`projects-skel-${i + 1}`} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  const projects = projectsQuery.data?.data ?? []
  const installations = installationsQuery.data?.data ?? []
  const projectsError = projectsQuery.error as ApiError | null | undefined
  const hasInstallations = installations.length > 0

  return (
    <div className="flex flex-col gap-3" data-testid="projects-section">
      {callerIsAdmin && (
        <div className="flex items-center justify-between gap-2">
          <CreateProjectDialog
            teamId={teamId}
            installations={installations}
            disabled={!hasInstallations}
            disabledReason={
              !hasInstallations
                ? "Install the GitHub App and link an account before creating a project."
                : undefined
            }
            trigger={
              <Button
                type="button"
                data-testid="create-project-button"
                disabled={!hasInstallations}
                data-disabled-reason={
                  hasInstallations ? undefined : "no_installations"
                }
              >
                <Plus className="mr-2 h-4 w-4" />
                New Project
              </Button>
            }
          />
        </div>
      )}

      {projectsError && (
        <Card className="border-destructive/50 bg-destructive/5 p-3 text-sm">
          <p className="font-medium">Could not load projects</p>
          <p className="text-muted-foreground text-xs">
            {extractDetail(projectsError) ?? "Unknown error"}
          </p>
        </Card>
      )}

      {!projectsError && projects.length === 0 && (
        <Card
          data-testid="projects-empty"
          className="flex flex-col items-center justify-center text-center py-8 px-6 gap-2"
        >
          <p className="text-sm font-medium">No projects yet</p>
          <p className="text-muted-foreground text-xs max-w-sm">
            {callerIsAdmin
              ? hasInstallations
                ? "Create your first project to link a GitHub repo to this team."
                : "A team admin must install the GitHub App before linking a repo."
              : "A team admin must create projects before they appear here."}
          </p>
          {callerIsAdmin && hasInstallations && (
            <CreateProjectDialog
              teamId={teamId}
              installations={installations}
              trigger={
                <Button
                  type="button"
                  size="sm"
                  data-testid="create-project-empty-cta"
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Create your first project
                </Button>
              }
            />
          )}
        </Card>
      )}

      {projects.length > 0 && (
        <ul
          className="flex flex-col divide-y rounded-lg border bg-card"
          data-testid="projects-list"
        >
          {projects.map((project) => (
            <ProjectRow
              key={project.id}
              project={project}
              callerIsAdmin={callerIsAdmin}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

export default ProjectsList
