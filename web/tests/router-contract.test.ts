// @vitest-environment node
import { spawn } from "node:child_process";
import { createServer, request } from "node:http";
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
      const outbound = request(
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

test("synchronous upstream creation failures are contained without exiting", async () => {
  const router = createRoutingFixture({
    backend: "http://[invalid",
    frontend: "http://[invalid",
    publicHosts: ["converter.example"],
  });
  const origin = await listen(router);
  expect(await call(origin, "GET", "/missing%20path")).toEqual({
    body: "",
    cookies: [],
    status: 502,
  });
  expect(await call(origin, "GET", "/caf%C3%A9")).toEqual({
    body: "",
    cookies: [],
    status: 502,
  });
  await new Promise((resolve) => router.close(resolve));
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
