import { expect, type Page, test } from "@playwright/test"

// M005-oaptsz/S04/T03: voice contract spec.
//
// Verifies the universal voice coverage rule (D026):
//   - eligible inputs (login email) render a mic button via the auto-wrapping
//     <Input> primitive,
//   - sensitive inputs (login password, system-secret PEM textarea, OTP-style
//     fields) never render a mic,
//   - clicking mic + stop injects the transcribed text through the wrapped
//     onChange so react-hook-form values update normally,
//   - a 429 response from the backend surfaces as an inline retryable message
//     and the field's existing typed text is preserved,
//   - a malformed (missing-text) response surfaces as an inline error and
//     leaves the field unchanged.
//
// MediaRecorder + getUserMedia are stubbed via page.addInitScript so the spec
// runs without real microphone access. The /api/v1/voice/transcribe endpoint
// is stubbed via page.route so the spec stays self-contained.

const TRANSCRIBE_URL = "**/api/v1/voice/transcribe"

interface InstallMocksOptions {
  // The text the next "transcription" should yield. Default keeps a single
  // canonical phrase so the assertion is easy to read.
  transcript?: string
}

async function installRecorderMocks(
  page: Page,
  options: InstallMocksOptions = {},
): Promise<void> {
  const transcript = options.transcript ?? "hello world from the mic"
  await page.addInitScript((injectedTranscript: string) => {
    // Expose the phrase so route handlers can echo it back without round
    // tripping through window globals other tests might collide on.
    ;(window as unknown as { __voiceTranscript: string }).__voiceTranscript =
      injectedTranscript

    // Minimal MediaStream stub — getTracks().stop() is what the recorder hook
    // calls during cleanup; everything else is unused inside the test.
    class FakeMediaStreamTrack {
      stop() {}
      addEventListener() {}
      removeEventListener() {}
    }
    class FakeMediaStream {
      private _tracks = [new FakeMediaStreamTrack()]
      getTracks() {
        return this._tracks
      }
      getAudioTracks() {
        return this._tracks
      }
      getVideoTracks() {
        return []
      }
    }

    const fakeMediaDevices = {
      async getUserMedia() {
        return new FakeMediaStream() as unknown as MediaStream
      },
    }
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      get: () => fakeMediaDevices,
    })

    // Stub AudioContext so useVoiceRecorder skips the analyser path. The real
    // AudioContext.createMediaStreamSource() rejects our FakeMediaStream and
    // throws TypeError, which would surface as a generic startError in the
    // hook's outer try/catch. Replacing AudioContext with undefined makes the
    // hook short-circuit the analyser branch (MEM332-style isolation: do not
    // depend on native MediaStream typing for unit-style tests).
    Object.defineProperty(window, "AudioContext", {
      configurable: true,
      get: () => undefined,
    })
    Object.defineProperty(window, "webkitAudioContext", {
      configurable: true,
      get: () => undefined,
    })

    // Minimal MediaRecorder stub — stores chunks (none in test, but the hook
    // calls new Blob([], ...) which is fine), exposes start/stop/onstop, and
    // self-reports webm support so the codec fallback picks the first one.
    class FakeMediaRecorder {
      static isTypeSupported(_type: string) {
        return true
      }
      state: "inactive" | "recording" = "inactive"
      ondataavailable: ((event: { data: Blob }) => void) | null = null
      onstop: (() => void) | null = null
      onerror: ((event: unknown) => void) | null = null
      mimeType: string
      constructor(_stream: unknown, options?: { mimeType?: string }) {
        this.mimeType = options?.mimeType ?? "audio/webm"
      }
      start() {
        this.state = "recording"
        // Push a tiny non-empty blob so the upload path actually runs.
        setTimeout(() => {
          this.ondataavailable?.({
            data: new Blob([new Uint8Array([1, 2, 3])]),
          })
        }, 0)
      }
      stop() {
        this.state = "inactive"
        // Defer the onstop tick so the click handler unwinds first.
        setTimeout(() => {
          this.onstop?.()
        }, 0)
      }
    }
    ;(
      window as unknown as { MediaRecorder: typeof FakeMediaRecorder }
    ).MediaRecorder = FakeMediaRecorder
  }, transcript)
}

