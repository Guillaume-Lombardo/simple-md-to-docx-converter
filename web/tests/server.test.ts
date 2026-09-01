// @vitest-environment node
import { EventEmitter } from "node:events";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { request, type IncomingMessage, type ServerResponse } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  createBuildIntegrity,
  createAdmission,
  closeWithin,
  createPageServer,
  createProbeServer,
} from "../src/runtime/server.mjs";

function fakeResponse() {
  const response = new EventEmitter() as EventEmitter & {
    status?: number;
    headersSent: boolean;
    writeHead(status: number): void;
    end(): void;
    destroy(): void;
  };
  response.headersSent = false;
  response.writeHead = (status) => {
    response.status = status;
    response.headersSent = true;
  };
  response.end = () => response.emit("finish");
  response.destroy = () => response.emit("close");
  return response;
}

test("admission enforces exact 128 boundary and finish/close accounting once", () => {
  const admission = createAdmission(() => undefined);
  const responses = Array.from({ length: 128 }, () => fakeResponse());
  responses.forEach((item) => admission.handler({} as never, item as never));
  expect(admission.inFlight).toBe(128);
  const rejected = fakeResponse();
  admission.handler({} as never, rejected as never);
  expect(rejected.status).toBe(503);
  responses[0]!.emit("finish");
  responses[0]!.emit("close");
  expect(admission.inFlight).toBe(127);
});

test("draining rejects races without invoking the handler", () => {
  const handler = vi.fn();
  const admission = createAdmission(handler);
  admission.drain();
  const response = fakeResponse();
  admission.handler({} as never, response as never);
  expect(response.status).toBe(503);
  expect(handler).not.toHaveBeenCalled();
  expect(admission.draining).toBe(true);
});

test("handler failures close safely", async () => {
  const admission = createAdmission(() => {
    throw new Error("failure");
  });
  const response = fakeResponse();
  admission.handler({} as never, response as never);
  await new Promise(setImmediate);
  expect(response.status).toBe(500);
  expect(admission.inFlight).toBe(0);
});

async function listen(server: ReturnType<typeof createPageServer>["server"]) {
  server.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  return (server.address() as { port: number }).port;
}

test("real HTTP admission rejects request 129, drains, and has empty failures", async () => {
  const held: ServerResponse[] = [];
  const page = createPageServer(
    (_request: IncomingMessage, response: ServerResponse) =>
      void held.push(response),
  );
  const port = await listen(page.server);
  const pending = Array.from({ length: 128 }, () => get(port, "/hold"));
  await vi.waitFor(() => expect(page.admission.inFlight).toBe(128));
  expect(await get(port, "/overflow")).toEqual({
    body: "",
    csp: undefined,
    status: 503,
  });
  page.admission.drain();
  expect(await get(port, "/draining")).toEqual({
    body: "",
    csp: undefined,
    status: 503,
  });
  held.forEach((response) => response.end("ok"));
  await Promise.all(pending);
  await vi.waitFor(() => expect(page.admission.inFlight).toBe(0));
  await new Promise((resolve) => page.server.close(resolve));
});

test("synchronous handler throws become an empty HTTP 500", async () => {
  const page = createPageServer(() => {
    throw new Error("failure");
  });
  const port = await listen(page.server);
  expect(await get(port, "/")).toEqual({
    body: "",
    csp: undefined,
    status: 500,
  });
  expect(page.admission.inFlight).toBe(0);
  await new Promise((resolve) => page.server.close(resolve));
});

test("real HTTP shutdown is bounded and terminates held connections", async () => {
  const page = createPageServer(() => undefined);
  const port = await listen(page.server);
  const pending = get(port, "/hold").catch(() => ({
    body: "",
    csp: undefined,
    status: 0,
  }));
  await vi.waitFor(() => expect(page.admission.inFlight).toBe(1));
  await expect(closeWithin([page.server], 5)).resolves.toBe("timeout");
  await pending;
  await vi.waitFor(() => expect(page.admission.inFlight).toBe(0));
});

test("handler failure destroys a response whose headers were sent", async () => {
  const admission = createAdmission(
    async (_request: unknown, response: ReturnType<typeof fakeResponse>) => {
      response.headersSent = true;
      throw new Error("failure");
    },
  );
  const response = fakeResponse();
  admission.handler({} as never, response as never);
  await new Promise(setImmediate);
  expect(admission.inFlight).toBe(0);
});

