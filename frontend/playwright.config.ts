import { defineConfig, devices } from '@playwright/test';
import 'dotenv/config'

/**
 * Read environment variables from file.
 * https://github.com/motdotla/dotenv
 */

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  testDir: './tests',
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI. */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: process.env.CI ? 'blob' : 'html',
  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: 'http://localhost:5173',

    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'on-first-retry',
  },

  /* Configure projects for major browsers */
  projects: [
    { name: 'setup', testMatch: /.*\.setup\.ts/ },

    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: 'playwright/.auth/user.json',
      },
      dependencies: ['setup'],
      // M004/S06/T05: the m004-guylpp spec needs the mock-github sidecars
      // and an orchestrator parameterized to talk to them — running it
      // under the default chromium project would fail because the compose
      // orchestrator points at the real api.github.com. The dedicated
      // m004-guylpp project below is the only place this spec runs.
      // M005-oaptsz/S01/T05: m005-oaptsz-sw-bypass needs the production
      // build so the SW actually registers — the dedicated m005-oaptsz-sw
      // project below is the only place that spec runs.
      // M005-oaptsz/S03/T05: m005-oaptsz-push is also pinned to its own
      // project (preview build + serviceWorkers:'allow' + pre-granted
      // notifications permission); excluded everywhere else.
      testIgnore: [
        'm004-guylpp.spec.ts',
        'm005-oaptsz-sw-bypass.spec.ts',
        'm005-oaptsz-push.spec.ts',
      ],
    },

    {
      name: 'mobile-chrome',
      use: {
        ...devices['Pixel 5'],
        storageState: 'playwright/.auth/user.json',
      },
      dependencies: ['setup'],
      testIgnore: [
        'm004-guylpp.spec.ts',
        'm005-oaptsz-sw-bypass.spec.ts',
        'm005-oaptsz-push.spec.ts',
      ],
    },

    {
      name: 'mobile-chrome-no-auth',
      use: {
        ...devices['Pixel 5'],
        storageState: { cookies: [], origins: [] },
      },
      testIgnore: [
        'm004-guylpp.spec.ts',
        'm005-oaptsz-sw-bypass.spec.ts',
        'm005-oaptsz-push.spec.ts',
      ],
    },

    // M005-oaptsz/S01/T04: iOS mobile-audit project. Uses iPhone 13 device
    // descriptor (390x844 viewport, ~414px logical width on landscape; close
    // enough to the 414 viewport target in the slice plan). storageState +
    // setup dependency mirror chromium so the audit walks authenticated
    // routes as the seeded superuser.
    {
      name: 'iphone-13-mobile-safari',
      use: {
        ...devices['iPhone 13'],
        storageState: 'playwright/.auth/user.json',
      },
      dependencies: ['setup'],
      testIgnore: [
        'm004-guylpp.spec.ts',
        'm005-oaptsz-sw-bypass.spec.ts',
        'm005-oaptsz-push.spec.ts',
      ],
    },

    // M005-oaptsz/S01/T04: desktop Firefox keyboard/mouse regression catch.
    // Same auth + setup wiring; touch-target enforcement still catches
    // tab-focus and pointer regressions on a non-WebKit/non-Chromium engine.
    {
      name: 'desktop-firefox',
      use: {
        ...devices['Desktop Firefox'],
        storageState: 'playwright/.auth/user.json',
      },
      dependencies: ['setup'],
      testIgnore: [
        'm004-guylpp.spec.ts',
        'm005-oaptsz-sw-bypass.spec.ts',
        'm005-oaptsz-push.spec.ts',
      ],
    },

    // M004/S06/T05: dedicated project that ONLY runs the m004-guylpp e2e.
    // Inherits the chromium auth state so the spec lands as the seeded
    // superuser for /admin/settings flows, but boots its own mock-github
    // sidecars + ephemeral orchestrator inside the spec's beforeAll.
    //
    // Run with:  cd frontend && VITE_API_URL=http://localhost:8001 \
    //              bunx playwright test --project=m004-guylpp
    {
      name: 'm004-guylpp',
      use: {
        ...devices['Desktop Chrome'],
        storageState: 'playwright/.auth/user.json',
      },
      dependencies: ['setup'],
      testMatch: /m004-guylpp\.spec\.ts/,
    },

    // M005-oaptsz/S01/T05: dedicated project that ONLY runs the SW-bypass
    // slice contract gate. The SW only registers under the production build
    // (vite-plugin-pwa devOptions.enabled is false), so this project points
    // at the `bun run preview` server on :4173 instead of the dev server on
    // :5173. No setup/storageState dependency — the spec hits an unauthed
    // public endpoint (/api/v1/utils/health-check/) so login is unnecessary.
    //
    // Run with:  cd frontend && bunx playwright test --project=m005-oaptsz-sw
    {
      name: 'm005-oaptsz-sw',
      use: {
        ...devices['Desktop Chrome'],
        baseURL: 'http://localhost:4173',
        // Storage isolation: a fresh context per test so SW registration
        // state is not carried over between specs.
        storageState: { cookies: [], origins: [] },
      },
      testMatch: /m005-oaptsz-sw-bypass\.spec\.ts/,
    },

    // M005-oaptsz/S03/T05: dedicated project that ONLY runs the push slice
    // contract gate. Mirrors m005-oaptsz-sw's preview-build wiring (the SW
    // only registers under :4173) and adds Notification permission +
    // serviceWorkers:'allow' + the seeded superuser's storageState so the
    // spec can subscribe end-to-end without scripting the browser-level
    // permission dialog.
    //
    // Run with:  cd frontend && bunx playwright test --project=m005-oaptsz-push
    {
      name: 'm005-oaptsz-push',
      use: {
        ...devices['Desktop Chrome'],
        baseURL: 'http://localhost:4173',
        permissions: ['notifications'],
        serviceWorkers: 'allow',
        storageState: 'playwright/.auth/user.json',
      },
      dependencies: ['setup'],
      testMatch: /m005-oaptsz-push\.spec\.ts/,
    },

    // {
    //   name: 'firefox',
    //   use: {
    //     ...devices['Desktop Firefox'],
    //     storageState: 'playwright/.auth/user.json',
    //   },
    //   dependencies: ['setup'],
    // },

    // {
    //   name: 'webkit',
    //   use: {
    //     ...devices['Desktop Safari'],
    //     storageState: 'playwright/.auth/user.json',
    //   },
    //   dependencies: ['setup'],
    // },

    // {
    //   name: 'Mobile Safari',
    //   use: { ...devices['iPhone 12'] },
    // },

    /* Test against branded browsers. */
    // {
    //   name: 'Microsoft Edge',
    //   use: { ...devices['Desktop Edge'], channel: 'msedge' },
    // },
    // {
    //   name: 'Google Chrome',
    //   use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    // },
  ],

  /* Run your local dev server(s) before starting the tests. The dev server
   * (port 5173) backs every project except m005-oaptsz-sw; the production
   * preview (port 4173) is what registers the real SW for the bypass spec.
   * Both honor reuseExistingServer so devs running their own dev/preview
   * stack don't get duplicate processes. */
  webServer: [
    {
      command: 'bun run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
    },
    // M005-oaptsz/S01/T05: production preview for the SW-bypass spec.
    // `bun run build` produces `dist/sw.js` from `src/sw.ts`; `vite preview`
    // serves dist/ (including the SW) on :4173. timeout is bumped because
    // the build can take >30s on cold caches.
    {
      command: 'bun run build && bun run preview --port 4173 --strictPort',
      url: 'http://localhost:4173',
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
  ],
});
