export const dynamic = "force-dynamic";

export function GET(request: Request) {
  if (process.env.MARKWEAVE_FRONTEND_ERROR_TEST !== "1")
    return new Response(null, { status: 404 });
  const kind = new URL(request.url).searchParams.get("kind");
  if (kind === "empty") return new Response(null, { status: 204 });
  return Response.json({ fixture: true });
}
