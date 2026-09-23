import { beforeAll, test } from "vitest";

type FrameClient = typeof import("../src/composer/preview/frame-client");
let client: FrameClient;

beforeAll(async () => {
  document.body.innerHTML = `<main id="viewport">
    <div id="document"></div><nav id="slide-controls"></nav>
    <nav id="slide-thumbnails"></nav><span id="slide-position"></span>
    <button id="previous-slide"></button><button id="next-slide"></button>
  </main>`;
  client = await import("../src/composer/preview/frame-client");
});

test("nested traversal and control characters cannot address another Office package member", () => {
  for (const path of [
    "",
    "word/media/../document.xml",
    "word/media/../../private.xml",
    "word/media/preview\n.png",
    "word/media/preview\u001b.png",
  ])
    expect(() => client.assertArchivePath(path)).toThrow("unsafe path");
  expect(() =>
    client.assertArchivePath("word/media/ordinary-image.png"),
  ).not.toThrow();
});

test("image signatures reject extension-only claims and truncated magic bytes", () => {
  expect(
    client.validImageSignature("asset.jpeg", Uint8Array.of(255, 216)),
  ).toBe(false);
  expect(
    client.validImageSignature("asset.png", Uint8Array.of(137, 80, 78, 71)),
  ).toBe(false);
  expect(
    client.validImageSignature("asset.gif", new TextEncoder().encode("GIF87a")),
  ).toBe(false);
  expect(
    client.validImageSignature("asset.bin", Uint8Array.of(255, 216, 255)),
  ).toBe(false);
});

test("truncated PNG and GIF headers never invent image dimensions", () => {
  const png = new Uint8Array(23);
  png.set([137, 80, 78, 71, 13, 10, 26, 10]);
  const gif = new TextEncoder().encode("GIF89a");
  expect(client.validImageSignature("truncated.png", png)).toBe(true);
  expect(client.imagePixels("truncated.png", png)).toBe(0);
  expect(client.validImageSignature("truncated.gif", gif)).toBe(true);
  expect(client.imagePixels("truncated.gif", gif)).toBe(0);
});

test("JPEG fill markers are traversed, but a truncated frame cannot supply dimensions", () => {
  const withFill = Uint8Array.of(
    255,
    216,
    255,
    255,
    255,
    192,
    0,
    7,
    8,
    0,
    2,
    0,
    3,
    255,
    217,
  );
  expect(client.validImageSignature("photo.jpg", withFill)).toBe(true);
  expect(client.imagePixels("photo.jpg", withFill)).toBe(6);
  const truncated = Uint8Array.of(
    255,
    216,
    255,
    255,
    255,
    192,
    0,
    20,
    8,
    0,
    2,
    0,
    3,
  );
  expect(client.imagePixels("photo.jpg", truncated)).toBe(0);
});

test("an external relationship without a hyperlink type is rejected even in another XML namespace", () => {
  const external =
    '<r:Relationships xmlns:r="urn:arbitrary"><r:Relationship TargetMode="EXTERNAL" Target="https://example.invalid/pixel.png"/></r:Relationships>';
  expect(() => client.validateRelationships(external)).toThrow(
    "external resource",
  );
  const local =
    '<r:Relationships xmlns:r="urn:arbitrary"><r:Relationship TargetMode="Internal" Type="image" Target="../media/chart.png"/></r:Relationships>';
  expect(() => client.validateRelationships(local)).not.toThrow();
});
