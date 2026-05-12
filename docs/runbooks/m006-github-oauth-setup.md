# M006 GitHub App OAuth Setup Runbook

## Why

Milestone M006 adds personal-GitHub-install support: when a user's Perpetuity
installation is tied to a personal GitHub account (not an organization), repo
creation calls `POST /user/repos` using a **GitHub user-to-server OAuth token**
rather than the installation token. This allows the GitHub API to create repos
under the user's personal account, which the installation token cannot do.

For this flow to work, the GitHub App must be configured with:

1. **OAuth (GitHub App) enabled** — so the install callback can exchange an
   authorization code for a user access token + refresh token.
2. **`repo` scope requested** — so the resulting token has permission to create
   private repositories under the user's account.
3. **OAuth Client ID + Client Secret seeded in Perpetuity admin settings** —
   so the backend can perform the OAuth token exchange and future refreshes.
4. **GitHub App slug seeded in Perpetuity admin settings** — so the backend
   can construct the reinstall URL surfaced to users when their token row is
   missing or expired.

Without these changes the `POST /api/v1/teams/{tid}/github/installations/{iid}/create-repository`
route returns `409 github_user_token_required` for every personal install,
and the `CreateGitHubRepoDialog.tsx` reinstall CTA fires but the install URL
points to a mis-configured App that will not grant the correct scopes.

## What to Change

### 1. Enable OAuth on the GitHub App

1. Navigate to **GitHub → Settings → Developer settings → GitHub Apps → `<your-app>` → Edit → General**.
2. Scroll to **"Identifying and authorizing users"**.
3. Ensure **"Expire user authorization tokens"** is **checked** (required for
   refresh-token support; tokens expire after 8 hours and refresh tokens after
   ~6 months).
4. Set **"Callback URL"** to:
   ```
   https://<your-perpetuity-domain>/api/v1/github/install-callback
   ```
   This is the same endpoint used for the installation callback; the M006
   backend now reads both the `installation_id` and the `code` parameter on
   the same redirect. If you already set this URL during M004 setup, it does
   not need to change.
5. Click **"Save changes"**.

### 2. Add `repo` Scope to the App's Permissions

1. Still in **Edit → Permissions & events → Repository permissions**.
2. Find **"Contents"** (this controls `repo`-level repo creation access) and
   set it to **"Read & write"** if it is not already.
3. GitHub will ask whether to notify existing installations of the permission
   upgrade — select **"Send notification"** so existing users see the
   authorization request on next access.
4. Click **"Save changes"**.

> **Note:** `public_repo` scope alone is insufficient for creating private
> repositories. If users will only ever create public repos, `public_repo` may
> suffice, but the reinstall CTA and 403 handling assume `repo` scope.

### 3. Copy the OAuth Client ID and Client Secret

1. From **GitHub App → Edit → General**, copy the value shown under
   **"Client ID"** (format: `Iv23.…`).
2. Click **"Generate a new client secret"**. Copy the value shown — it is
   displayed **once** and GitHub will not show it again.

### 4. Seed OAuth Credentials in Perpetuity Admin Settings

Log in as a Perpetuity system admin, navigate to `/admin/settings`, and set
the following four values:

| Setting key | Where to paste | Sensitive? |
|---|---|---|
| `github_app_client_id` | Client ID copied in step 3 | No |
| `github_app_client_secret` | Client secret copied in step 3 | Yes (Fernet-encrypted at rest) |
| `github_app_slug` | Short slug from the App URL: `github.com/apps/<slug>` | No |
| `github_app_private_key` | (Already set by M004; no change needed) | Yes |

The `github_app_slug` is used to build the install URL
(`https://github.com/apps/<slug>/installations/new?state=<jwt>`) that the
reinstall CTA button opens. An empty or missing slug causes the
`GET /api/v1/teams/{team_id}/github/install-url` endpoint to return
`404 github_app_not_configured`.

### 5. No Restart Required

The backend reads `github_app_client_id` and `github_app_client_secret` from
the database on every OAuth exchange call (no caching). Seeding the values in
the admin UI takes effect immediately.

## How to Verify

