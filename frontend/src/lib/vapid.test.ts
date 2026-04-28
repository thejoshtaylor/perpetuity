// M005-oaptsz/S03/T04 — Vitest unit test for urlBase64ToUint8Array.
//
// The backend's T01 generator emits the VAPID public key as the uncompressed
// EC P-256 point (65 bytes: 0x04 ‖ X ‖ Y) encoded as base64url-no-padding
// per RFC 8292 §3.2. The decoder must round-trip that exact shape because
// PushManager.subscribe() requires a 65-byte BufferSource.

import { describe, expect, it } from "vitest"

import { urlBase64ToUint8Array } from "./vapid"

function bytesToBase64Url(bytes: Uint8Array): string {
  let bin = ""
  for (let i = 0; i < bytes.length; i++) {
    bin += String.fromCharCode(bytes[i])
  }
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "")
}

describe("urlBase64ToUint8Array", () => {
  it("round-trips a 65-byte uncompressed P-256 point (RFC 8292 §3.2)", () => {
    const fixture = new Uint8Array(65)
    fixture[0] = 0x04 // uncompressed point marker
    for (let i = 1; i < 65; i++) {
      // Deterministic, non-zero, distinct bytes so a slice mismatch shows up.
      fixture[i] = (i * 7 + 13) & 0xff
    }
    const b64url = bytesToBase64Url(fixture)
    expect(b64url.length).toBe(87) // ceil(65 * 4 / 3) without padding
    expect(b64url).not.toContain("=")
    expect(b64url).not.toContain("+")
    expect(b64url).not.toContain("/")

    const decoded = urlBase64ToUint8Array(b64url)
    expect(decoded.length).toBe(65)
    expect(decoded[0]).toBe(0x04)
    expect(Array.from(decoded)).toEqual(Array.from(fixture))
  })

  it("handles missing padding by reconstructing it", () => {
    // Single-byte input encodes to 2 base64 chars + 2 padding chars; the
    // base64url-no-padding form drops the trailing `==`. The decoder must
    // reinsert padding before atob() or the call throws InvalidCharacterError.
    const decoded = urlBase64ToUint8Array("Zg") // "f" → 0x66
    expect(Array.from(decoded)).toEqual([0x66])
  })

  it("decodes the URL-safe alphabet (- and _)", () => {
    // 0x3e (>) → standard '+', URL-safe '-'
    // 0x3f (?) → standard '/', URL-safe '_'
    const decoded = urlBase64ToUint8Array("Pj8") // 0x3e 0x3f
    expect(Array.from(decoded)).toEqual([0x3e, 0x3f])
  })

  it("throws on empty input", () => {
    expect(() => urlBase64ToUint8Array("")).toThrow(/empty/)
  })
})
