export const dynamic = "force-dynamic";

export function GET(request: Request) {
  if (process.env.MARKWEAVE_FRONTEND_ERROR_TEST !== "1")
    return new Response(null, { status: 404 });
  const kind = new URL(request.url).searchParams.get("kind");
  if (kind === "empty") return new Response(null, { status: 204 });
  if (kind === "html") {
    const nonce = request.headers.get("x-nonce") ?? "";
    return new Response(
      `<!doctype html><html><head><style nonce="${nonce}">body{display:block}</style></head><body><main>Fixture</main><script nonce="${nonce}">document.body.dataset.ready="true"</script></body></html>`,
      { headers: { "Content-Type": "text/html; charset=utf-8" } },
    );
  }
  return Response.json({ fixture: true });
}
