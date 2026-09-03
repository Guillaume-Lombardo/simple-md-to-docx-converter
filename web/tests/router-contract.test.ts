// @vitest-environment node
import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer, request } from "node:http";
import { request as secureRequest } from "node:https";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { connect as connectTls } from "node:tls";
import {
  createProductionRouter,
  HSTS,
  loadTls,
  normalizeRequestTarget,
  PERMISSIONS_POLICY,
  selectUpstream,
} from "../src/runtime/router.mjs";
import { createRoutingFixture } from "./fixtures/router.mjs";

type Capture = {
  cookie?: string;
  forwarded?: string;
  host?: string;
  method: string;
  origin?: string;
  path: string;
  xForwardedHost?: string;
};

async function listen(server: ReturnType<typeof createServer>) {
  server.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  return `http://127.0.0.1:${(server.address() as { port: number }).port}`;
}

async function call(
  origin: string,
  method: string,
  path: string,
  host = "converter.example",
) {
  const destination = new URL(origin);
  return new Promise<{ body: string; cookies: string[]; status: number }>(
    (resolve, reject) => {
      const outbound = (
        destination.protocol === "https:" ? secureRequest : request
      )(
        {
          headers: {
            cookie: "session=a; csrf=b",
            forwarded: "host=attacker.invalid",
            host,
            origin: "https://converter.example",
            "x-forwarded-host": "attacker.invalid",
          },
          host: destination.hostname,
          method,
          path,
          port: destination.port,
          rejectUnauthorized: false,
        },
        (response) => {
          let body = "";
          response.setEncoding("utf8");
          response.on("data", (chunk) => (body += chunk));
          response.on("aborted", () => reject(new Error("response aborted")));
          response.on("error", reject);
          response.on("end", () =>
            resolve({
              body,
              cookies: response.headers["set-cookie"] ?? [],
              status: response.statusCode!,
            }),
          );
        },
      );
      outbound.on("error", reject);
      outbound.end();
    },
  );
}

