// @vitest-environment node
import { EventEmitter } from "node:events";
import { request, type IncomingMessage, type ServerResponse } from "node:http";
import {
  createAdmission,
  closeWithin,
  createPageServer,
  createProbeServer,
} from "../src/runtime/server.mjs";

function fakeResponse() {
  const response = new EventEmitter() as EventEmitter & {
    status?: number;
    headersSent: boolean;
    writeHead(status: number): void;
    end(): void;
    destroy(): void;
  };
  response.headersSent = false;
  response.writeHead = (status) => {
    response.status = status;
    response.headersSent = true;
  };
  response.end = () => response.emit("finish");
  response.destroy = () => response.emit("close");
  return response;
}

test("admission enforces exact 128 boundary and finish/close accounting once", () => {
  const admission = createAdmission(() => undefined);
  const responses = Array.from({ length: 128 }, () => fakeResponse());
  responses.forEach((item) => admission.handler({} as never, item as never));
  expect(admission.inFlight).toBe(128);
  const rejected = fakeResponse();
  admission.handler({} as never, rejected as never);
  expect(rejected.status).toBe(503);
  responses[0]!.emit("finish");
  responses[0]!.emit("close");
  expect(admission.inFlight).toBe(127);
});

test("draining rejects races without invoking the handler", () => {
  const handler = vi.fn();
  const admission = createAdmission(handler);
  admission.drain();
  const response = fakeResponse();
  admission.handler({} as never, response as never);
  expect(response.status).toBe(503);
  expect(handler).not.toHaveBeenCalled();
  expect(admission.draining).toBe(true);
});

test("handler failures close safely", async () => {
  const admission = createAdmission(async () => {
    throw new Error("failure");
  });
  const response = fakeResponse();
  admission.handler({} as never, response as never);
  await new Promise(setImmediate);
  expect(response.status).toBe(500);
  expect(admission.inFlight).toBe(0);
});

test("handler failure destroys a response whose headers were sent", async () => {
  const admission = createAdmission(
    async (_request: unknown, response: ReturnType<typeof fakeResponse>) => {
      response.headersSent = true;
      throw new Error("failure");
    },
  );
  const response = fakeResponse();
  admission.handler({} as never, response as never);
  await new Promise(setImmediate);
  expect(admission.inFlight).toBe(0);
});

test("bounded close reports clean completion", async () => {
  const server = {
    close: vi.fn((callback: () => void) => callback()),
    closeAllConnections: vi.fn(),
  };
  await expect(closeWithin([server] as never, 10)).resolves.toBe("closed");
  expect(server.closeAllConnections).not.toHaveBeenCalled();
});

test("bounded close terminates connections after its deadline", async () => {
  const server = { close: vi.fn(), closeAllConnections: vi.fn() };
  await expect(closeWithin([server] as never, 1)).resolves.toBe("timeout");
  expect(server.closeAllConnections).toHaveBeenCalledOnce();
});

async function get(
  port: number,
  path: string,
  headers?: Record<string, string>,
) {
  return new Promise<{ body: string; status: number }>((resolve, reject) => {
    const call = request({ headers, path, port }, (result) => {
      let body = "";
      result.on("data", (chunk) => {
        body += chunk;
      });
      result.on("end", () => resolve({ body, status: result.statusCode! }));
    });
    call.on("error", reject);
    call.end();
  });
}

test("probe server keeps exact internal paths content-free", async () => {
  const state = { draining: false, ready: false };
  const server = createProbeServer(state);
  server.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  const port = (server.address() as { port: number }).port;
  expect(await get(port, "/_frontend/health/live")).toEqual({
    body: "",
    status: 200,
  });
  expect(await get(port, "/_frontend/health/ready")).toEqual({
    body: "",
    status: 503,
  });
  state.ready = true;
  expect((await get(port, "/_frontend/health/ready")).status).toBe(200);
  state.draining = true;
  expect((await get(port, "/_frontend/health/ready")).status).toBe(503);
  expect((await get(port, "/health/live")).status).toBe(404);
  const post = await new Promise<number>((resolve, reject) => {
    const call = request(
      { method: "POST", path: "/_frontend/health/live", port },
      (result) => {
        result.resume();
        result.on("end", () => resolve(result.statusCode!));
      },
    );
    call.on("error", reject);
    call.end();
  });
  expect(post).toBe(404);
  server.close();
});

test("HTTP header overflow returns an empty 431", async () => {
  const page = createPageServer(
    (_request: IncomingMessage, result: ServerResponse) => {
      result.end("ok");
    },
  );
  page.server.listen(0, "127.0.0.1");
  await new Promise((resolve) => page.server.once("listening", resolve));
  const port = (page.server.address() as { port: number }).port;
  const result = await get(port, "/", { "x-large": "a".repeat(17_000) });
  expect(result).toEqual({ body: "", status: 431 });
  page.server.close();
});
