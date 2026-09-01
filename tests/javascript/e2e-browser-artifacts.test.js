import assert from "node:assert/strict";
import { execFile as executeFile } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { promisify } from "node:util";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  closeCompletedBrowserPhases,
  retainFailureArtifacts,
} from "../e2e/browser-helpers.mjs";
import {
  collectResourceDiagnostics,
  isValidResourceDiagnostics,
  parseMemoryEvents,
  retainResourceDiagnostics,
  summarizeProcessNames,
} from "../e2e/resource-diagnostics.mjs";

const execFile = promisify(executeFile);

test("controlled browser failure retains bounded redacted diagnostics", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "md-converter-e2e-artifacts-"));
  let screenshotOptions;
  const page = {
    isClosed: () => false,
    locator: (selector) => ({ selector }),
    screenshot: async (options) => {
      screenshotOptions = options;
      await writeFile(options.path, "controlled screenshot");
    },
  };
  const trace = {
    started: true,
    context: {
      tracing: {
        stop: async ({ path: destination }) => writeFile(destination, "controlled trace"),
      },
    },
  };
  try {
    await retainFailureArtifacts({
      artifactRoot: root,
      profile: "standalone",
      step: "controlled artifact failure",
      pages: [{ name: "admin", page }],
      traces: [{ name: "admin", trace }],
      error: new Error("private failure detail"),
    });
    const [directoryName] = await readdir(root);
    const directory = path.join(root, directoryName);
    assert.deepEqual(
      (await readdir(directory)).sort(),
      ["admin-trace.zip", "admin.png", "failure.json"],
    );
    const diagnostic = await readFile(path.join(directory, "failure.json"), "utf8");
    assert.match(diagnostic, /controlled artifact failure/);
    assert.doesNotMatch(diagnostic, /private failure detail/);
    assert.equal(screenshotOptions.mask.length, 2);
    assert.equal(trace.started, false);
  } finally {
    await rm(root, { recursive: true });
  }
});

test("completed browser phases discard traces before closing contexts", async () => {
  const traces = ["admin", "alice", "bob"].map((name) => ({
    name,
    trace: {
      started: true,
      context: {
        tracing: {
          stop: async () => {
            traces.find((entry) => entry.name === name).trace.started = false;
          },
        },
      },
    },
  }));
  const contexts = ["admin", "alice", "bob"].map((name) => ({
    close: async () => {
      assert.ok(traces.every(({ trace }) => trace.started === false));
      return name;
    },
  }));

  await closeCompletedBrowserPhases(contexts, traces);

  assert.ok(traces.every(({ trace }) => trace.started === false));
});

test("completed browser phases close every context after a trace discard failure", async () => {
  const closed = [];
  await closeCompletedBrowserPhases(
    [{ close: async () => closed.push("first") }, { close: async () => closed.push("second") }],
    [{ trace: { started: true, context: { tracing: { stop: async () => { throw new Error("failed"); } } } } }],
  );
  assert.deepEqual(closed, ["first", "second"]);
});

test("completed browser phases reject after attempting every context close", async () => {
  const closed = [];
  await assert.rejects(
    closeCompletedBrowserPhases(
      [
        { close: async () => closed.push("first") },
        { close: async () => { closed.push("failed"); throw new Error("close failed"); } },
        { close: async () => closed.push("last") },
      ],
      [],
    ),
    /close failed/,
  );
  assert.deepEqual(closed, ["first", "failed", "last"]);
});

test("resource diagnostics expose only the bounded allowlisted schema", async () => {
  const reads = [];
  const fixtures = new Map([
    ["/sys/fs/cgroup/memory.current", "1048576\n"],
    ["/sys/fs/cgroup/memory.peak", "2097152\n"],
    [
      "/sys/fs/cgroup/memory.events",
      "low 1\nhigh 2\nmax 3\noom 4\noom_kill 5\noom_group_kill 6\nsecret 99\n",
    ],
    ["/sys/fs/cgroup/pids.current", "3\n"],
    ["/proc/101/comm", "node\n"],
    ["/proc/102/comm", "chrome\n"],
    ["/proc/103/comm", "unsafe name --password=secret\n"],
  ]);
  const payload = await collectResourceDiagnostics({
    readText: async (source) => {
      reads.push(source);
      if (!fixtures.has(source)) throw new Error("unexpected probe");
      return fixtures.get(source);
    },
    listDirectory: async () => ["self", "103", "101", "102", "not-a-pid"],
    statFileSystem: async () => ({ blocks: 32n, bfree: 24n, bsize: 4096n }),
  });

  assert.deepEqual(payload, {
    schema_version: 1,
    cgroup: {
      memory_current_bytes: 1048576,
      memory_peak_bytes: 2097152,
      memory_events: {
        low: 1,
        high: 2,
        max: 3,
        oom: 4,
        oom_kill: 5,
        oom_group_kill: 6,
      },
      pid_count: 3,
    },
    shared_memory: {
      capacity_bytes: 131072,
      used_bytes: 32768,
    },
    process_names: [
      { name: "chrome", count: 1 },
      { name: "node", count: 1 },
    ],
    container: { state: null, exit_code: null, oom_killed: null },
  });
  assert.ok(reads.every((source) => !source.endsWith("/cmdline")));
  assert.doesNotMatch(JSON.stringify(payload), /secret|password|unsafe name/);
});

