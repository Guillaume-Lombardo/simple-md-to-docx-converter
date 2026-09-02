import { createProductionRouter, loadTls } from "./src/runtime/router.mjs";

const port = Number.parseInt(process.env.ROUTER_PORT || "8080", 10);
const host = process.env.ROUTER_HOST || "0.0.0.0";
const backend = process.env.BACKEND_ORIGIN;
const frontend = process.env.FRONTEND_ORIGIN;
const publicHosts = (process.env.PUBLIC_HOSTS || "")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);
if (
  !Number.isInteger(port) ||
  port < 1 ||
  port > 65_535 ||
  !backend ||
  !frontend
)
  throw new Error(
    "BACKEND_ORIGIN, FRONTEND_ORIGIN, PUBLIC_HOSTS, and a valid ROUTER_PORT are required",
  );
const server = createProductionRouter({
  backend,
  frontend,
  publicHosts,
  tls: loadTls(
    process.env.ROUTER_TLS_CERT_FILE,
    process.env.ROUTER_TLS_KEY_FILE,
  ),
});
server.once("error", (error) => {
  process.stderr.write(`production router failed: ${error.message}\n`);
  process.exitCode = 1;
});
server.listen(port, host, () => {
  process.stdout.write(`production router listening on ${host}:${port}\n`);
});
let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"])
  process.on(signal, () => {
    if (stopping) return;
    stopping = true;
    server.close(() => {
      process.exitCode = 0;
    });
    setTimeout(() => {
      process.exitCode = 1;
      server.closeAllConnections();
    }, 30_000).unref();
  });
