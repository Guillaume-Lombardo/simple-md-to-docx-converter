import { createServer } from "node:http";

export const MAX_IN_FLIGHT = 128;
export const MAX_HEADER_SIZE = 16_384;
export const SHUTDOWN_TIMEOUT_MS = 30_000;

function empty(response, status) {
  response.writeHead(status, { "Content-Length": "0" });
  response.end();
}

export function createAdmission(handler, maximum = MAX_IN_FLIGHT) {
  let draining = false;
  let inFlight = 0;
  const admit = (request, response) => {
    if (draining || inFlight >= maximum) {
      empty(response, 503);
      return;
    }
    inFlight += 1;
    let accounted = false;
    const release = () => {
      if (accounted) return;
      accounted = true;
      inFlight -= 1;
    };
    response.once("finish", release);
    response.once("close", release);
    Promise.resolve(handler(request, response)).catch(() => {
      if (!response.headersSent) empty(response, 500);
      else response.destroy();
    });
  };
  return {
    handler: admit,
    drain() {
      draining = true;
    },
    get draining() {
      return draining;
    },
    get inFlight() {
      return inFlight;
    },
  };
}

export function createPageServer(handler) {
  const admission = createAdmission(handler);
  const server = createServer(
    { maxHeaderSize: MAX_HEADER_SIZE },
    admission.handler,
  );
  server.on("clientError", (_error, socket) => {
    if (socket.writable)
      socket.end(
        "HTTP/1.1 431 Request Header Fields Too Large\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
      );
  });
  return { admission, server };
}

export function createProbeServer(state) {
  return createServer((request, response) => {
    if (request.method !== "GET") return empty(response, 404);
    if (request.url === "/_frontend/health/live") return empty(response, 200);
    if (request.url === "/_frontend/health/ready")
      return empty(response, state.ready && !state.draining ? 200 : 503);
    return empty(response, 404);
  });
}

export async function closeWithin(servers, timeoutMs = SHUTDOWN_TIMEOUT_MS) {
  const close = Promise.all(
    servers.map((server) => new Promise((resolve) => server.close(resolve))),
  );
  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve("timeout"), timeoutMs);
  });
  const result = await Promise.race([close.then(() => "closed"), timeout]);
  clearTimeout(timer);
  if (result === "timeout")
    servers.forEach((server) => server.closeAllConnections());
  return result;
}