test("bounded close reports clean completion", async () => {
  const server = {
    close: vi.fn((callback: () => void) => callback()),
    closeAllConnections: vi.fn(),
  };
  await expect(closeWithin([server] as never, 10)).resolves.toBe("closed");
  expect(server.closeAllConnections).not.toHaveBeenCalled();
});

test("bounded close terminates connections after its deadline", async () => {
  const server = { close: vi.fn(), closeAllConnections: vi.fn() };
  await expect(closeWithin([server] as never, 1)).resolves.toBe("timeout");
  expect(server.closeAllConnections).toHaveBeenCalledOnce();
});

async function get(
  port: number,
  path: string,
  headers?: Record<string, string>,
  method = "GET",
) {
  return new Promise<{ body: string; csp?: string; status: number }>(
    (resolve, reject) => {
      const call = request({ headers, method, path, port }, (result) => {
        let body = "";
        result.on("data", (chunk) => {
          body += chunk;
        });
        result.on("end", () =>
          resolve({
            body,
            csp: result.headers["content-security-policy"] as
              | string
              | undefined,
            status: result.statusCode!,
          }),
        );
      });
      call.on("error", reject);
      call.end();
    },
  );
}

test("probe server keeps exact internal paths content-free", async () => {
  const state = { draining: false, ready: false };
  const server = createProbeServer(state);
  server.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  const port = (server.address() as { port: number }).port;
  expect(await get(port, "/_frontend/health/live")).toEqual({
    body: "",
    csp: undefined,
    status: 200,
  });
  expect(await get(port, "/_frontend/health/ready")).toEqual({
    body: "",
    csp: undefined,
    status: 503,
  });
  state.ready = true;
  expect((await get(port, "/_frontend/health/ready")).status).toBe(200);
  state.draining = true;
  expect((await get(port, "/_frontend/health/ready")).status).toBe(503);
  expect((await get(port, "/health/live")).status).toBe(404);
  const post = await new Promise<number>((resolve, reject) => {
    const call = request(
      { method: "POST", path: "/_frontend/health/live", port },
      (result) => {
        result.resume();
        result.on("end", () => resolve(result.statusCode!));
      },
    );
    call.on("error", reject);
    call.end();
  });
  expect(post).toBe(404);
  server.close();
});

test("HTTP header overflow returns an empty 431", async () => {
  const page = createPageServer(
    (_request: IncomingMessage, result: ServerResponse) => {
      result.end("ok");
    },
  );
  page.server.listen(0, "127.0.0.1");
  await new Promise((resolve) => page.server.once("listening", resolve));
  const port = (page.server.address() as { port: number }).port;
  const result = await get(port, "/", { "x-large": "a".repeat(17_000) });
  expect(result).toEqual({ body: "", csp: undefined, status: 431 });
  page.server.close();
});

test("page server retains CSP only for emitted HTML documents", async () => {
  const page = createPageServer(
    (incoming: IncomingMessage, response: ServerResponse) => {
      response.setHeader("Content-Security-Policy", "generated");
      if (incoming.url === "/json") {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end("{}");
      } else if (incoming.url === "/empty") {
        response.writeHead(204, { "Content-Type": "text/html" });
        response.end();
      } else if (incoming.url === "/not-modified") {
        response.writeHead(304, { "Content-Type": "text/html" });
        response.end();
      } else if (incoming.url === "/zero") {
        response.writeHead(200, {
          "Content-Length": "0",
          "Content-Type": "text/html",
        });
        response.end();
      } else if (incoming.url === "/raw") {
        response.writeHead(200, [
          "Content-Type",
          "application/json",
          "Content-Security-Policy",
          "generated",
        ]);
        response.end("{}");
      } else if (incoming.url === "/status-message") {
        response.writeHead(200, "Fine", { "Content-Type": "text/html" });
        response.end("document");
      } else {
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end("document");
      }
    },
  );
  const port = await listen(page.server);
  expect((await get(port, "/html")).csp).toBe("generated");
  expect((await get(port, "/json")).csp).toBeUndefined();
  expect((await get(port, "/empty")).csp).toBeUndefined();
  expect(
    (await get(port, "/html", { Purpose: "prefetch" })).csp,
  ).toBeUndefined();
  expect((await get(port, "/html", { RSC: "1" })).csp).toBeUndefined();
  expect(
    (await get(port, "/html", { "Next-Router-Prefetch": "1" })).csp,
  ).toBeUndefined();
  expect(
    (await get(port, "/html", { "Next-Router-Segment-Prefetch": "1" })).csp,
  ).toBeUndefined();
  expect((await get(port, "/not-modified")).csp).toBeUndefined();
  expect((await get(port, "/zero")).csp).toBeUndefined();
  expect((await get(port, "/raw")).csp).toBeUndefined();
  expect((await get(port, "/status-message")).csp).toBe("generated");
  expect((await get(port, "/html", undefined, "HEAD")).csp).toBeUndefined();
  await new Promise((resolve) => page.server.close(resolve));
});

