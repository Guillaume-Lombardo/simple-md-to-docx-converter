import { createRoutingFixture } from "../tests/fixtures/router.mjs";

const port = Number.parseInt(process.env.ROUTER_PORT, 10);
const publicHost = process.env.PUBLIC_HOST;
const frontend = process.env.FRONTEND_ORIGIN;
if (!Number.isInteger(port) || !publicHost || !frontend)
  throw new Error("ROUTER_PORT, PUBLIC_HOST, and FRONTEND_ORIGIN are required");
const server = createRoutingFixture({
  backend: frontend,
  frontend,
  publicHosts: [publicHost],
});
server.once("error", (error) => {
  process.stderr.write(`routing fixture failed: ${error.message}\n`);
  process.exitCode = 1;
});
server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`routing fixture listening on 127.0.0.1:${port}\n`);
});
for (const signal of ["SIGINT", "SIGTERM"])
  process.on(signal, () => server.close(() => process.exit(0)));
