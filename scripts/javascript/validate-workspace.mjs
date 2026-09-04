import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const repository = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const { values } = parseArgs({
  options: { "list-json": { type: "string" } },
  strict: true,
});

const workspace = await readFile(resolve(repository, "pnpm-workspace.yaml"), "utf8");
assert.match(workspace, /^packages:\n  - "\."\n  - "web"\n  - "!spikes\/toolchain"\n/m);

const listing = values["list-json"]
  ? JSON.parse(await readFile(values["list-json"], "utf8"))
  : JSON.parse(
      execFileSync("pnpm", ["list", "--recursive", "--depth", "-1", "--json"], {
        cwd: repository,
        encoding: "utf8",
      }),
    );
assert.ok(Array.isArray(listing), "pnpm recursive listing must be an array");

const paths = listing.map((entry) => resolve(entry.path));
assert.deepEqual(
  new Set(paths),
  new Set([repository, resolve(repository, "web")]),
  "the root workspace must contain only the root browser tests and web package",
);
assert.ok(
  paths.every((path) => !path.startsWith(`${resolve(repository, "spikes/toolchain")}/`) && path !== resolve(repository, "spikes/toolchain")),
  "spikes/toolchain must remain outside the pnpm workspace",
);
