/**
 * Helpers for the M004 admin-side e2e Playwright spec
 * (`frontend/tests/m004-guylpp.spec.ts`).
 *
 * Boots two sibling containers on `perpetuity_default` so the live
 * orchestrator can answer GitHub App API + clone calls without ever
 * reaching the public `api.github.com`:
 *
 *   1. mock-github API (FastAPI) — token mint + installation lookup,
 *      mounting `backend/tests/integration/fixtures/mock_github_app.py`
 *      verbatim (MEM261 / MEM252).
 *   2. mock-github git-daemon (workspace image) — bare repo
 *      `acme/widgets.git` with one initial commit on `main`, served on
 *      port 9418 with `--enable=receive-pack` (MEM281 / MEM289).
 *
 * Then stops the compose orchestrator and launches an ephemeral one
 * with `--network-alias orchestrator` carrying GITHUB_API_BASE_URL +
 * GITHUB_CLONE_BASE_URL pointing at the two sidecars (S04/T05 pattern).
 * The compose backend talks to `http://orchestrator:8001` so the alias
 * trick is transparent.
 *
 * `cleanup()` captures sidecar logs first, then `docker rm -f`s the
 * three managed containers, removes any team-mirror / workspace
 * containers spawned by the orchestrator during the run, wipes the
 * github_app_* + projects + push_rule rows the test populated, and
 * restores the compose orchestrator.
 *
 * The wall-clock budget for setup is ~30s on a warm stack — pip-install
 * inside python:3.12-slim dominates first-run boot.
 */
import { execFileSync, type SpawnSyncReturns } from "node:child_process"
import { generateKeyPairSync, randomBytes } from "node:crypto"
import { existsSync, readFileSync } from "node:fs"
import path from "node:path"
import { setTimeout as delay } from "node:timers/promises"
import { fileURLToPath } from "node:url"

import { expect, type Page } from "@playwright/test"

import { firstSuperuser, firstSuperuserPassword } from "../config.ts"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

export const REPO_ROOT = path.resolve(__dirname, "../../..")

export const NETWORK = "perpetuity_default"
export const ORCH_DNS_ALIAS = "orchestrator"
export const ORCH_IMAGE = "orchestrator:latest"
export const WORKSPACE_IMAGE = "perpetuity/workspace:test"

/** Same Fernet key the orchestrator + backend share. Must match what the
 * compose backend was started with so a sibling orchestrator can decrypt
 * the ciphertext the backend wrote. */
export const SYSTEM_SETTINGS_ENCRYPTION_KEY =
  readEnvValue("SYSTEM_SETTINGS_ENCRYPTION_KEY") ??
  "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo="

/** Fake installation token returned by the mock-github API. Prefix `ghs_`
 * matches GitHub's real installation-token shape so the redaction sweep
 * exercises the real-world fingerprint (MEM262). */
export const MOCK_FIXED_TOKEN = "ghs_M004S06FRONTENDFAKEINSTALLATIONTOKEN0000"

/** Fixed installation_id so the test has a deterministic value to feed into
 * the install-callback endpoint and the create-project dialog. */
export const FIXED_INSTALLATION_ID = 4242

const GITHUB_APP_KEYS = [
  "github_app_id",
  "github_app_client_id",
  "github_app_private_key",
  "github_app_webhook_secret",
] as const

function shortId(): string {
  return randomBytes(4).toString("hex")
}

function readEnvValue(key: string): string | null {
  // Mirror frontend/tests/config.ts — read from the root .env so this util
  // works whether the harness is launched via `bun run dev` (which loads
  // .env via dotenv) or via raw `bunx playwright`.
  const envPath = path.join(REPO_ROOT, ".env")
  if (!existsSync(envPath)) return null
  for (const raw of readFileSync(envPath, "utf-8").split("\n")) {
    const line = raw.trim()
    if (!line || line.startsWith("#")) continue
    const eq = line.indexOf("=")
    if (eq === -1) continue
    const k = line.slice(0, eq).trim()
    if (k !== key) continue
    let v = line.slice(eq + 1).trim()
    if (
      (v.startsWith('"') && v.endsWith('"')) ||
      (v.startsWith("'") && v.endsWith("'"))
    ) {
      v = v.slice(1, -1)
    }
    return v
  }
  return null
}

