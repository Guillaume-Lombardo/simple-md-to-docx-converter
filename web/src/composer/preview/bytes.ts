export const DEFAULT_PREVIEW_MAX_BYTES = 16 * 1024 * 1024;

export function revisionArtifactPath(
  draftId: string,
  revisionId: string,
  kind: "preview" | "download",
): string {
  if (!/^[0-9a-f-]{36}$/i.test(draftId) || !/^[0-9a-f-]{36}$/i.test(revisionId))
    throw new Error("Invalid revision identity.");
  return `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}/artifacts/${kind}`;
}

export function assertRevisionUrl(url: string, expectedPath: string): string {
  const parsed = new URL(url, window.location.origin);
  if (
    parsed.origin !== window.location.origin ||
    parsed.pathname !== expectedPath ||
    parsed.search ||
    parsed.hash
  )
    throw new Error("The artifact URL does not match this revision.");
  return parsed.pathname;
}

export async function readBoundedResponse(
  response: Response,
  maximumBytes: number,
  expectedType: string,
): Promise<ArrayBuffer> {
  if (!response.ok) throw new Error("The preview is unavailable.");
  if (
    response.headers.get("content-type")?.split(";")[0]?.trim() !== expectedType
  )
    throw new Error("The preview has an unexpected file type.");
  const declared = Number(response.headers.get("content-length"));
  if (declared > maximumBytes)
    throw new Error("The preview exceeds the configured size limit.");
  if (!response.body) throw new Error("The preview response is empty.");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maximumBytes)
        throw new Error("The preview exceeds the configured size limit.");
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  }
  const output = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return output.buffer;
}

export async function verifySha256(
  bytes: ArrayBuffer,
  expected?: string,
): Promise<void> {
  if (!expected) return;
  if (!/^[0-9a-f]{64}$/i.test(expected))
    throw new Error("Invalid preview digest.");
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  const actual = Array.from(digest, (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  if (actual.toLowerCase() !== expected.toLowerCase())
    throw new Error("The preview does not match the selected revision.");
}
