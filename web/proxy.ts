import { randomBytes } from "node:crypto";
import { NextResponse, type NextRequest } from "next/server";

function policy(nonce: string): string {
  return [
    "default-src 'none'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    `script-src 'nonce-${nonce}' 'strict-dynamic'`,
    `style-src 'self' 'nonce-${nonce}'`,
    "connect-src 'self'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "manifest-src 'self'",
    "worker-src 'none'",
  ].join("; ");
}

export function proxy(request: NextRequest) {
  const acceptsHtml = (request.headers.get("accept") ?? "")
    .split(",")
    .some((item) => item.split(";", 1)[0]?.trim() === "text/html");
  const isComponent =
    request.headers.get("rsc") === "1" ||
    request.headers.has("next-router-prefetch") ||
    request.headers.has("next-router-segment-prefetch") ||
    request.headers.get("purpose") === "prefetch";
  if (request.method === "HEAD" || !acceptsHtml || isComponent)
    return NextResponse.next();

  const nonce = randomBytes(18).toString("base64");
  const csp = policy(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("Referrer-Policy", "same-origin");
  response.headers.set("X-Content-Type-Options", "nosniff");
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image).*)"],
};