function dockerSync(
  args: string[],
  opts: { check?: boolean; timeoutSec?: number } = {},
): { stdout: string; stderr: string; status: number } {
  const { check = false, timeoutSec = 60 } = opts
  try {
    const out = execFileSync("docker", args, {
      encoding: "utf-8",
      timeout: timeoutSec * 1000,
      stdio: ["ignore", "pipe", "pipe"],
    })
    return { stdout: out, stderr: "", status: 0 }
  } catch (err) {
    const e = err as SpawnSyncReturns<string> & {
      stdout?: string
      stderr?: string
      status?: number | null
    }
    const stdout = typeof e.stdout === "string" ? e.stdout : ""
    const stderr = typeof e.stderr === "string" ? e.stderr : ""
    const status = typeof e.status === "number" ? e.status : -1
    if (check) {
      throw new Error(
        `docker ${args.join(" ")} failed (status=${status}): ${stderr || stdout}`,
      )
    }
    return { stdout, stderr, status }
  }
}

function composeSync(
  args: string[],
  opts: { check?: boolean; timeoutSec?: number } = {},
): { stdout: string; stderr: string; status: number } {
  const { check = false, timeoutSec = 180 } = opts
  try {
    const out = execFileSync("docker", ["compose", ...args], {
      encoding: "utf-8",
      timeout: timeoutSec * 1000,
      cwd: REPO_ROOT,
      stdio: ["ignore", "pipe", "pipe"],
    })
    return { stdout: out, stderr: "", status: 0 }
  } catch (err) {
    const e = err as SpawnSyncReturns<string> & {
      stdout?: string
      stderr?: string
      status?: number | null
    }
    const stdout = typeof e.stdout === "string" ? e.stdout : ""
    const stderr = typeof e.stderr === "string" ? e.stderr : ""
    const status = typeof e.status === "number" ? e.status : -1
    if (check) {
      throw new Error(
        `docker compose ${args.join(" ")} failed (status=${status}): ${stderr || stdout}`,
      )
    }
    return { stdout, stderr, status }
  }
}

/** RSA-2048 keypair via Node's crypto module. Format `pkcs1` matches what
 * the backend's PEM validator accepts (`-----BEGIN RSA PRIVATE KEY-----`,
 * S01 contract). The mock-github sidecar verifies inbound RS256 JWTs
 * against the matching SubjectPublicKeyInfo public key. */
function generateRsaKeypair(): { privatePem: string; publicPem: string } {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding: { type: "spki", format: "pem" },
    privateKeyEncoding: { type: "pkcs1", format: "pem" },
  })
  if (
    !privateKey.startsWith("-----BEGIN") ||
    !publicKey.startsWith("-----BEGIN")
  ) {
    throw new Error("malformed PEM output from generateKeyPairSync")
  }
  return { privatePem: privateKey, publicPem: publicKey }
}

async function probeContainerPython(
  name: string,
  script: string,
  timeoutMs: number,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const r = dockerSync(["exec", name, "python3", "-c", script], {
      timeoutSec: 5,
    })
    if (r.status === 0) return true
    await delay(1_000)
  }
  return false
}

/** Boot the FastAPI mock-github sidecar (S02/T04 pattern, MEM252). Returns
 * the container name (DNS alias on `perpetuity_default`). */
function bootMockGithubApi(opts: {
  publicKeyPem: string
  fixedToken: string
  appId: number
}): string {
  const name = `mock-github-api-${shortId()}`
  const fixturePath = path.resolve(
    REPO_ROOT,
    "backend/tests/integration/fixtures/mock_github_app.py",
  )
  if (!existsSync(fixturePath)) {
    throw new Error(
      `mock-github fixture missing at ${fixturePath} — slice contract regression`,
    )
  }
  const bootCmd =
    "set -e; " +
    "pip install --quiet --no-cache-dir " +
    "'fastapi==0.115.*' 'uvicorn==0.32.*' " +
    "'pyjwt[crypto]==2.9.*' 'cryptography>=43,<46'; " +
    "exec uvicorn mock_github_app:app --host 0.0.0.0 --port 8080"
  dockerSync(
    [
      "run",
      "-d",
      "--name",
      name,
      "--network",
      NETWORK,
      "--network-alias",
      name,
      "-v",
      `${fixturePath}:/app/mock_github_app.py:ro`,
      "-w",
      "/app",
      "-e",
      `PUBLIC_KEY_PEM=${opts.publicKeyPem}`,
      "-e",
      `FIXED_TOKEN=${opts.fixedToken}`,
      "-e",
      `GITHUB_APP_ID=${opts.appId}`,
      "--entrypoint",
      "bash",
      "python:3.12-slim",
      "-c",
      bootCmd,
    ],
    { check: true, timeoutSec: 60 },
  )
  return name
}

