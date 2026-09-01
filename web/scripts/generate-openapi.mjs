import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const contract = resolve(root, "../openapi/v1.json");
const generator =
  process.env.MARKWEAVE_OPENAPI_TS_BIN ??
  resolve(root, "node_modules/.bin/openapi-ts");
const outputs = [
  resolve(root, "src/api/generated"),
  resolve(root, "tests/fixtures/generated"),
];
const check = process.argv.includes("--check");
const temporary = check
  ? mkdtempSync(join(tmpdir(), "markweave-openapi-"))
  : undefined;

function generate(output) {
  const result = spawnSync(
    generator,
    [
      "-i",
      contract,
      "-o",
      output,
      "-p",
      "@hey-api/typescript",
      "valibot",
      "--no-log-file",
      "--silent",
    ],
    { cwd: root, encoding: "utf8" },
  );
  if (result.error || result.status !== 0) {
    const failure = new Error(
      [result.stderr, result.stdout, result.error?.message]
        .filter((item) => typeof item === "string" && item.length > 0)
        .join("\n") || "OpenAPI generation failed",
    );
    failure.exitCode = result.status || 1;
    throw failure;
  }
}

try {
  if (!check) {
    outputs.forEach(generate);
  } else {
    const candidate = join(temporary, "generated");
    generate(candidate);
    for (const output of outputs) {
      for (const file of ["index.ts", "types.gen.ts", "valibot.gen.ts"]) {
        if (
          readFileSync(join(output, file), "utf8") !==
          readFileSync(join(candidate, file), "utf8")
        ) {
          console.error(
            `${output}/${file} is stale; run npm run bindings:generate`,
          );
          process.exitCode = 1;
        }
      }
    }
  }
} catch (error) {
  process.stderr.write(
    `${error instanceof Error ? error.message : String(error)}\n`,
  );
  process.exitCode = Number.isInteger(error?.exitCode) ? error.exitCode : 1;
} finally {
  if (temporary) rmSync(temporary, { force: true, recursive: true });
}
