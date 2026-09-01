// @vitest-environment node
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
