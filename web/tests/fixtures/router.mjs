import { createServer, request as send } from "node:http";

const exactOperations = new Set([
  "/health/live",
  "/health/ready",
  "/metrics",
  "/docs",
  "/redoc",
  "/openapi.json",
]);

function normalize(path) {
  let decoded = path;
  try {
    for (let count = 0; count < 2; count += 1)
      decoded = decodeURIComponent(decoded);
  } catch {
    return path.toLowerCase();
  }
  const parts = [];
  for (const part of decoded.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return `/${parts.join("/")}`.toLowerCase();
}

function target(path) {
  const decoded = normalize(path);
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
    const selected = target(clientRequest.url.split(/[?#]/, 1)[0]);
    if (selected === "deny") {
      clientResponse.writeHead(404, { "Content-Length": "0" });
      clientResponse.end();
      return;
    }
    const destination = new URL(selected === "backend" ? backend : frontend);
    const headers = { ...clientRequest.headers };
    for (const name of Object.keys(headers))
      if (name === "forwarded" || name.startsWith("x-forwarded-"))
        delete headers[name];
    if (selected === "frontend") {
      delete headers.cookie;
    }
    const upstream = send(
      {
        headers,
        hostname: destination.hostname,
        method: clientRequest.method,
        path: clientRequest.url,
        port: destination.port,
      },
      (upstreamResponse) => {
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
    upstream.on("error", () => {
      clientResponse.writeHead(502, { "Content-Length": "0" });
      clientResponse.end();
    });
    clientRequest.pipe(upstream);
  });
}