test("real router preserves backend credentials and isolates frontend credentials", async () => {
  const frontendCaptures: Capture[] = [];
  const backendCaptures: Capture[] = [];
  const upstream = (captures: Capture[], label: string) =>
    createServer((incoming, response) => {
      captures.push({
        cookie: incoming.headers.cookie,
        forwarded: incoming.headers.forwarded,
        host: incoming.headers.host,
        method: incoming.method!,
        origin: incoming.headers.origin,
        path: incoming.url!,
        xForwardedHost: incoming.headers["x-forwarded-host"] as
          | string
          | undefined,
      });
      response.setHeader("Set-Cookie", ["session=one; HttpOnly", "csrf=two"]);
      response.end(label);
    });
  const frontend = upstream(frontendCaptures, "frontend");
  const backend = upstream(backendCaptures, "backend");
  const frontendOrigin = await listen(frontend);
  const backendOrigin = await listen(backend);
  const router = createRoutingFixture({
    backend: backendOrigin,
    frontend: frontendOrigin,
    publicHosts: ["converter.example"],
  });
  const routerOrigin = await listen(router);

  for (const [method, path] of [
    ["GET", "/convert"],
    ["GET", "/_next/static/chunk.js"],
    ["GET", "/missing"],
    ["POST", "/convert"],
    ["PATCH", "/unknown"],
  ] as const) {
    expect(await call(routerOrigin, method, path)).toEqual({
      body: "frontend",
      cookies: [],
      status: 200,
    });
  }
  expect(
    frontendCaptures.map(
      ({ cookie, forwarded, host, method, origin, path, xForwardedHost }) => ({
        cookie,
        forwarded,
        host,
        method,
        origin,
        path,
        xForwardedHost,
      }),
    ),
  ).toEqual(
    [
      ["GET", "/convert"],
      ["GET", "/_next/static/chunk.js"],
      ["GET", "/missing"],
      ["POST", "/convert"],
      ["PATCH", "/unknown"],
    ].map(([method, path]) => ({
      cookie: undefined,
      forwarded: undefined,
      host: "converter.example",
      method,
      origin: "https://converter.example",
      path,
      xForwardedHost: undefined,
    })),
  );

  for (const path of [
    "/api/v1",
    "/api/v1/session",
    "/health/live",
    "/docs/oauth2-redirect",
  ]) {
    expect(await call(routerOrigin, "GET", path)).toEqual({
      body: "backend",
      cookies: ["session=one; HttpOnly", "csrf=two"],
      status: 200,
    });
  }
  expect(
    backendCaptures.every(
      (item) =>
        item.cookie === "session=a; csrf=b" &&
        item.forwarded === undefined &&
        item.host === "converter.example" &&
        item.origin === "https://converter.example" &&
        item.xForwardedHost === undefined,
    ),
  ).toBe(true);

  for (const [path, canonical] of [
    ["/missing%20path", "/missing%20path"],
    ["/caf%C3%A9", "/caf%C3%A9"],
    ["/caf%c3%a9", "/caf%C3%A9"],
    ["/missing%2520path", "/missing%2520path"],
    ["/literal%2525", "/literal%2525"],
    ["/literal%252efile", "/literal%252efile"],
    ["/%E2%98%83", "/%E2%98%83"],
  ] as const) {
    expect(await call(routerOrigin, "GET", path)).toEqual({
      body: "frontend",
      cookies: [],
      status: 200,
    });
    expect(frontendCaptures.at(-1)?.path).toBe(canonical);
  }

  for (const [path, canonical] of [
    ["/api/v1/items/hello%20world", "/api/v1/items/hello%20world"],
    ["/api/v1/items/100%25", "/api/v1/items/100%25"],
    ["/docs/caf%C3%A9", "/docs/caf%C3%A9"],
  ] as const) {
    expect(await call(routerOrigin, "GET", path)).toEqual({
      body: "backend",
      cookies: ["session=one; HttpOnly", "csrf=two"],
      status: 200,
    });
    expect(backendCaptures.at(-1)?.path).toBe(canonical);
  }

  for (const path of ["/api/v1/../convert", "/docs/%2e%2e/convert"]) {
    expect(await call(routerOrigin, "GET", path)).toEqual({
      body: "frontend",
      cookies: [],
      status: 200,
    });
  }
  expect(frontendCaptures.slice(-2).map((item) => item.path)).toEqual([
    "/api/convert",
    "/convert",
  ]);

  for (const path of [
    "/convert/../api/v1/session",
    "/convert/%2e%2e/api/v1/session",
    "/convert/%5c..%5c/api/v1/session",
    "/api/v1/%2e%2e/%2e%2e/docs/oauth2-redirect",
  ])
    expect(await call(routerOrigin, "GET", path)).toEqual({
      body: "backend",
      cookies: ["session=one; HttpOnly", "csrf=two"],
      status: 200,
    });
  expect(backendCaptures.slice(-4).map((item) => item.path)).toEqual([
    "/api/v1/session",
    "/api/v1/session",
    "/api/v1/session",
    "/docs/oauth2-redirect",
  ]);

  expect(await call(routerOrigin, "GET", "/api/v1/%ZZ")).toEqual({
    body: "",
    cookies: [],
    status: 400,
  });

  const capturesBeforeNestedTraversal =
    frontendCaptures.length + backendCaptures.length;
  for (const path of [
    "/convert/%252e%252e/api/v1/session",
    "/convert/%252fapi/v1/session",
    "/convert/%255capi/v1/session",
  ])
    expect(await call(routerOrigin, "GET", path)).toEqual({
      body: "",
      cookies: [],
      status: 400,
    });
  expect(frontendCaptures.length + backendCaptures.length).toBe(
    capturesBeforeNestedTraversal,
  );

  const capturesBeforeDenial = frontendCaptures.length + backendCaptures.length;
  for (const path of [
    "/_frontend/health",
    "/_frontend/health/live",
    "/_FRONTEND/HEALTH/live",
    "/%5ffrontend/health/live",
    "//_frontend//health//ready",
    "/x/../_frontend/health/ready",
  ])
    expect(await call(routerOrigin, "GET", path)).toEqual({
      body: "",
      cookies: [],
      status: 404,
    });
  expect(frontendCaptures.length + backendCaptures.length).toBe(
    capturesBeforeDenial,
  );

  expect(
    await call(routerOrigin, "GET", "/convert", "attacker.invalid"),
  ).toEqual({
    body: "",
    cookies: [],
    status: 421,
  });

  await Promise.all(
    [router, frontend, backend].map(
      (server) => new Promise((resolve) => server.close(resolve)),
    ),
  );
});

