import next from "next";
import {
  closeWithin,
  createBuildIntegrity,
  createPageServer,
  createProbeServer,
} from "./src/runtime/server.mjs";

const host = process.env.HOSTNAME || "0.0.0.0";
const pagePort = Number.parseInt(process.env.PORT || "3000", 10);
const probePort = Number.parseInt(process.env.PROBE_PORT || "3001", 10);
const state = { draining: false, ready: async () => false };
const app = next({
  dev: false,
  dir: import.meta.dirname,
  hostname: host,
  port: pagePort,
});
await app.prepare();
state.ready = await createBuildIntegrity(import.meta.dirname);

const page = createPageServer(app.getRequestHandler());
const probe = createProbeServer(state);
page.server.listen(pagePort, host);
probe.listen(probePort, host);

let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  state.draining = true;
  page.admission.drain();
  const result = await closeWithin([page.server, probe]);
  process.exitCode = result === "closed" ? 0 : 1;
}
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
