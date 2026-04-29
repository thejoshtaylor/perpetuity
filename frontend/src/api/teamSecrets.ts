import { type TeamSecretStatus, TeamSecretsService } from "@/client"

export type TeamSecretsListEnvelope = TeamSecretStatus[]

export const teamSecretsQueryKey = (teamId: string) =>
  ["team", teamId, "secrets"] as const

export function teamSecretsQueryOptions(teamId: string) {
  return {
    queryKey: teamSecretsQueryKey(teamId),
    queryFn: async (): Promise<TeamSecretsListEnvelope> => {
      const res = await TeamSecretsService.listTeamSecrets({ teamId })
      return res as unknown as TeamSecretsListEnvelope
    },
  }
}

/** The two registered keys for M005/S01. Locked by the backend's
 * `_VALIDATORS` registry in `backend/app/api/team_secrets_registry.py`;
 * this constant exists so the panel renders both rows even before the
 * server's GET resolves (skeletons stay shape-stable). */
export const REGISTERED_TEAM_SECRET_KEYS = [
  "claude_api_key",
  "openai_api_key",
] as const

export type RegisteredTeamSecretKey =
  (typeof REGISTERED_TEAM_SECRET_KEYS)[number]