### Verify OAuth credentials are present

```sql
SELECT key, has_value, sensitive
FROM system_settings
WHERE key IN (
    'github_app_client_id',
    'github_app_client_secret',
    'github_app_slug'
)
ORDER BY key;
```

Expected result: three rows, all with `has_value = true`. The
`github_app_client_secret` row should show `sensitive = true`.

### Verify the install URL endpoint works

As a team admin, call:

```bash
curl -sS -b cookies.txt \
  "https://api.example.com/api/v1/teams/<team_id>/github/install-url"
```

A `200` response with body `{"install_url":"https://github.com/apps/<slug>/installations/new?state=...","state":"...","expires_at":"..."}` confirms the slug is seeded correctly.

### Verify the OAuth user token table exists

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name = 'github_user_oauth_tokens';
```

Expected: one row. If the table is absent, the S17 Alembic migration has not
run — apply it with `alembic upgrade head` before proceeding.

### Verify the full install flow (post-setup smoke test)

1. As a regular team member (not admin), open the **Create Repository** dialog
   for a project linked to a personal GitHub installation.
2. Submit the form.
3. If the user has not yet authorized the App, the dialog should display the
   reinstall CTA: **"Reinstall on GitHub"**.
4. Clicking the button should open
   `https://github.com/apps/<slug>/installations/new?state=<jwt>` in a new tab.
5. Complete the GitHub authorization flow.
6. Return to Perpetuity and retry repo creation — it should succeed.

In the backend logs, a successful token-backed create logs:

```
github_create_repository installation_id=<id> token_class=user_token user_token_prefix=ghu_...
```

A missing token still in effect before the reinstall logs:

```
github_user_token_required installation_id=<id> reason=row_missing
```

## Rollback

This procedure is additive (new columns, new admin settings). Rollback steps:

1. **If you added the `repo` scope** and existing installations received the
   "permissions changed" notification, revoking the scope requires another
   permissions change on the GitHub App and another notification round. Users
   who already accepted the updated permissions will need to re-accept the
   downgraded set on next use.

2. **If you seeded `github_app_client_secret` and want to revoke it:**
   go to **GitHub App → Edit → General → Client secrets** and click **Revoke**
   next to the secret. Then clear the `github_app_client_secret` row in
   Perpetuity admin settings (PUT an empty value or use the database directly:
   `UPDATE system_settings SET has_value = FALSE, value_encrypted = NULL WHERE key = 'github_app_client_secret'`).
   No restart is required; the next OAuth exchange attempt will fail with a
   recognisable GitHub error.

3. **If you need to revert the S17 migration** (removes the `github_user_oauth_tokens`
   table — all stored user tokens are **permanently deleted**):
   ```bash
   alembic downgrade s16_workflow_run_rejected_status
   ```
   Only do this during a maintenance window. All personal-install repo creation
   attempts will begin returning `409 github_user_token_required` after
   downgrade because the storage table no longer exists.

## When This Changes

| Trigger | Action |
|---|---|
| GitHub App is re-registered under a new name/slug | Update `github_app_slug` in admin settings; the install URL changes immediately |
| `github_app_client_secret` is rotated on GitHub | Generate a new secret on GitHub, update `github_app_client_secret` in admin settings; old secret stops working immediately after GitHub-side revocation |
| OAuth callback URL changes (e.g. domain rename) | Update the "Callback URL" field on the GitHub App; the backend route path (`/api/v1/github/install-callback`) is fixed |
| `repo` scope is downgraded to `public_repo` | Existing stored tokens retain whichever scope was granted at install time; new installs receive the reduced scope; private-repo creation will begin returning 403 for new users |
| Perpetuity user is off-boarded | Their `github_user_oauth_tokens` row is CASCADE-deleted when the `user` row is removed; no manual token revocation needed |

See also: [M004 Secrets Rotation Runbook](m004-secrets-rotation.md) for
rotating `SYSTEM_SETTINGS_ENCRYPTION_KEY` (which protects the
`github_app_client_secret` and `github_app_private_key` ciphertext) and for
rotating `github_app_webhook_secret`.