/** Wait for the mock-github FastAPI to answer /healthz from inside its own
 * container. python:3.12-slim has urllib but no curl, so we exec python3. */
async function waitForMockGithubApi(name: string): Promise<void> {
  const probe =
    "import sys, urllib.request\n" +
    "try:\n" +
    "    urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()\n" +
    "    sys.exit(0)\n" +
    "except Exception as e:\n" +
    "    print(repr(e)); sys.exit(3)\n"
  const ok = await probeContainerPython(name, probe, 90_000)
  if (!ok) {
    const logs = dockerSync(["logs", name], { timeoutSec: 10 }).stdout
    throw new Error(
      `mock-github-api ${name} never became healthy\nlogs:\n${logs.slice(-2000)}`,
    )
  }
}

/** Boot the workspace-image git-daemon sidecar (S04/T05 pattern, MEM281).
 * Seeds `acme/widgets.git` with one commit on `main` and serves it on
 * port 9418 with `--enable=receive-pack`. */
function bootMockGithubGitDaemon(): string {
  const name = `mock-gh-git-${shortId()}`
  const bootCmd = [
    "set -e",
    "mkdir -p /srv/git/acme/widgets.git",
    "git init --bare /srv/git/acme/widgets.git >/dev/null",
    "echo 'ref: refs/heads/main' > /srv/git/acme/widgets.git/HEAD",
    "mkdir -p /tmp/seed && cd /tmp/seed",
    "git init -b main >/dev/null 2>&1",
    "git config user.email seed@example.com",
    "git config user.name seed",
    "echo 'initial' > README.md",
    "git add README.md",
    "git commit -m 'initial commit' >/dev/null 2>&1",
    "git push /srv/git/acme/widgets.git main:main >/dev/null 2>&1",
    "cd /",
    "exec git daemon --base-path=/srv/git --export-all --reuseaddr " +
      "--enable=receive-pack --listen=0.0.0.0 --port=9418 " +
      "--verbose --informative-errors",
  ].join("; ")
  dockerSync(
    [
      "run",
      "-d",
      "--name",
      name,
      "--network",
      NETWORK,
      "--network-alias",
      name,
      "--entrypoint",
      "bash",
      WORKSPACE_IMAGE,
      "-c",
      bootCmd,
    ],
    { check: true, timeoutSec: 60 },
  )
  return name
}

/** Wait for the git-daemon to answer ls-remote. Probe via a sibling
 * workspace-image container so we don't need git locally. */
async function waitForGitDaemon(name: string): Promise<void> {
  const deadline = Date.now() + 30_000
  let lastErr = ""
  while (Date.now() < deadline) {
    const r = dockerSync(
      [
        "run",
        "--rm",
        "--network",
        NETWORK,
        "--entrypoint",
        "git",
        WORKSPACE_IMAGE,
        "ls-remote",
        `git://${name}:9418/acme/widgets.git`,
      ],
      { timeoutSec: 10 },
    )
    if (r.status === 0 && r.stdout.includes("refs/heads/main")) return
    lastErr = `${r.stderr.slice(0, 200)} | ${r.stdout.slice(0, 200)}`
    await delay(500)
  }
  const logs = dockerSync(["logs", name], { timeoutSec: 10 }).stdout
  throw new Error(
    `mock-github-git ${name} never became reachable; last=${lastErr}\nlogs:\n${logs.slice(-2000)}`,
  )
}

/** Stop the compose orchestrator and launch an ephemeral replacement
 * carrying GITHUB_API_BASE_URL + GITHUB_CLONE_BASE_URL pointing at our
 * sidecars. Uses `--network-alias orchestrator` so the compose backend
 * (which talks to `http://orchestrator:8001`) routes to it transparently. */