test("TLS routing owns exact response-wide security headers", async () => {
  const directory = mkdtempSync(join(tmpdir(), "markweave-router-tls-"));
  const key = join(directory, "key.pem");
  const cert = join(directory, "cert.pem");
  try {
    execFileSync(
      "openssl",
      [
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=converter.example",
        "-keyout",
        key,
        "-out",
        cert,
        "-days",
        "1",
      ],
      { stdio: "ignore" },
    );
    const upstream = createServer((_incoming, response) => {
      response.setHeader("Strict-Transport-Security", "max-age=1; preload");
      response.setHeader("Permissions-Policy", "camera=*");
      response.end("secure");
    });
    const upstreamOrigin = await listen(upstream);
    const router = createProductionRouter({
      backend: upstreamOrigin,
      frontend: upstreamOrigin,
      publicHosts: ["converter.example"],
      tls: { cert: readFileSync(cert), key: readFileSync(key) },
    });
    router.listen(0, "127.0.0.1");
    await new Promise((resolve) => router.once("listening", resolve));
    const origin = `https://127.0.0.1:${(router.address() as { port: number }).port}`;
    const response = await new Promise<{
      body: string;
      hsts: string | undefined;
      permissions: string | undefined;
      status: number;
    }>((resolve, reject) => {
      const outbound = secureRequest(
        origin,
        {
          headers: { host: "converter.example" },
          rejectUnauthorized: false,
        },
        (incoming) => {
          let body = "";
          incoming.setEncoding("utf8");
          incoming.on("data", (chunk) => (body += chunk));
          incoming.on("end", () => {
            const hsts = incoming.headers["strict-transport-security"];
            const permissions = incoming.headers["permissions-policy"];
            resolve({
              body,
              hsts: Array.isArray(hsts) ? hsts.join(", ") : hsts,
              permissions: Array.isArray(permissions)
                ? permissions.join(", ")
                : permissions,
              status: incoming.statusCode!,
            });
          });
        },
      );
      outbound.on("error", reject);
      outbound.end();
    });
    expect(response).toEqual({
      body: "secure",
      hsts: HSTS,
      permissions: PERMISSIONS_POLICY,
      status: 200,
    });
    await Promise.all(
      [router, upstream].map(
        (server) => new Promise((resolve) => server.close(resolve)),
      ),
    );
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test("TLS header overflow returns a bounded secured 431", async () => {
  const directory = mkdtempSync(join(tmpdir(), "markweave-router-overflow-"));
  const key = join(directory, "key.pem");
  const cert = join(directory, "cert.pem");
  let router: ReturnType<typeof createProductionRouter> | undefined;
  try {
    execFileSync(
      "openssl",
      [
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=converter.example",
        "-keyout",
        key,
        "-out",
        cert,
        "-days",
        "1",
      ],
      { stdio: "ignore" },
    );
    router = createProductionRouter({
      backend: "http://127.0.0.1:1",
      frontend: "http://127.0.0.1:1",
      publicHosts: ["converter.example"],
      tls: { cert: readFileSync(cert), key: readFileSync(key) },
    });
    const activeRouter = router;
    activeRouter.listen(0, "127.0.0.1");
    await new Promise((resolve) => activeRouter.once("listening", resolve));
    const port = (activeRouter.address() as { port: number }).port;
    const response = await new Promise<string>((resolve, reject) => {
      const socket = connectTls(
        { host: "127.0.0.1", port, rejectUnauthorized: false },
        () => {
          socket.write(
            `GET / HTTP/1.1\r\nHost: converter.example\r\nX-Large: ${"a".repeat(17_000)}\r\n\r\n`,
          );
        },
      );
      let received = "";
      socket.setEncoding("utf8");
      socket.on("data", (chunk) => (received += chunk));
      socket.on("end", () => resolve(received));
      socket.on("error", reject);
    });
    expect(response).toBe(
      "HTTP/1.1 431 Request Header Fields Too Large\r\n" +
        "Connection: close\r\n" +
        "Content-Length: 0\r\n" +
        `Strict-Transport-Security: ${HSTS}\r\n` +
        `Permissions-Policy: ${PERMISSIONS_POLICY}\r\n\r\n`,
    );
  } finally {
    if (router?.listening) {
      const activeRouter = router;
      await new Promise((resolve) => activeRouter.close(resolve));
    }
    rmSync(directory, { force: true, recursive: true });
  }
});

test("invalid production origins fail before the router listens", () => {
  expect(() =>
    createRoutingFixture({
      backend: "http://[invalid",
      frontend: "http://[invalid",
      publicHosts: ["converter.example"],
    }),
  ).toThrow("Invalid URL");
  for (const backend of [
    "ftp://backend.example",
    "http://user@backend.example",
    "http://backend.example/path",
    "http://backend.example/?query=1",
    "http://backend.example/#fragment",
  ])
    expect(() =>
      createRoutingFixture({
        backend,
        frontend: "http://frontend.example",
        publicHosts: ["converter.example"],
      }),
    ).toThrow("must be an HTTP(S) origin");
  expect(() =>
    createRoutingFixture({
      backend: "http://backend.example",
      frontend: "http://frontend.example",
      publicHosts: [],
    }),
  ).toThrow("at least one public host");
  expect(() =>
    createRoutingFixture({
      backend: "http://backend.example",
      frontend: "http://frontend.example",
      publicHosts: ["bad host"],
    }),
  ).toThrow("invalid public host");
  for (const maxRequestBytes of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])
    expect(() =>
      createRoutingFixture({
        backend: "http://backend.example",
        frontend: "http://frontend.example",
        maxRequestBytes,
        publicHosts: ["converter.example"],
      }),
    ).toThrow("positive safe integer");
  for (const upstreamTimeoutMs of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])
    expect(() =>
      createRoutingFixture({
        backend: "http://backend.example",
        frontend: "http://frontend.example",
        publicHosts: ["converter.example"],
        upstreamTimeoutMs,
      }),
    ).toThrow("positive safe integer");
});

test("request transport ceiling rejects streamed bodies without an error body", async () => {
  let completedBodies = 0;
  const upstream = createServer((incoming, response) => {
    incoming.on("data", () => undefined);
    incoming.on("end", () => {
      completedBodies += 1;
      response.end("accepted");
    });
  });
  const upstreamOrigin = await listen(upstream);
  const router = createProductionRouter({
    backend: upstreamOrigin,
    frontend: upstreamOrigin,
    maxRequestBytes: 8,
    publicHosts: ["converter.example"],
  });
  const routerOrigin = await listen(router);
  const sendBody = (chunks: string[]) =>
    new Promise<{ body: string; status: number }>((resolve, reject) => {
      const destination = new URL(routerOrigin);
      const outbound = request(
        {
          headers: {
            host: "converter.example",
            "transfer-encoding": "chunked",
          },
          host: destination.hostname,
          method: "POST",
          path: "/api/v1/conversions",
          port: destination.port,
        },
        (response) => {
          let body = "";
          response.setEncoding("utf8");
          response.on("data", (chunk) => (body += chunk));
          response.on("end", () =>
            resolve({ body, status: response.statusCode! }),
          );
        },
      );
      outbound.on("error", reject);
      for (const chunk of chunks) outbound.write(chunk);
      outbound.end();
    });

  await expect(sendBody(["1234", "5678"])).resolves.toEqual({
    body: "accepted",
    status: 200,
  });
  await expect(sendBody(["1234", "5678", "9"])).resolves.toEqual({
    body: "",
    status: 413,
  });
  expect(completedBodies).toBe(1);
  await Promise.all(
    [router, upstream].map(
      (server) => new Promise((resolve) => server.close(resolve)),
    ),
  );
});

test("request ceiling wins when an upstream responds before the full body", async () => {
  const upstream = createServer((incoming, response) => {
    if (incoming.url === "/ok") {
      response.writeHead(202, { "Content-Type": "text/plain" });
      response.end("premature acceptance");
      return;
    }
    incoming.once("data", () => {
      response.writeHead(202, { "Content-Type": "text/plain" });
      response.end("premature acceptance");
    });
  });
  const upstreamOrigin = await listen(upstream);
  const router = createProductionRouter({
    backend: upstreamOrigin,
    frontend: upstreamOrigin,
    maxRequestBytes: 8,
    publicHosts: ["converter.example"],
  });
  const routerOrigin = await listen(router);
  const rejected = await new Promise<{ body: string; status: number }>(
    (resolve, reject) => {
      const destination = new URL(routerOrigin);
      const outbound = request(
        {
          headers: {
            host: "converter.example",
            "transfer-encoding": "chunked",
          },
          host: destination.hostname,
          method: "POST",
          path: "/api/v1/conversions",
          port: destination.port,
        },
        (response) => {
          let body = "";
          response.setEncoding("utf8");
          response.on("data", (chunk) => (body += chunk));
          response.on("end", () =>
            resolve({ body, status: response.statusCode! }),
          );
        },
      );
      outbound.on("error", reject);
      outbound.write("1234");
      setTimeout(() => outbound.end("56789"), 20);
    },
  );
  expect(rejected).toEqual({ body: "", status: 413 });
  await expect(call(routerOrigin, "GET", "/ok")).resolves.toEqual({
    body: "premature acceptance",
    cookies: [],
    status: 202,
  });
  await Promise.all(
    [router, upstream].map(
      (server) => new Promise((resolve) => server.close(resolve)),
    ),
  );
});

test("stalled upstreams time out and downstream disconnects abort upstream work", async () => {
  let activeUpstreamRequests = 0;
  let observeDisconnect: (() => void) | undefined;
  const disconnected = new Promise<void>((resolve) => {
    observeDisconnect = resolve;
  });
  const upstream = createServer((incoming) => {
    activeUpstreamRequests += 1;
    incoming.on("close", () => {
      activeUpstreamRequests -= 1;
      observeDisconnect?.();
    });
  });
  const upstreamOrigin = await listen(upstream);
  const router = createProductionRouter({
    backend: upstreamOrigin,
    frontend: upstreamOrigin,
    publicHosts: ["converter.example"],
    upstreamTimeoutMs: 30,
  });
  const routerOrigin = await listen(router);
  await expect(call(routerOrigin, "GET", "/convert")).resolves.toEqual({
    body: "",
    cookies: [],
    status: 502,
  });
  await disconnected;

  const secondDisconnected = new Promise<void>((resolve) => {
    observeDisconnect = resolve;
  });
  let observeSecondRequest: (() => void) | undefined;
  const secondRequest = new Promise<void>((resolve) => {
    observeSecondRequest = resolve;
  });
  upstream.once("request", () => observeSecondRequest?.());
  const destination = new URL(routerOrigin);
  const abandoned = request({
    headers: { host: "converter.example" },
    host: destination.hostname,
    path: "/convert",
    port: destination.port,
  });
  abandoned.on("error", () => undefined);
  abandoned.end();
  await secondRequest;
  abandoned.destroy();
  await secondDisconnected;
  expect(activeUpstreamRequests).toBe(0);
  await Promise.all(
    [router, upstream].map(
      (server) => new Promise((resolve) => server.close(resolve)),
    ),
  );
});

test("routing selection and normalization cover every ordered class", () => {
  expect(selectUpstream("/api/v1")).toBe("backend");
  expect(selectUpstream("/api/v1/jobs/1")).toBe("backend");
  expect(selectUpstream("/health/live")).toBe("backend");
  expect(selectUpstream("/metrics")).toBe("backend");
  expect(selectUpstream("/docs/redirect")).toBe("backend");
  expect(selectUpstream("/_FRONTEND/HEALTH/ready")).toBe("deny");
  expect(selectUpstream("/API/v1")).toBe("frontend");
  expect(selectUpstream("/missing")).toBe("frontend");
  expect(normalizeRequestTarget("/a/./b/../c?q=1")).toEqual({
    path: "/a/c",
    requestTarget: "/a/c?q=1",
  });
  expect(normalizeRequestTarget("/a%5Cb")).toEqual({
    path: "/a/b",
    requestTarget: "/a/b",
  });
});

test("TLS files are optional only as a complete pair", () => {
  expect(loadTls(undefined, undefined)).toBeUndefined();
  expect(() => loadTls("cert.pem", undefined)).toThrow("must be set together");
  expect(() => loadTls(undefined, "key.pem")).toThrow("must be set together");
});

test("upstream failures are empty before headers and destroy started bodies", async () => {
  const brokenOrigin = await new Promise<string>((resolve) => {
    const temporary = createServer();
    temporary.listen(0, "127.0.0.1", () => {
      const port = (temporary.address() as { port: number }).port;
      temporary.close(() => resolve(`http://127.0.0.1:${port}`));
    });
  });
  const unavailableRouter = createRoutingFixture({
    backend: brokenOrigin,
    frontend: brokenOrigin,
    publicHosts: ["converter.example"],
  });
  const unavailableOrigin = await listen(unavailableRouter);
  for (let attempt = 0; attempt < 2; attempt += 1)
    expect(await call(unavailableOrigin, "GET", "/convert")).toEqual({
      body: "",
      cookies: [],
      status: 502,
    });
  await new Promise((resolve) => unavailableRouter.close(resolve));

  const upstream = createServer((incoming, response) => {
    if (incoming.url === "/break") {
      response.writeHead(200, { "Content-Type": "text/plain" });
      response.write("partial");
      response.flushHeaders();
      setImmediate(() => response.destroy(new Error("upstream failed")));
    } else response.end("survived");
  });
  const upstreamOrigin = await listen(upstream);
  const router = createRoutingFixture({
    backend: upstreamOrigin,
    frontend: upstreamOrigin,
    publicHosts: ["converter.example"],
  });
  const routerOrigin = await listen(router);
  await expect(call(routerOrigin, "GET", "/break")).rejects.toThrow();
  await expect(call(routerOrigin, "GET", "/ok")).resolves.toEqual({
    body: "survived",
    cookies: [],
    status: 200,
  });
  await Promise.all(
    [router, upstream].map(
      (server) => new Promise((resolve) => server.close(resolve)),
    ),
  );
});

test("routing fixture CLI reports listener startup failures", async () => {
  const occupied = createServer();
  const occupiedOrigin = await listen(occupied);
  const port = new URL(occupiedOrigin).port;
  const child = spawn(process.execPath, ["scripts/routing-fixture.mjs"], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      FRONTEND_ORIGIN: "http://127.0.0.1:1",
      PUBLIC_HOST: `127.0.0.1:${port}`,
      ROUTER_PORT: port,
    },
    stdio: ["ignore", "ignore", "pipe"],
  });
  let diagnostics = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => (diagnostics += chunk));
  const code = await new Promise<number | null>((resolve) =>
    child.once("close", resolve),
  );
  expect(code).toBe(1);
  expect(diagnostics).toMatch(/routing fixture failed:.*EADDRINUSE/);
  await new Promise((resolve) => occupied.close(resolve));
});
