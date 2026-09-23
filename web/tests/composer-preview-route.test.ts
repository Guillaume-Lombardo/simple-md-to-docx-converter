// @vitest-environment node
import { NextRequest } from "next/server";
import { GET } from "../app/composer-preview/route";
import { proxy } from "../proxy";

test("only the Composer parent receives frame-src self", () => {
  const composer = proxy(new NextRequest("https://markweave.example/composer"));
  const settings = proxy(
    new NextRequest("https://markweave.example/composer/connections"),
  );
  const convert = proxy(new NextRequest("https://markweave.example/convert"));
  expect(composer.headers.get("content-security-policy")).toContain(
    "frame-src 'self'",
  );
  expect(settings.headers.get("content-security-policy")).not.toContain(
    "frame-src",
  );
  expect(convert.headers.get("content-security-policy")).not.toContain(
    "frame-src",
  );
});

test("opaque child has a separate fresh nonce, no network, and classic self-hosted scripts", async () => {
  const first = GET();
  const second = GET();
  const policy = first.headers.get("content-security-policy")!;
  const nonce = policy.match(/script-src 'nonce-([^']+)'/)?.[1];
  expect(nonce).toBeTruthy();
  expect(policy).toContain("sandbox allow-scripts");
  expect(policy).toContain("frame-ancestors 'self'");
  expect(policy).toContain("connect-src 'none'");
  expect(policy).toContain("worker-src 'none'");
  expect(policy).toContain("form-action 'none'");
  expect(policy).toContain("img-src data: blob:");
  expect(policy).toContain("style-src 'unsafe-inline'");
  expect(policy).not.toContain("allow-same-origin");
  expect(policy).not.toContain("'unsafe-eval'");
  expect(policy).not.toContain("strict-dynamic");
  expect(second.headers.get("content-security-policy")).not.toBe(policy);
  const html = await first.text();
  const scripts = Array.from(
    html.matchAll(/<script\b([^>]*)>/g),
    (match) => match[1],
  );
  expect(scripts).toHaveLength(4);
  for (const attributes of scripts) {
    expect(attributes).toContain(`nonce="${nonce}"`);
    expect(attributes).toMatch(/src="\/composer-preview\/[^" ]+\.js"/);
    expect(attributes).not.toMatch(/type="module"/);
  }
});