function bootEphemeralOrchestrator(opts: {
  mockApiUrl: string
  mockGitUrl: string
  redisPassword: string
  pgPassword: string
  apiKey: string
}): string {
  const name = `orch-s06-m004-${shortId()}`
  composeSync(["rm", "-sf", "orchestrator"], { timeoutSec: 60 })
  dockerSync(
    [
      "run",
      "-d",
      "--name",
      name,
      "--network",
      NETWORK,
      "--network-alias",
      ORCH_DNS_ALIAS,
      "--privileged", // MEM136 — loopback-ext4 needs privileged on linuxkit
      "-v",
      "/var/run/docker.sock:/var/run/docker.sock",
      "--mount",
      "type=bind,source=/var/lib/perpetuity/workspaces," +
        "target=/var/lib/perpetuity/workspaces,bind-propagation=rshared",
      "-v",
      "/var/lib/perpetuity/vols:/var/lib/perpetuity/vols",
      "-e",
      `WORKSPACE_IMAGE=${WORKSPACE_IMAGE}`,
      "-e",
      `ORCHESTRATOR_API_KEY=${opts.apiKey}`,
      "-e",
      `SYSTEM_SETTINGS_ENCRYPTION_KEY=${SYSTEM_SETTINGS_ENCRYPTION_KEY}`,
      "-e",
      "REDIS_HOST=redis",
      "-e",
      `REDIS_PASSWORD=${opts.redisPassword}`,
      "-e",
      `DATABASE_URL=postgresql://postgres:${opts.pgPassword}@db:5432/app`,
      "-e",
      `GITHUB_API_BASE_URL=${opts.mockApiUrl}`,
      "-e",
      `GITHUB_CLONE_BASE_URL=${opts.mockGitUrl}`,
      "-e",
      // Reaper interval doesn't matter for the test budget; default is fine.
      "MIRROR_REAPER_INTERVAL_SECONDS=30",
      ORCH_IMAGE,
    ],
    { check: true, timeoutSec: 60 },
  )
  return name
}

async function waitForOrchestrator(name: string): Promise<void> {
  const probe =
    "import sys, urllib.request\n" +
    "try:\n" +
    "    body = urllib.request.urlopen('http://127.0.0.1:8001/v1/health', timeout=2).read().decode()\n" +
    "    print(body)\n" +
    "    sys.exit(0 if 'image_present' in body else 2)\n" +
    "except Exception as e:\n" +
    "    print(repr(e)); sys.exit(3)\n"
  const ok = await probeContainerPython(name, probe, 90_000)
  if (!ok) {
    const logs = dockerSync(["logs", name], { timeoutSec: 10 }).stdout
    throw new Error(
      `ephemeral orchestrator ${name} never became healthy\nlogs:\n${logs.slice(-2000)}`,
    )
  }
}

/** Wipe github_app_* + projects + push-rule rows directly via psql. The
 * compose db container exposes `perpetuity-db-1` on perpetuity_default. */
function wipeM004State(): void {
  const sqls = [
    "DELETE FROM project_push_rules",
    "DELETE FROM projects",
    "DELETE FROM github_app_installations",
    `DELETE FROM system_settings WHERE key IN (${GITHUB_APP_KEYS.map((k) => `'${k}'`).join(",")})`,
    // Mirror rows the always-on toggle may have created.
    "DELETE FROM team_mirror_volumes",
  ]
  for (const sql of sqls) {
    dockerSync(
      [
        "exec",
        "perpetuity-db-1",
        "psql",
        "-U",
        "postgres",
        "-d",
        "app",
        "-c",
        sql,
      ],
      { timeoutSec: 15 },
    )
  }
}

function removeOrchestratorChildren(): void {
  for (const label of [
    "label=perpetuity.team_mirror=true",
    "label=perpetuity.managed=true",
  ]) {
    const ls = dockerSync(["ps", "-aq", "--filter", label], { timeoutSec: 15 })
    const ids = ls.stdout.split(/\s+/).filter(Boolean)
    if (ids.length === 0) continue
    dockerSync(["rm", "-f", ...ids], { timeoutSec: 120 })
  }
}

function restoreComposeOrchestrator(): void {
  composeSync(["up", "-d", "orchestrator"], { timeoutSec: 180 })
}

/** Seed the three github_app_* settings that drive install + clone. PUT
 * the synthetic PEM into github_app_private_key (Fernet-encrypted at the
 * backend), then the matching app_id / client_id. */
async function seedGithubAppCredentials(opts: {
  apiBase: string
  cookieHeader: string
  privatePem: string
  appId: number
  clientId: string
}): Promise<void> {
  const headers = {
    "content-type": "application/json",
    cookie: opts.cookieHeader,
  }
  const puts: Array<[string, unknown]> = [
    ["github_app_id", opts.appId],
    ["github_app_client_id", opts.clientId],
    ["github_app_private_key", opts.privatePem],
  ]
  for (const [key, value] of puts) {
    const r = await fetch(`${opts.apiBase}/api/v1/admin/settings/${key}`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ value }),
    })
    if (!r.ok) {
      const text = await r.text()
      throw new Error(`seed PUT ${key} failed: ${r.status} ${text}`)
    }
  }
}

