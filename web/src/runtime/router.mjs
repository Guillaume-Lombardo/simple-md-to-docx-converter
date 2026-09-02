import { readFileSync } from "node:fs";
import {
  createServer as createHttpServer,
  request as sendHttp,
} from "node:http";
import {
  createServer as createHttpsServer,
  request as sendHttps,
} from "node:https";

export const HSTS = "max-age=31536000";
export const PERMISSIONS_POLICY =
  "camera=(), geolocation=(), microphone=(), payment=(), usb=()";

const exactOperations = new Set([
  "/health/live",
  "/health/ready",
  "/metrics",
  "/docs",
  "/redoc",
  "/openapi.json",
]);
const hopByHop = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

export function normalizeRequestTarget(requestTarget) {
  const separator = requestTarget.indexOf("?");
  const rawPath =
    separator < 0 ? requestTarget : requestTarget.slice(0, separator);
  const query = separator < 0 ? "" : requestTarget.slice(separator);
  let decoded = decodeURIComponent(rawPath);
  const encodedPathSeparator = /%(?:2f|5c)/i.test(decoded);
  const encodedDotSegment = decoded.split(/[\\/]/).some((part) => {
    const dotsDecoded = part.replaceAll(/%2e/gi, ".");
    return /%2e/i.test(part) && (dotsDecoded === "." || dotsDecoded === "..");
  });
  if (encodedPathSeparator || encodedDotSegment)
    throw new URIError("Nested path control encoding");
  decoded = decoded.replaceAll("\\", "/");
  const parts = [];
  for (const part of decoded.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  const path = `/${parts.join("/")}`;
  const encodedPath = `/${parts.map((part) => encodeURIComponent(part)).join("/")}`;
  return { path, requestTarget: `${encodedPath}${query}` };
}

export function selectUpstream(path) {
  const lower = path.toLowerCase();
  if (lower === "/_frontend/health" || lower.startsWith("/_frontend/health/"))
    return "deny";
  if (
    path === "/api/v1" ||
    path.startsWith("/api/v1/") ||
    exactOperations.has(path) ||
    path.startsWith("/docs/")
  )
    return "backend";
  return "frontend";
}

function validatedOrigin(value, name) {
  const origin = new URL(value);
  if (
    !["http:", "https:"].includes(origin.protocol) ||
    origin.username ||
    origin.password ||
    origin.pathname !== "/" ||
    origin.search ||
    origin.hash
  )
    throw new Error(
      `${name} must be an HTTP(S) origin without credentials or a path`,
    );
  return origin;
}

function requestHeaders(incoming, frontend) {
  const headers = { ...incoming.headers };
  for (const name of Object.keys(headers))
    if (
      hopByHop.has(name) ||
      name === "forwarded" ||
      name.startsWith("x-forwarded-") ||
      (frontend && name === "cookie")
    )
      delete headers[name];
  return headers;
}

function securityHeaders(response, secure) {
  if (!secure) return;
  response.setHeader("Strict-Transport-Security", HSTS);
  response.setHeader("Permissions-Policy", PERMISSIONS_POLICY);
}

function empty(response, status, secure) {
  response.statusCode = status;
  response.setHeader("Content-Length", "0");
  securityHeaders(response, secure);
  response.end();
}

/**
 * @param {{
 *   backend: string,
 *   frontend: string,
 *   publicHosts: string[],
 *   tls?: import("node:https").ServerOptions,
 * }} options
 */
export function createProductionRouter({
  backend,
  frontend,
  publicHosts,
  tls = undefined,
}) {
  const destinations = {
    backend: validatedOrigin(backend, "backend"),
    frontend: validatedOrigin(frontend, "frontend"),
  };
  if (!Array.isArray(publicHosts) || publicHosts.length === 0)
    throw new Error("at least one public host is required");
  const allowedHosts = new Set(
    publicHosts.map((host) => {
      if (!host || /[\s/]/u.test(host)) throw new Error("invalid public host");
      return host.toLowerCase();
    }),
  );
  const secure = tls !== undefined;
  const handler = (clientRequest, clientResponse) => {
    if (
      !clientRequest.headers.host ||
      !allowedHosts.has(clientRequest.headers.host.toLowerCase())
    ) {
      empty(clientResponse, 421, secure);
      return;
    }
    let normalized;
    try {
      normalized = normalizeRequestTarget(clientRequest.url);
    } catch {
      empty(clientResponse, 400, secure);
      return;
    }
    const selected = selectUpstream(normalized.path);
    if (selected === "deny") {
      empty(clientResponse, 404, secure);
      return;
    }
    const destination = destinations[selected];
    const failUpstream = () => {
      if (clientResponse.destroyed || clientResponse.writableEnded) return;
      if (clientResponse.headersSent) clientResponse.destroy();
      else empty(clientResponse, 502, secure);
    };
    let upstream;
    try {
      upstream = (destination.protocol === "https:" ? sendHttps : sendHttp)(
        {
          headers: requestHeaders(clientRequest, selected === "frontend"),
          hostname: destination.hostname,
          method: clientRequest.method,
          path: normalized.requestTarget,
          port: destination.port,
          protocol: destination.protocol,
        },
        (upstreamResponse) => {
          upstreamResponse.on("error", failUpstream);
          for (
            let index = 0;
            index < upstreamResponse.rawHeaders.length;
            index += 2
          ) {
            const name = upstreamResponse.rawHeaders[index];
            const lowerName = name.toLowerCase();
            const value = upstreamResponse.rawHeaders[index + 1];
            if (
              hopByHop.has(lowerName) ||
              lowerName === "strict-transport-security" ||
              lowerName === "permissions-policy" ||
              (selected === "frontend" && lowerName === "set-cookie")
            )
              continue;
            clientResponse.appendHeader(name, value);
          }
          securityHeaders(clientResponse, secure);
          clientResponse.writeHead(upstreamResponse.statusCode ?? 502);
          upstreamResponse.pipe(clientResponse);
        },
      );
      clientRequest.on("aborted", () => upstream.destroy());
      clientRequest.pipe(upstream);
    } catch {
      failUpstream();
      return;
    }
    upstream.on("error", failUpstream);
  };
  const options = { maxHeaderSize: 16_384 };
  return secure
    ? createHttpsServer({ ...options, ...tls }, handler)
    : createHttpServer(options, handler);
}

export function loadTls(certFile, keyFile) {
  if (!certFile && !keyFile) return undefined;
  if (!certFile || !keyFile)
    throw new Error(
      "ROUTER_TLS_CERT_FILE and ROUTER_TLS_KEY_FILE must be set together",
    );
  return { cert: readFileSync(certFile), key: readFileSync(keyFile) };
}