test("readiness checks route manifests and immutable asset integrity", async () => {
  const root = await mkdtemp(join(tmpdir(), "markweave-build-"));
  const staticRoot = join(root, ".next/static/chunks");
  const serverRoot = join(root, ".next/server/app/convert");
  await mkdir(staticRoot, { recursive: true });
  await mkdir(serverRoot, { recursive: true });
  await writeFile(join(root, ".next/BUILD_ID"), "build-id\n");
  const routes = '{"staticRoutes":[{"page":"/convert"}]}';
  await writeFile(join(root, ".next/routes-manifest.json"), routes);
  await writeFile(
    join(root, ".next/build-manifest.json"),
    '{"rootMainFiles":["static/chunks/app-abc.js"]}',
  );
  await writeFile(
    join(root, ".next/server/app-paths-manifest.json"),
    '{"/convert/page":"app/convert/page.js"}',
  );
  await writeFile(join(serverRoot, "page.js"), "route");
  const clientChunk = join(staticRoot, "route-client.js");
  const serverChunk = join(root, ".next/server/chunks/route-ssr.js");
  await mkdir(join(root, ".next/server/chunks"), { recursive: true });
  await writeFile(
    join(serverRoot, "page_client-reference-manifest.js"),
    'globalThis.__RSC_MANIFEST["/convert/page"] = {"clientModules":{"fixture":{"chunks":["/_next/static/chunks/route-client.js"]}},"ssrModuleMapping":{"fixture":{"chunks":["server/chunks/route-ssr.js"]}}};\n',
  );
  await writeFile(clientChunk, "client route");
  await writeFile(serverChunk, "server route");
  await writeFile(join(staticRoot, "unreferenced.js"), "present");
  await expect(createBuildIntegrity(root)).rejects.toThrow();
  const asset = join(staticRoot, "app-abc.js");
  await writeFile(asset, "original");
  const ready = await createBuildIntegrity(root);
  expect(await ready()).toBe(true);
  await rm(clientChunk);
  expect(await ready()).toBe(false);
  await writeFile(clientChunk, "client route");
  await writeFile(asset, "corrupt");
  expect(await ready()).toBe(false);
  await writeFile(asset, "original");
  await writeFile(join(staticRoot, "unexpected.js"), "unexpected");
  expect(await ready()).toBe(false);
  await rm(join(staticRoot, "unexpected.js"));
  await rm(join(root, ".next/routes-manifest.json"));
  expect(await ready()).toBe(false);
  await writeFile(join(root, ".next/routes-manifest.json"), routes);
  await writeFile(join(root, ".next/routes-manifest.json"), "{}");
  await expect(createBuildIntegrity(root)).rejects.toThrow(
    "Invalid routes manifest",
  );
  await writeFile(
    join(root, ".next/routes-manifest.json"),
    '{"staticRoutes":[{"page":"/missing"}]}',
  );
  await expect(createBuildIntegrity(root)).rejects.toThrow(
    "Missing route artifact",
  );
  await writeFile(join(root, ".next/routes-manifest.json"), routes);
  await writeFile(
    join(serverRoot, "page_client-reference-manifest.js"),
    "invalid",
  );
  await expect(createBuildIntegrity(root)).rejects.toThrow(
    "Invalid client-reference manifest",
  );
  await writeFile(
    join(serverRoot, "page_client-reference-manifest.js"),
    'globalThis.__RSC_MANIFEST["/convert/page"] = {};\n',
  );
  await writeFile(join(root, ".next/BUILD_ID"), "\n");
  await expect(createBuildIntegrity(root)).rejects.toThrow(
    "Empty build identifier",
  );
  await rm(root, { recursive: true });
});