/** Login the seeded superuser via the backend's HTTP API and return the
 * session-cookie header value (`session=...`). Used by `seedGithubAppCredentials`
 * which must run before the browser ever loads the page. */
async function adminCookieHeader(apiBase: string): Promise<string> {
  const r = await fetch(`${apiBase}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      email: firstSuperuser,
      password: firstSuperuserPassword,
    }),
    redirect: "manual",
  })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`admin login failed: ${r.status} ${text}`)
  }
  const setCookie = r.headers.get("set-cookie") ?? ""
  // Pull the `session=<jwt>` cookie out of the set-cookie list. Multiple
  // cookies may be returned as comma-separated entries; FastAPI sends one.
  const parts = setCookie.split(/, (?=[A-Za-z0-9_-]+=)/)
  const sessionEntry = parts.find((p) => /^session=/.test(p.trim()))
  if (!sessionEntry) {
    throw new Error(`no session cookie in admin login response: ${setCookie}`)
  }
  return sessionEntry.split(";")[0].trim()
}

export type SetupMockGithubResult = {
  cleanup: () => Promise<void>
  apiName: string
  gitName: string
  ephName: string
  appId: number
  privatePem: string
  fakeInstallationId: number
  mockTokenValue: string
  mockApiBase: string
  mockGitBase: string
}

/** Boot mock-github sidecars + ephemeral orchestrator pointed at them, and
 * seed the github_app_* system settings via the backend admin API.
 *
 * Idempotent w.r.t. cleanup: if any step throws, the partial state is
 * unwound before the throw bubbles. The returned `cleanup()` is safe to
 * call multiple times. */
export async function setupMockGithub(opts: {
  apiBase: string
  redisPassword: string
  pgPassword: string
  clientId?: string
}): Promise<SetupMockGithubResult> {
  const apiKey = randomBytes(24).toString("hex")
  const appId = Math.floor(Math.random() * 900_000) + 100_000
  const clientId = opts.clientId ?? "perpetuity-m004-s06"

  // 1) keypair
  const { privatePem, publicPem } = generateRsaKeypair()

  const cleanupSteps: Array<() => Promise<void> | void> = []
  let alreadyCleaned = false
  const cleanup = async (): Promise<void> => {
    if (alreadyCleaned) return
    alreadyCleaned = true
    // Run in reverse — last-registered cleanup runs first so we tear down
    // ephemeral orchestrator before sidecars before db wipe.
    for (let i = cleanupSteps.length - 1; i >= 0; i--) {
      try {
        await cleanupSteps[i]()
      } catch (err) {
        // Best-effort — never let a teardown failure mask the original test
        // assertion. Log to stderr so CI captures it.
        process.stderr.write(
          `[m004 cleanup] step ${i} failed: ${(err as Error).message}\n`,
        )
      }
    }
  }

  try {
    // 2) mock-github API sidecar
    const apiName = bootMockGithubApi({
      publicKeyPem: publicPem,
      fixedToken: MOCK_FIXED_TOKEN,
      appId,
    })
    cleanupSteps.push(() => {
      dockerSync(["rm", "-f", apiName], { timeoutSec: 30 })
    })
    await waitForMockGithubApi(apiName)

    // 3) git-daemon sidecar
    const gitName = bootMockGithubGitDaemon()
    cleanupSteps.push(() => {
      dockerSync(["rm", "-f", gitName], { timeoutSec: 30 })
    })
    await waitForGitDaemon(gitName)

    // 4) ephemeral orchestrator pointed at the sidecars (replaces compose
    //    orchestrator via --network-alias).
    const ephName = bootEphemeralOrchestrator({
      mockApiUrl: `http://${apiName}:8080`,
      mockGitUrl: `git://${gitName}:9418`,
      redisPassword: opts.redisPassword,
      pgPassword: opts.pgPassword,
      apiKey,
    })
    cleanupSteps.push(() => {
      dockerSync(["rm", "-f", ephName], { timeoutSec: 30 })
      removeOrchestratorChildren()
      restoreComposeOrchestrator()
    })
    await waitForOrchestrator(ephName)

    // 5) wipe any leftover M004 state from prior runs, then seed the three
    //    github_app_* settings via the admin API.
    cleanupSteps.push(() => {
      wipeM004State()
    })
    wipeM004State()
    const cookie = await adminCookieHeader(opts.apiBase)
    await seedGithubAppCredentials({
      apiBase: opts.apiBase,
      cookieHeader: cookie,
      privatePem,
      appId,
      clientId,
    })

    return {
      cleanup,
      apiName,
      gitName,
      ephName,
      appId,
      privatePem,
      fakeInstallationId: FIXED_INSTALLATION_ID,
      mockTokenValue: MOCK_FIXED_TOKEN,
      mockApiBase: `http://${apiName}:8080`,
      mockGitBase: `git://${gitName}:9418`,
    }
  } catch (err) {
    await cleanup()
    throw err
  }
}