test("resource diagnostics fail closed for unavailable and malformed probes", async () => {
  assert.deepEqual(parseMemoryEvents("oom\noom_kill invalid\nhigh 7 extra\nmax 8\n"), {
    low: null,
    high: null,
    max: 8,
    oom: null,
    oom_kill: null,
    oom_group_kill: null,
  });

  const payload = await collectResourceDiagnostics({
    readText: async () => {
      throw new Error("probe unavailable");
    },
    listDirectory: async () => {
      throw new Error("proc unavailable");
    },
    statFileSystem: async () => {
      throw new Error("shared memory unavailable");
    },
  });

  assert.deepEqual(payload, {
    schema_version: 1,
    cgroup: {
      memory_current_bytes: null,
      memory_peak_bytes: null,
      memory_events: {
        low: null,
        high: null,
        max: null,
        oom: null,
        oom_kill: null,
        oom_group_kill: null,
      },
      pid_count: null,
    },
    shared_memory: {
      capacity_bytes: null,
      used_bytes: null,
    },
    process_names: [],
    container: { state: null, exit_code: null, oom_killed: null },
  });
});

test("stopped-container fallback diagnostics retain only safe state and OOM evidence", async () => {
  let probes = 0;
  const payload = await collectResourceDiagnostics({
    readText: async () => {
      probes += 1;
      throw new Error("probe unavailable");
    },
    listDirectory: async () => {
      probes += 1;
      return [];
    },
    statFileSystem: async () => {
      probes += 1;
      throw new Error("probe unavailable");
    },
    container: { state: "exited", exit_code: 137, oom_killed: true },
    collectHostResources: false,
  });

  assert.equal(probes, 0);
  assert.deepEqual(payload.container, { state: "exited", exit_code: 137, oom_killed: true });
  assert.equal(isValidResourceDiagnostics(payload), true);
  assert.equal(isValidResourceDiagnostics({ ...payload, unexpected: "/host/path" }), false);
  assert.equal(isValidResourceDiagnostics({ ...payload, container: { state: "exited" } }), false);
  assert.equal(isValidResourceDiagnostics({
    ...payload,
    process_names: [{ name: "node", count: 1, arguments: "--password=secret" }],
  }), false);
  assert.equal(isValidResourceDiagnostics({
    ...payload,
    process_names: [{ name: 123, count: 1 }],
  }), false);
  assert.doesNotMatch(JSON.stringify(payload), /password|secret|host|path/);
});

test("resource diagnostics bound and sanitize process-name cardinality", () => {
  const names = Array.from({ length: 70 }, (_, index) => `worker-${index}`);
  names.push("worker-0", "unsafe process --password=secret", "/host/path");

  const summary = summarizeProcessNames(names);

  assert.equal(summary.length, 64);
  assert.deepEqual(summary.find(({ name }) => name === "worker-0"), {
    name: "worker-0",
    count: 2,
  });
  assert.doesNotMatch(JSON.stringify(summary), /secret|password|host|path/);
});

test("resource diagnostics are retained separately with private permissions", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "markweave-resource-evidence-"));
  const fixtures = new Map([
    ["/sys/fs/cgroup/memory.current", "1\n"],
    ["/sys/fs/cgroup/memory.peak", "2\n"],
    ["/sys/fs/cgroup/memory.events", "oom 0\noom_kill 0\n"],
    ["/sys/fs/cgroup/pids.current", "1\n"],
    ["/proc/1/comm", "node\n"],
  ]);
  try {
    await retainResourceDiagnostics(root, {
      readText: async (source) => fixtures.get(source),
      listDirectory: async () => ["1"],
      statFileSystem: async () => ({ blocks: 2n, bfree: 1n, bsize: 4096n }),
    });
    const output = path.join(root, "resource-diagnostics.json");
    assert.equal((await stat(output)).mode & 0o777, 0o600);
    assert.equal(JSON.parse(await readFile(output, "utf8")).schema_version, 1);
    assert.deepEqual(await readdir(root), ["resource-diagnostics.json"]);
  } finally {
    await rm(root, { recursive: true });
  }
});

