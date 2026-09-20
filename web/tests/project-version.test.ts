// @vitest-environment node

import { projectVersion, readProjectVersion } from "../project-version.mjs";

test("release version comes from project metadata, not other tables", () => {
  expect(
    projectVersion(
      '[tool.example]\nversion = "9.9.9"\n[project]\nname = "markweave"\nversion = "1.2.3"\n[tool.other]\nversion = "8.8.8"',
    ),
  ).toBe("1.2.3");
  expect(readProjectVersion()).toBe(process.env.NEXT_PUBLIC_MARKWEAVE_VERSION);
});

test.each(["0.6.4", "1.0.0rc1", "1.0.0.dev2", "1.0.0.post1"])(
  "accepts release %s",
  (version) => {
    expect(projectVersion(`[project]\nversion = "${version}"`)).toBe(version);
  },
);

test.each([
  "",
  '[tool.example]\nversion = "1.2.3"',
  '[project]\nname = "markweave"\n[tool.example]\nversion = "1.2.3"',
  '[project]\nversion = "<script>"',
])("rejects missing or invalid project metadata: %s", (metadata) => {
  expect(() => projectVersion(metadata)).toThrow(
    "Missing or invalid Markweave project version",
  );
});