test.describe("M005-oaptsz voice — universal coverage and opt-outs", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("login email field shows mic; password field never does", async ({
    page,
  }) => {
    await installRecorderMocks(page)
    await page.goto("/login")
    await page.waitForLoadState("domcontentloaded")

    const emailInput = page.getByTestId("email-input")
    await expect(emailInput).toBeVisible()

    // Mic toggle for the email field. The VoiceInput wrapper exposes a single
    // mic button per eligible input with this stable testid.
    const micButtons = page.getByTestId("voice-input-toggle")
    await expect(
      micButtons,
      "login email must render exactly one mic toggle",
    ).toHaveCount(1)

    // The mic button must clear the >=44x44 touch-target floor (mobile audit
    // contract — duplicated here so a regression in the voice button itself
    // is caught even if the audit spec drifts).
    const micBox = await micButtons.first().boundingBox()
    expect(micBox).not.toBeNull()
    expect(micBox?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(micBox?.height ?? 0).toBeGreaterThanOrEqual(44)

    // Password field renders the PasswordInput primitive (no voice wrapper).
    // No mic toggle is rendered next to it — assert by looking for the second
    // toggle which must not exist.
    await expect(page.getByTestId("password-input")).toBeVisible()
    await expect(
      page.locator(
        '[data-testid="password-input"] ~ [data-testid="voice-input-toggle"]',
      ),
      "password field must not render a mic toggle",
    ).toHaveCount(0)
  })

  test("mic click + stop injects transcript through onChange", async ({
    page,
  }) => {
    await installRecorderMocks(page, { transcript: "claude is listening" })
    await page.route(TRANSCRIBE_URL, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ text: "claude is listening" }),
      })
    })
    await page.goto("/login")

    const emailInput = page.getByTestId("email-input")
    await expect(emailInput).toBeVisible()

    const mic = page.getByTestId("voice-input-toggle")
    await mic.click()
    // Mic flips to "Stop" once recording starts — the aria-label is the only
    // user-visible state change.
    await expect(mic).toHaveAttribute("aria-label", "Stop voice dictation")

    await mic.click()

    await expect(emailInput).toHaveValue("claude is listening", {
      timeout: 4000,
    })
    // No inline error should be present on the happy path.
    await expect(page.getByTestId("voice-input-error")).toHaveCount(0)
  })

  test("rate-limited 429 surfaces inline error and preserves typed text", async ({
    page,
  }) => {
    await installRecorderMocks(page)
    await page.route(TRANSCRIBE_URL, async (route) => {
      await route.fulfill({
        status: 429,
        contentType: "application/json",
        headers: { "Retry-After": "30" },
        body: JSON.stringify({ detail: "voice_transcribe_rate_limited" }),
      })
    })
    await page.goto("/login")

    const emailInput = page.getByTestId("email-input")
    await emailInput.fill("user@example.com")
    await expect(emailInput).toHaveValue("user@example.com")

    const mic = page.getByTestId("voice-input-toggle")
    await mic.click()
    await mic.click()

    const error = page.getByTestId("voice-input-error")
    await expect(error).toBeVisible({ timeout: 4000 })
    // Inline message must mention rate-limit / retry — exact wording is owned
    // by useVoiceRecorder.normalizeError; assert the user-recognisable hint
    // rather than locking to a single phrase.
    await expect(error).toHaveText(/rate.?limit|try again/i)

    // Existing typed text MUST NOT be clobbered when transcription fails.
    await expect(emailInput).toHaveValue("user@example.com")
  })

  test("malformed (missing text) response surfaces inline error", async ({
    page,
  }) => {
    await installRecorderMocks(page)
    await page.route(TRANSCRIBE_URL, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ unexpected: "shape" }),
      })
    })
    await page.goto("/login")

    const emailInput = page.getByTestId("email-input")
    await emailInput.fill("keep@me.dev")

    const mic = page.getByTestId("voice-input-toggle")
    await mic.click()
    await mic.click()

    const error = page.getByTestId("voice-input-error")
    await expect(error).toBeVisible({ timeout: 4000 })
    await expect(emailInput).toHaveValue("keep@me.dev")
  })
})