/** Sign up a fresh user via the UI, create a non-personal team named
 * `<prefix>-<rand>`, and return its teamId. Mirrors `tests/utils/teams.ts`
 * but takes a Page so the calling test can re-use its authenticated
 * context. The signup flow lands on /teams. */
export async function seedTeamAdmin(
  page: Page,
  opts: { fullName: string; email: string; password: string; teamName: string },
): Promise<{ teamId: string }> {
  await page.goto("/signup")
  await page.getByTestId("full-name-input").fill(opts.fullName)
  await page.getByTestId("email-input").fill(opts.email)
  await page.getByTestId("password-input").fill(opts.password)
  await page.getByTestId("confirm-password-input").fill(opts.password)
  await page.getByRole("button", { name: "Sign Up" }).click()
  await page.waitForURL("/teams")

  // Create a non-personal team so connections-section + mirror-section
  // both render (mirror suppresses on personal teams).
  await page.getByTestId("create-team-button").first().click()
  await page.getByTestId("create-team-name-input").fill(opts.teamName)
  await page.getByTestId("create-team-submit").click()
  const card = page.getByTestId("team-card").filter({ hasText: opts.teamName })
  await expect(card).toBeVisible()
  await card.click()
  await page.waitForURL(/\/teams\/[^/]+$/)
  const m = page.url().match(/\/teams\/([^/?#]+)/)
  if (!m) throw new Error(`could not parse team id from ${page.url()}`)
  return { teamId: m[1] }
}

/** Capture the latest backend + orchestrator logs and assert that no
 * GitHub token-prefix or PEM armor leaked into them during the test. The
 * single permitted match shape is `token_prefix=ghs_<4chars>` (MEM262).
 *
 * Skips the mock-github sidecar logs by design — those legitimately
 * contain the canned token. */
export function assertRedactedLogs(opts: {
  ephName: string
  /** Backend container name. Defaults to compose's `perpetuity-backend-1`. */
  backendName?: string
  capturedSecretValue?: string | null
}): void {
  const backendName = opts.backendName ?? "perpetuity-backend-1"
  const orchLogs = dockerSync(["logs", opts.ephName], { timeoutSec: 15 })
  const beLogs = dockerSync(["logs", backendName], { timeoutSec: 15 })
  const blob = `${orchLogs.stdout}\n${orchLogs.stderr}\n${beLogs.stdout}\n${beLogs.stderr}`

  // 1. Generic GitHub token prefix families. ghs_ is allowed only inside
  //    `token_prefix=ghs_<4>` log lines (the canonical 4-char prefix).
  for (const prefix of ["gho_", "ghu_", "ghr_", "github_pat_"]) {
    if (blob.includes(prefix)) {
      throw new Error(
        `redaction sweep — '${prefix}' appeared in backend/orchestrator logs`,
      )
    }
  }
  for (const line of blob.split("\n")) {
    if (line.includes("ghs_") && !line.includes("token_prefix=")) {
      throw new Error(
        `redaction sweep — 'ghs_' appeared in non-prefix context: ${line}`,
      )
    }
  }

  // 2. Mock token plaintext must NEVER appear (the canned fixed-token).
  if (blob.includes(MOCK_FIXED_TOKEN)) {
    throw new Error(
      `redaction sweep — installation token leaked into backend/orchestrator logs`,
    )
  }

  // 3. PEM armor — never legitimate inside backend/orchestrator logs.
  if (blob.includes("-----BEGIN")) {
    throw new Error(
      `redaction sweep — PEM armor '-----BEGIN' appeared in backend/orchestrator logs`,
    )
  }

  // 4. The webhook secret captured in scenario 1 must never appear in
  //    the backend log either — closure of the FE one-shot discipline.
  if (
    opts.capturedSecretValue &&
    opts.capturedSecretValue.length >= 8 &&
    blob.includes(opts.capturedSecretValue)
  ) {
    throw new Error(
      `redaction sweep — generated webhook secret leaked into backend logs`,
    )
  }
}