test("resource diagnostics publish atomically and clean up interrupted replacements", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "markweave-resource-atomic-"));
  const output = path.join(root, "resource-diagnostics.json");
  const oldPayload = "{\"previous\":true}\n";
  try {
    await writeFile(output, oldPayload, { mode: 0o600 });
    await assert.rejects(
      retainResourceDiagnostics(root, {
        readText: async () => {
          throw new Error("probe unavailable");
        },
        listDirectory: async () => [],
        statFileSystem: async () => {
          throw new Error("probe unavailable");
        },
        renameFile: async () => {
          throw new Error("simulated interruption");
        },
      }),
      /simulated interruption/,
    );
    assert.equal(await readFile(output, "utf8"), oldPayload);
    assert.deepEqual(await readdir(root), ["resource-diagnostics.json"]);
  } finally {
    await rm(root, { recursive: true });
  }
});

test("runner fallback CLI repairs missing and malformed diagnostics at its exact output path", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "markweave-resource-cli-"));
  const output = path.join(root, "resource-diagnostics.json");
  const script = path.resolve("tests/e2e/resource-diagnostics.mjs");
  try {
    for (const initial of [null, "{malformed\n"]) {
      await rm(output, { force: true });
      if (initial !== null) await writeFile(output, initial, { mode: 0o600 });
      await execFile(process.execPath, [
        script,
        "--output", output,
        "--host-fallback",
        "--container-state", "exited",
        "--container-exit-code", "137",
        "--container-oom-killed", "true",
      ]);
      const payload = JSON.parse(await readFile(output, "utf8"));
      assert.equal(isValidResourceDiagnostics(payload), true);
      assert.deepEqual(payload.container, { state: "exited", exit_code: 137, oom_killed: true });
      assert.deepEqual(payload.cgroup, {
        memory_current_bytes: null,
        memory_peak_bytes: null,
        memory_events: {
          low: null,
          high: null,
          max: null,
          oom: null,
          oom_kill: null,
          oom_group_kill: null,
        },
        pid_count: null,
      });
      assert.deepEqual(payload.shared_memory, { capacity_bytes: null, used_bytes: null });
      assert.deepEqual(payload.process_names, []);
      assert.equal((await stat(output)).mode & 0o777, 0o600);
      assert.deepEqual(await readdir(root), ["resource-diagnostics.json"]);
    }
  } finally {
    await rm(root, { recursive: true });
  }
});

test("fresh expiry tracing and resource collection keep failure-only ordering", async () => {
  const browserSource = await readFile("tests/e2e/browser-final-image.test.mjs", "utf8");
  const runnerSource = await readFile("scripts/e2e/run.sh", "utf8");
  const releaseIndex = browserSource.indexOf(
    "await closeCompletedBrowserPhases(contexts, traces);",
  );
  const expiryContextIndex = browserSource.indexOf(
    "const expiringContext = await browser.newContext",
  );
  const expiryTraceIndex = browserSource.indexOf(
    'traces.push({ name: "expiring-alice", trace: await startTrace(expiringContext) });',
  );
  const expiryPageIndex = browserSource.indexOf(
    "const expiringPage = await expiringContext.newPage();",
  );

  assert.ok(releaseIndex > 0 && releaseIndex < expiryContextIndex);
  assert.ok(expiryContextIndex < expiryTraceIndex && expiryTraceIndex < expiryPageIndex);
  const collectorIndex = runnerSource.indexOf(
    "podman exec \"$application_name\" node /e2e/resource-diagnostics.mjs",
  );
  let validationIndex = runnerSource.indexOf(
    'node "$browser_runtime_directory/resource-diagnostics.mjs" \\\n+      --validate "$temporary_directory/browser-artifacts/resource-diagnostics.json"',
  );
  let fallbackIndex = runnerSource.indexOf(
    'node "$browser_runtime_directory/resource-diagnostics.mjs" \\\n+      --output "$temporary_directory/browser-artifacts/resource-diagnostics.json"',
  );
  validationIndex = runnerSource.indexOf(
    '--validate "$temporary_directory/browser-artifacts/resource-diagnostics.json"',
  );
  fallbackIndex = runnerSource.indexOf(
    '--output "$temporary_directory/browser-artifacts/resource-diagnostics.json"',
  );
  const failureCopyIndex = runnerSource.indexOf(
    'cp -a -- "$temporary_directory/browser-artifacts/." "$artifact_directory/"',
  );
  assert.ok(collectorIndex > 0 && collectorIndex < failureCopyIndex);
  assert.ok(validationIndex > 0 && validationIndex < collectorIndex);
  assert.ok(fallbackIndex > collectorIndex && fallbackIndex < failureCopyIndex);
  assert.ok(fallbackIndex > runnerSource.indexOf("container_oom_killed"));
  assert.ok(runnerSource.indexOf("--host-fallback") > fallbackIndex);
  assert.ok(failureCopyIndex > runnerSource.indexOf("podman unshare chown -R 0:0"));
  assert.ok(runnerSource.includes("if ! collect_failure_artifacts; then\n      exit_code=1"));
  assert.ok(collectorIndex > runnerSource.indexOf("collect_failure_artifacts()"));
  assert.ok(
    browserSource.indexOf("retainResourceDiagnostics(settings.artifactRoot)")
      < browserSource.indexOf("await retainFailureArtifacts({"),
  );
});
