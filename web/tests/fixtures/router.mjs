import { createServer, request as send } from "node:http";

const operations = new Set([
  "/health/live",
  "/health/ready",
  "/metrics",
  "/docs",
  "/redoc",
  "/openapi.json",
]);

function target(path) {
  const decoded = decodeURIComponent(path).toLowerCase();
  if (
    decoded === "/_frontend/health" ||
    decoded.startsWith("/_frontend/health/")
  )
    return "deny";
  if (path === "/api/v1" || path.startsWith("/api/v1/") || operations.has(path))
    return "backend";
  return "frontend";
}

export function createRoutingFixture({ backend, frontend }) {
  return createServer((clientRequest, clientResponse) => {
    const selected = target(
      new URL(clientRequest.url, "http://fixture").pathname,
    );
    if (selected === "deny") {
      clientResponse.writeHead(404, { "Content-Length": "0" });
      clientResponse.end();
      return;
    }
    const destination = new URL(selected === "backend" ? backend : frontend);
    const headers = { ...clientRequest.headers, host: destination.host };
    if (selected === "frontend") {
      delete headers.cookie;
      delete headers["x-forwarded-for"];
      delete headers["x-forwarded-host"];
      delete headers["x-forwarded-proto"];
    }
    const upstream = send(
      {
        headers,
        host: destination.hostname,
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
