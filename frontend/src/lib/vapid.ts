// M005-oaptsz/S03/T04 — VAPID public-key encoding helpers.
//
// The backend (T01) serves the public key as base64url-no-padding per
// RFC 8292 §3.2. PushManager.subscribe() requires `applicationServerKey` to
// be a `Uint8Array` (or BufferSource), so the browser must decode the
// transport string. This is the standard MDN snippet, isolated as a pure
// utility so it can be unit-tested without spinning up the SW.

export function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  if (typeof base64 !== "string" || base64.length === 0) {
    throw new Error("vapid: empty base64 input")
  }
  const padding = "=".repeat((4 - (base64.length % 4)) % 4)
  const normalized = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/")
  const raw = atob(normalized)
  // Allocate a fresh ArrayBuffer so the Uint8Array's `.buffer` is an
  // ArrayBuffer (not ArrayBufferLike / SharedArrayBuffer); PushManager's
  // `applicationServerKey` BufferSource type rejects the broader form.
  const buffer = new ArrayBuffer(raw.length)
  const out = new Uint8Array(buffer)
  for (let i = 0; i < raw.length; i++) {
    out[i] = raw.charCodeAt(i)
  }
  return out
}

export async function endpointHash(endpoint: string): Promise<string> {
  // Client-side mirror of the backend's `endpoint_hash=sha256[:8]` log token.
  // Same hash function so log surfaces line up across frontend / backend.
  if (typeof crypto === "undefined" || typeof crypto.subtle === "undefined") {
    return "unknown"
  }
  const bytes = new TextEncoder().encode(endpoint)
  const digest = await crypto.subtle.digest("SHA-256", bytes)
  const hex = Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
  return hex.slice(0, 8)
}
