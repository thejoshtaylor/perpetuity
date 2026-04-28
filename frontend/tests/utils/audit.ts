import { expect, type Page } from "@playwright/test"

const MIN_TOUCH_TARGET_PX = 44

const INTERACTIVE_SELECTOR =
  "button, a, [role=button], input, select, textarea"

export async function assertNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => {
    const el = document.documentElement
    return {
      scrollWidth: el.scrollWidth,
      innerWidth: window.innerWidth,
    }
  })
  expect(
    overflow.scrollWidth,
    `document scrollWidth (${overflow.scrollWidth}px) exceeds viewport innerWidth (${overflow.innerWidth}px) by more than 1px tolerance`,
  ).toBeLessThanOrEqual(overflow.innerWidth + 1)
}

export interface UndersizedTarget {
  tag: string
  role: string | null
  text: string
  width: number
  height: number
}

export async function assertTouchTargets(page: Page): Promise<void> {
  const locator = page.locator(INTERACTIVE_SELECTOR)
  const count = await locator.count()
  const undersized: UndersizedTarget[] = []

  for (let i = 0; i < count; i++) {
    const el = locator.nth(i)
    const visible = await el.isVisible().catch(() => false)
    if (!visible) continue

    const box = await el.boundingBox()
    if (!box) continue

    if (box.width < MIN_TOUCH_TARGET_PX || box.height < MIN_TOUCH_TARGET_PX) {
      const meta = await el
        .evaluate((node: Element) => ({
          tag: node.tagName.toLowerCase(),
          role: node.getAttribute("role"),
          text: (node.textContent ?? "").trim().slice(0, 40),
        }))
        .catch(() => ({ tag: "?", role: null, text: "" }))
      undersized.push({
        tag: meta.tag,
        role: meta.role,
        text: meta.text,
        width: Math.round(box.width),
        height: Math.round(box.height),
      })
    }
  }

  expect(
    undersized,
    `interactive elements smaller than ${MIN_TOUCH_TARGET_PX}x${MIN_TOUCH_TARGET_PX} CSS px:\n${undersized
      .map(
        (t) =>
          `  ${t.tag}${t.role ? `[role=${t.role}]` : ""} "${t.text}" — ${t.width}x${t.height}`,
      )
      .join("\n")}`,
  ).toEqual([])
}
