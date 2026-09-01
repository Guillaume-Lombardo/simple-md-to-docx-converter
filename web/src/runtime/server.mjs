import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { join, relative } from "node:path";

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
    Promise.resolve()
      .then(() => handler(request, response))
      .catch(() => {
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
  return createServer(async (request, response) => {
    if (request.method !== "GET") return empty(response, 404);
    if (request.url === "/_frontend/health/live") return empty(response, 200);
    if (request.url === "/_frontend/health/ready") {
      let ready = false;
      try {
        ready =
          typeof state.ready === "function"
            ? await state.ready()
            : Boolean(state.ready);
      } catch {}
      return empty(response, ready && !state.draining ? 200 : 503);
    }
    return empty(response, 404);
  });
}

async function assetFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await assetFiles(path)));
    else if (entry.isFile()) files.push(path);
  }
  return files;
}

async function digest(path) {
  return createHash("sha256")
    .update(await readFile(path))
    .digest("hex");
}

export async function createBuildIntegrity(root) {
  const nextRoot = join(root, ".next");
  const required = ["BUILD_ID", "routes-manifest.json", "build-manifest.json"];
  const staticRoot = join(nextRoot, "static");
  const staticFiles = await assetFiles(staticRoot);
  if (staticFiles.length === 0) throw new Error("No immutable assets found");
  const paths = [
    ...required.map((item) => join(nextRoot, item)),
    ...staticFiles.sort(),
  ];
  const expected = new Map(
    await Promise.all(
      paths.map(async (path) => [relative(nextRoot, path), await digest(path)]),
    ),
  );
  for (const manifest of required.filter((item) => item.endsWith(".json")))
    JSON.parse(await readFile(join(nextRoot, manifest), "utf8"));
  if (!(await readFile(join(nextRoot, "BUILD_ID"), "utf8")).trim())
    throw new Error("Empty build identifier");

  return async () => {
    try {
      for (const [path, expectedDigest] of expected) {
        if ((await digest(join(nextRoot, path))) !== expectedDigest)
          return false;
      }
      const currentAssets = (await assetFiles(staticRoot))
        .map((path) => relative(nextRoot, path))
        .sort();
      const expectedAssets = [...expected.keys()]
        .filter((path) => path.startsWith("static/"))
        .sort();
      return JSON.stringify(currentAssets) === JSON.stringify(expectedAssets);
    } catch {
      return false;
    }
  };
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
