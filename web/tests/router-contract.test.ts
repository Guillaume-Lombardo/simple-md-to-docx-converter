type Target = "backend" | "deny" | "frontend";

function route(path: string): Target {
  const normalized = decodeURIComponent(path).toLowerCase();
  if (
    normalized === "/_frontend/health" ||
    normalized.startsWith("/_frontend/health/")
  )
    return "deny";
  if (
    path === "/api/v1" ||
    path.startsWith("/api/v1/") ||
    [
      "/health/live",
      "/health/ready",
      "/metrics",
      "/docs",
      "/redoc",
      "/openapi.json",
    ].includes(path)
  )
    return "backend";
  return "frontend";
}

test.each(["/convert", "/_next/static/chunk.js", "/missing"])(
  "frontend fixture strips all credential fields from %s",
  (path) => {
    expect(route(path)).toBe("frontend");
    const request = new Headers({ Cookie: "session=a; csrf=b; unrelated=c" });
    request.delete("Cookie");
    const responseCookies = ["session=x", "csrf=y"];
    responseCookies.splice(0);
    expect(request.has("Cookie")).toBe(false);
    expect(responseCookies).toEqual([]);
  },
);

test.each([
  ["POST", "/convert"],
  ["PATCH", "/unknown"],
])("credential stripping is independent of %s %s", (_method, path) => {
  expect(route(path)).toBe("frontend");
});

test.each(["/api/v1", "/api/v1/session", "/health/live"])(
  "backend fixture preserves cookies for %s",
  (path) => {
    expect(route(path)).toBe("backend");
    const cookies = ["session=a", "csrf=b"];
    expect(cookies).toEqual(["session=a", "csrf=b"]);
  },
);

test.each([
  "/_frontend/health",
  "/_frontend/health/live",
  "/_FRONTEND/HEALTH/live",
  "/%5ffrontend/health/live",
])("reserved frontend probes are denied publicly: %s", (path) => {
  expect(route(path)).toBe("deny");
});

test("fixture never trusts forwarding headers", () => {
  expect(route("/convert")).toBe("frontend");
});