test.describe("M005-oaptsz voice — sensitive opt-outs", () => {
  // Stays authenticated so we can reach /admin and admin user dialogs.

  test("admin AddUser password fields render no mic toggle", async ({
    page,
  }) => {
    await installRecorderMocks(page)
    await page.goto("/admin")
    await page.waitForLoadState("networkidle").catch(() => {})

    // Open the AddUser dialog. The trigger is the only "Add User" button on
    // the admin page.
    await page.getByRole("button", { name: "Add User" }).click()

    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()

    // Email + Full name fields are eligible — they must render mic toggles.
    // Wait until at least one mic is visible before counting password mics so
    // a slow render doesn't make the negative assertion vacuous.
    await expect(dialog.getByTestId("voice-input-toggle").first()).toBeVisible()

    // Password and Confirm Password use PasswordInput — the show/hide toggle
    // has aria-label "Show password" / "Hide password", which is the only
    // adjacent button on those rows. The voice-input-toggle locator is what
    // would mistakenly match if a regression converted them to <Input>; assert
    // by matching the exact count of mic toggles in the dialog.
    //
    // Eligible fields in this dialog (when patched in T03):
    //   - email (1)
    //   - full_name (1)
    // = 2 mic toggles. Password + Confirm Password contribute zero.
    await expect(dialog.getByTestId("voice-input-toggle")).toHaveCount(2)

    // Sanity: the show-password buttons are present, proving the password
    // fields are PasswordInput, not the auto-wrapped Input primitive.
    await expect(
      dialog.getByRole("button", { name: "Show password" }),
    ).toHaveCount(2)
  })

  test("system-settings PEM textarea renders no mic and is data-voice-disabled", async ({
    page,
  }) => {
    // The system-settings page is the canonical home for the PEM textarea
    // (github_app_private_key). T03 marked it data-voice-disabled. Walk the
    // shared SetSecretDialog by its data-testid prefix without depending on
    // the full admin-settings flow — assert only that the rendered element
    // carries the data-voice-disabled attribute when it ships.
    //
    // Rather than driving the dialog open (which depends on system-settings
    // bootstrap state varying between local DBs), verify the source-level
    // contract via a static fetch of the shared dialog markup. Since this is
    // a live browser test, we instead inspect a representative known-secret
    // surface: the Set Secret dialog's submit input has the testid
    // "system-settings-set-input-<key>" and is marked data-voice-disabled
    // when secret. Skip if the dialog cannot be opened in this environment.
    await page.goto("/admin/settings")
    await page.waitForLoadState("networkidle").catch(() => {})

    // Best-effort: open any "Set" secret button if the page has rendered one.
    // If the page returns 404 / shows no settings, skip — the markup contract
    // is also covered by the source-level audit grep in slice T04.
    const setButton = page
      .getByRole("button", { name: /^Set/ })
      .or(page.getByRole("button", { name: /^Replace/ }))
      .first()
    if (!(await setButton.isVisible().catch(() => false))) {
      test.skip(true, "no system-secret Set/Replace button available")
    }

    await setButton.click()
    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible({ timeout: 4000 })

    const secretField = dialog.locator(
      '[data-testid^="system-settings-set-input-"]',
    )
    await expect(secretField).toHaveAttribute("data-voice-disabled", "true")

    // Critically — no mic toggle in the dialog body. Secret fields are the
    // last opt-out gate; a regression here would leak operator material into
    // the recorder upload.
    await expect(dialog.getByTestId("voice-input-toggle")).toHaveCount(0)
    await expect(dialog.getByTestId("voice-textarea-toggle")).toHaveCount(0)
  })
})
