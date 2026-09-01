// @vitest-environment node
import { createServer, request } from "node:http";
import { createRoutingFixture } from "./fixtures/router.mjs";

type Capture = { cookie?: string; method: string; path: string };

async function listen(server: ReturnType<typeof createServer>) {
  server.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  return `http://127.0.0.1:${(server.address() as { port: number }).port}`;
}

async function call(origin: string, method: string, path: string) {
  const destination = new URL(origin);
  return new Promise<{ body: string; cookies: string[]; status: number }>(
    (resolve, reject) => {
      const outbound = request(
        {
          headers: {
            cookie: "session=a; csrf=b",
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
        method: incoming.method!,
        path: incoming.url!,
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
  expect(frontendCaptures).toEqual([
    { cookie: undefined, method: "GET", path: "/convert" },
    { cookie: undefined, method: "GET", path: "/_next/static/chunk.js" },
    { cookie: undefined, method: "GET", path: "/missing" },
    { cookie: undefined, method: "POST", path: "/convert" },
    { cookie: undefined, method: "PATCH", path: "/unknown" },
  ]);

  for (const path of ["/api/v1", "/api/v1/session", "/health/live"]) {
    expect(await call(routerOrigin, "GET", path)).toEqual({
      body: "backend",
      cookies: ["session=one; HttpOnly", "csrf=two"],
      status: 200,
    });
  }
  expect(
    backendCaptures.every((item) => item.cookie === "session=a; csrf=b"),
  ).toBe(true);

  await Promise.all(
    [router, frontend, backend].map(
      (server) => new Promise((resolve) => server.close(resolve)),
    ),
  );
});
