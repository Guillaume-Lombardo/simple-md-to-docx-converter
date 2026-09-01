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
    (request, response) => {
      filterGeneratedCsp(request, response);
      admission.handler(request, response);
    },
  );
  server.on("clientError", (_error, socket) => {
    if (socket.writable)
      socket.end(
        "HTTP/1.1 431 Request Header Fields Too Large\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
      );
  });
  return { admission, server };
}

function headerFrom(response, headers, name) {
  if (Array.isArray(headers)) {
    for (let index = 0; index < headers.length; index += 2)
      if (String(headers[index]).toLowerCase() === name)
        return String(headers[index + 1]);
  } else if (headers) {
    for (const [key, value] of Object.entries(headers))
      if (key.toLowerCase() === name) return String(value);
  }
  const value = response.getHeader(name);
  return value === undefined ? undefined : String(value);
}

function removeHeader(headers, name) {
  if (Array.isArray(headers)) {
    for (let index = headers.length - 2; index >= 0; index -= 2)
      if (String(headers[index]).toLowerCase() === name)
        headers.splice(index, 2);
  } else if (headers) {
    for (const key of Object.keys(headers))
      if (key.toLowerCase() === name) delete headers[key];
  }
}

function filterGeneratedCsp(request, response) {
  const writeHead = response.writeHead;
  response.writeHead = function filteredWriteHead(statusCode, reason, headers) {
    const suppliedHeaders = typeof reason === "string" ? headers : reason;
    const contentType = headerFrom(response, suppliedHeaders, "content-type")
      ?.split(";", 1)[0]
      ?.trim()
      .toLowerCase();
    const contentLength = headerFrom(
      response,
      suppliedHeaders,
      "content-length",
    );
    const prefetch =
      request.headers.rsc === "1" ||
      request.headers["next-router-prefetch"] !== undefined ||
      request.headers["next-router-segment-prefetch"] !== undefined ||
      request.headers.purpose?.toLowerCase() === "prefetch";
    const bodyless =
      request.method === "HEAD" ||
      statusCode === 204 ||
      statusCode === 304 ||
      contentLength === "0";
    if (contentType !== "text/html" || prefetch || bodyless) {
      response.removeHeader("content-security-policy");
      removeHeader(suppliedHeaders, "content-security-policy");
    }
    return typeof reason === "string"
      ? writeHead.call(this, statusCode, reason, headers)
      : writeHead.call(this, statusCode, reason);
  };
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

function strings(value) {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (value && typeof value === "object")
    return Object.values(value).flatMap(strings);
  return [];
}

async function clientReferences(nextRoot, routeFiles) {
  const manifests = [];
  const references = [];
  for (const routeFile of routeFiles) {
    const manifest = join(
      nextRoot,
      "server",
      routeFile.replace(/\.js$/, "_client-reference-manifest.js"),
    );
    const source = await readFile(manifest, "utf8");
    const match = source.match(/\]\s*=\s*(\{.*\});\s*$/s);
    if (!match) throw new Error("Invalid client-reference manifest");
    const parsed = JSON.parse(match[1]);
    manifests.push(manifest);
    for (const path of strings(parsed)) {
      if (path.startsWith("/_next/static/"))
        references.push(join(nextRoot, path.slice("/_next/".length)));
      else if (path.startsWith("static/") || path.startsWith("server/"))
        references.push(join(nextRoot, path));
    }
  }
  return { manifests, references };
}

export async function createBuildIntegrity(root) {
  const nextRoot = join(root, ".next");
  const required = [
    "BUILD_ID",
    "routes-manifest.json",
    "build-manifest.json",
    "server/app-paths-manifest.json",
  ];
  const buildManifest = JSON.parse(
    await readFile(join(nextRoot, "build-manifest.json"), "utf8"),
  );
  const routesManifest = JSON.parse(
    await readFile(join(nextRoot, "routes-manifest.json"), "utf8"),
  );
  const appPathsManifest = JSON.parse(
    await readFile(join(nextRoot, "server/app-paths-manifest.json"), "utf8"),
  );
  if (!Array.isArray(routesManifest.staticRoutes))
    throw new Error("Invalid routes manifest");
  for (const route of [
    ...routesManifest.staticRoutes,
    ...(routesManifest.dynamicRoutes ?? []),
  ]) {
    if (
      typeof route.page !== "string" ||
      (!Object.hasOwn(
        appPathsManifest,
        `${route.page === "/" ? "" : route.page}/page`,
      ) &&
        !Object.hasOwn(
          appPathsManifest,
          `${route.page === "/" ? "" : route.page}/route`,
        ))
    )
      throw new Error("Missing route artifact");
  }
  const referenced = [
    ...strings(buildManifest)
      .filter((path) => path.startsWith("static/"))
      .map((path) => join(nextRoot, path)),
    ...Object.values(appPathsManifest).map((path) =>
      join(nextRoot, "server", path),
    ),
  ];
  const client = await clientReferences(
    nextRoot,
    Object.values(appPathsManifest),
  );
  const staticRoot = join(nextRoot, "static");
  const staticFiles = await assetFiles(staticRoot);
  if (staticFiles.length === 0) throw new Error("No immutable assets found");
  const paths = [
    ...new Set([
      ...required.map((item) => join(nextRoot, item)),
      ...referenced,
      ...client.manifests,
      ...client.references,
      ...staticFiles.sort(),
    ]),
  ];
  const expected = new Map(
    await Promise.all(
      paths.map(async (path) => [relative(nextRoot, path), await digest(path)]),
    ),
  );
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
