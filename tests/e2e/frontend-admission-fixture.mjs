import { existsSync, writeFileSync } from "node:fs";

import {
  closeWithin,
  createPageServer,
} from "/opt/markweave-web/src/runtime/server.mjs";

const evidence = "/evidence";
const held = [];
let admissionHighWater = 0;
let page;
page = createPageServer((_request, response) => {
  held.push(response);
  if (page.admission.inFlight > admissionHighWater) {
    admissionHighWater = page.admission.inFlight;
    writeFileSync(
      `${evidence}/frontend-admission-high-water`,
      `${admissionHighWater}\n`,
      { mode: 0o600 },
    );
  }
  if (page.admission.inFlight === 128)
    writeFileSync(`${evidence}/frontend-saturated`, "128\n", { mode: 0o600 });
});
writeFileSync(`${evidence}/frontend-admission-high-water`, "0\n", {
  mode: 0o600,
});
page.server.listen(3000, "0.0.0.0", () => {
  writeFileSync(`${evidence}/frontend-admission-ready`, "true\n", {
    mode: 0o600,
  });
});

let stopping = false;
process.on("SIGTERM", async () => {
  if (stopping) return;
  stopping = true;
  page.admission.drain();
  writeFileSync(`${evidence}/frontend-draining`, "true\n", { mode: 0o600 });
  while (!existsSync(`${evidence}/frontend-release`))
    await new Promise((resolve) => setTimeout(resolve, 25));
  for (const response of held) response.destroy();
  process.exitCode = (await closeWithin([page.server])) === "closed" ? 0 : 1;
});
