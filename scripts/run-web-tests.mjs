import { spawn } from "node:child_process";

const child = spawn(
  process.execPath,
  [
    "--test",
    "--experimental-test-coverage",
    "--test-coverage-include=tests/e2e/browser-helpers.mjs",
    "--test-coverage-lines=90",
    "--test-coverage-branches=90",
    "--test-coverage-functions=90",
    "tests/javascript/*.test.js",
  ],
  { shell: false, stdio: ["ignore", "pipe", "pipe"] },
);

let diagnostics = "";
for (const stream of [child.stdout, child.stderr]) {
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    diagnostics = `${diagnostics}${chunk}`.slice(-1_000_000);
    (stream === child.stdout ? process.stdout : process.stderr).write(chunk);
  });
}

child.once("error", (error) => {
  process.stderr.write(`native browser tests failed to start: ${error.message}\n`);
  process.exitCode = 1;
});
child.once("close", (code, signal) => {
  if (
    code !== 0 ||
    signal !== null ||
    /Error: .* coverage does not meet threshold/u.test(diagnostics)
  )
    process.exitCode = 1;
});
