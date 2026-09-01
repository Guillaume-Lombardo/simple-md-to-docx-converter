import { createServer, request as send } from "node:http";

const exactOperations = new Set([
  "/health/live",
  "/health/ready",
  "/metrics",
  "/docs",
  "/redoc",
  "/openapi.json",
]);

function normalize(requestTarget) {
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

function target(path) {
  const decoded = path.toLowerCase();
  if (
    decoded === "/_frontend/health" ||
    decoded.startsWith("/_frontend/health/")
  )
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

export function createRoutingFixture({ backend, frontend, publicHosts }) {
  const allowedHosts = new Set(publicHosts.map((host) => host.toLowerCase()));
  return createServer((clientRequest, clientResponse) => {
    if (
      !clientRequest.headers.host ||
      !allowedHosts.has(clientRequest.headers.host.toLowerCase())
    ) {
      clientResponse.writeHead(421, { "Content-Length": "0" });
      clientResponse.end();
      return;
    }
    let normalized;
    try {
      normalized = normalize(clientRequest.url);
    } catch {
      clientResponse.writeHead(400, { "Content-Length": "0" });
      clientResponse.end();
      return;
    }
    const selected = target(normalized.path);
    if (selected === "deny") {
      clientResponse.writeHead(404, { "Content-Length": "0" });
      clientResponse.end();
      return;
    }
    const headers = { ...clientRequest.headers };
    for (const name of Object.keys(headers))
      if (name === "forwarded" || name.startsWith("x-forwarded-"))
        delete headers[name];
    if (selected === "frontend") {
      delete headers.cookie;
    }
    const failUpstream = () => {
      if (clientResponse.destroyed || clientResponse.writableEnded) return;
      if (clientResponse.headersSent) clientResponse.destroy();
      else {
        clientResponse.writeHead(502, { "Content-Length": "0" });
        clientResponse.end();
      }
    };
    let upstream;
    try {
      const destination = new URL(selected === "backend" ? backend : frontend);
      upstream = send(
        {
          headers,
          hostname: destination.hostname,
          method: clientRequest.method,
          path: normalized.requestTarget,
          port: destination.port,
        },
        (upstreamResponse) => {
          upstreamResponse.on("error", failUpstream);
          for (
            let index = 0;
            index < upstreamResponse.rawHeaders.length;
            index += 2
          ) {
            const name = upstreamResponse.rawHeaders[index];
            const value = upstreamResponse.rawHeaders[index + 1];
            if (selected === "frontend" && name.toLowerCase() === "set-cookie")
              continue;
            clientResponse.appendHeader(name, value);
          }
          clientResponse.writeHead(upstreamResponse.statusCode);
          upstreamResponse.pipe(clientResponse);
        },
      );
      clientRequest.pipe(upstream);
    } catch {
      failUpstream();
      return;
    }
    upstream.on("error", failUpstream);
  });
}
