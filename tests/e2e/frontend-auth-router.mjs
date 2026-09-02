import { createRoutingFixture } from "./routing-fixture.mjs";

const server = createRoutingFixture({
  backend: "http://127.0.0.1:8080",
  frontend: "http://frontend:3000",
  publicHosts: ["localhost:3100"],
});
server.listen(3100, "127.0.0.1", () =>
  process.stdout.write("frontend authentication router ready\n"),
);
for (const signal of ["SIGINT", "SIGTERM"])
  process.on(signal, () => server.close(() => process.exit(0)));
