import { randomUUID } from "node:crypto";
import { chmod, readdir, readFile, rename, rm, statfs, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const OUTPUT_ROOT = "/browser-artifacts";
const MEMORY_EVENT_FIELDS = ["low", "high", "max", "oom", "oom_kill", "oom_group_kill"];
const MAX_PROCESS_NAMES = 64;
const MAX_PROCESS_IDS = 4_096;
const SAFE_PROCESS_NAME = /^[A-Za-z0-9_.:+-]{1,63}$/;
const SAFE_CONTAINER_STATE = /^[a-z-]{1,32}$/;

function boundedInteger(value) {
  if (typeof value !== "string") return null;
  if (!/^[0-9]+$/.test(value.trim())) return null;
  const parsed = Number(value.trim());
  return Number.isSafeInteger(parsed) ? parsed : null;
}

export function parseMemoryEvents(value) {
  const parsed = new Map();
  for (const line of value.split("\n")) {
    const [name, count, ...remainder] = line.trim().split(/\s+/);
    if (remainder.length === 0 && MEMORY_EVENT_FIELDS.includes(name)) {
      parsed.set(name, boundedInteger(count));
    }
  }
  return Object.fromEntries(MEMORY_EVENT_FIELDS.map((name) => [name, parsed.get(name) ?? null]));
}

export function summarizeProcessNames(names) {
  const counts = new Map();
  for (const rawName of names) {
    const name = rawName.trim();
    if (!SAFE_PROCESS_NAME.test(name)) continue;
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return [...counts.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .slice(0, MAX_PROCESS_NAMES)
    .map(([name, count]) => ({ name, count }));
}

function isNullableInteger(value) {
  return value === null || (Number.isSafeInteger(value) && value >= 0);
}

export function isValidResourceDiagnostics(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return false;
  if (payload.schema_version !== 1) return false;
  if (Object.keys(payload).sort().join(",")
    !== "cgroup,container,process_names,schema_version,shared_memory") return false;
  if (!payload.cgroup || !payload.shared_memory || !payload.container) return false;
  if (!isNullableInteger(payload.cgroup.memory_current_bytes)
    || !isNullableInteger(payload.cgroup.memory_peak_bytes)
    || !isNullableInteger(payload.cgroup.pid_count)
    || !isNullableInteger(payload.shared_memory.capacity_bytes)
    || !isNullableInteger(payload.shared_memory.used_bytes)
    || !isNullableInteger(payload.container.exit_code)
    || (payload.container.state !== null
      && (typeof payload.container.state !== "string"
        || !SAFE_CONTAINER_STATE.test(payload.container.state)))
    || (payload.container.oom_killed !== null && typeof payload.container.oom_killed !== "boolean")) {
    return false;
  }
  if (Object.keys(payload.cgroup).sort().join(",")
    !== "memory_current_bytes,memory_events,memory_peak_bytes,pid_count") return false;
  if (Object.keys(payload.shared_memory).sort().join(",")
    !== "capacity_bytes,used_bytes") return false;
  if (Object.keys(payload.container).sort().join(",")
    !== "exit_code,oom_killed,state") return false;
  if (!payload.cgroup.memory_events || typeof payload.cgroup.memory_events !== "object"
    || Object.keys(payload.cgroup.memory_events).sort().join(",")
      !== MEMORY_EVENT_FIELDS.toSorted().join(",")
    || !Object.values(payload.cgroup.memory_events).every(isNullableInteger)) return false;
  return Array.isArray(payload.process_names)
    && payload.process_names.length <= MAX_PROCESS_NAMES
    && payload.process_names.every((entry) => entry && typeof entry === "object"
      && !Array.isArray(entry) && Object.keys(entry).sort().join(",") === "count,name"
      && typeof entry.name === "string" && SAFE_PROCESS_NAME.test(entry.name)
      && Number.isSafeInteger(entry.count) && entry.count > 0);
}

async function readBounded(readText, source, maximumBytes) {
  try {
    const value = await readText(source, "utf8");
    return typeof value === "string" && Buffer.byteLength(value, "utf8") <= maximumBytes
      ? value
      : null;
  } catch {
    return null;
  }
}

export async function collectResourceDiagnostics({
  readText = readFile,
  listDirectory = readdir,
  statFileSystem = statfs,
  container = { state: null, exit_code: null, oom_killed: null },
  collectHostResources = true,
} = {}) {
  const safeContainer = container && typeof container === "object" ? container : {};
  const memoryCurrent = collectHostResources
    ? await readBounded(readText, "/sys/fs/cgroup/memory.current", 128)
    : null;
  const memoryPeak = collectHostResources
    ? await readBounded(readText, "/sys/fs/cgroup/memory.peak", 128)
    : null;
  const memoryEvents = collectHostResources
    ? await readBounded(readText, "/sys/fs/cgroup/memory.events", 4_096)
    : null;
  const pidCount = collectHostResources
    ? await readBounded(readText, "/sys/fs/cgroup/pids.current", 128)
    : null;

  let processIds = [];
  if (collectHostResources) {
    try {
      processIds = (await listDirectory("/proc"))
        .filter((entry) => /^[1-9][0-9]*$/.test(entry))
        .sort((left, right) => Number(left) - Number(right))
        .slice(0, MAX_PROCESS_IDS);
    } catch {
      processIds = [];
    }
  }
  const processNames = await Promise.all(
    processIds.map((pid) => readBounded(readText, `/proc/${pid}/comm`, 128)),
  );

  let sharedMemoryCapacity = null;
  let sharedMemoryUsed = null;
  if (collectHostResources) {
    try {
      const sharedMemory = await statFileSystem("/dev/shm", { bigint: true });
      const capacity = sharedMemory.blocks * sharedMemory.bsize;
      const used = (sharedMemory.blocks - sharedMemory.bfree) * sharedMemory.bsize;
      if (capacity <= BigInt(Number.MAX_SAFE_INTEGER)) sharedMemoryCapacity = Number(capacity);
      if (used <= BigInt(Number.MAX_SAFE_INTEGER)) sharedMemoryUsed = Number(used);
    } catch {
      // The fixed schema retains nulls when the kernel probe is unavailable.
    }
  }

  return {
    schema_version: 1,
    cgroup: {
      memory_current_bytes: memoryCurrent === null ? null : boundedInteger(memoryCurrent),
      memory_peak_bytes: memoryPeak === null ? null : boundedInteger(memoryPeak),
      memory_events: memoryEvents === null
        ? Object.fromEntries(MEMORY_EVENT_FIELDS.map((name) => [name, null]))
        : parseMemoryEvents(memoryEvents),
      pid_count: pidCount === null ? null : boundedInteger(pidCount),
    },
    shared_memory: {
      capacity_bytes: sharedMemoryCapacity,
      used_bytes: sharedMemoryUsed,
    },
    process_names: summarizeProcessNames(processNames.filter((name) => name !== null)),
    container: {
      state: typeof safeContainer.state === "string" && SAFE_CONTAINER_STATE.test(safeContainer.state)
        ? safeContainer.state
        : null,
      exit_code: isNullableInteger(safeContainer.exit_code) ? safeContainer.exit_code : null,
      oom_killed: typeof safeContainer.oom_killed === "boolean" ? safeContainer.oom_killed : null,
    },
  };
}

export async function retainResourceDiagnosticsAt(outputPath, dependencies = {}) {
  const payload = await collectResourceDiagnostics(dependencies);
  const {
    writeText = writeFile,
    chmodFile = chmod,
    renameFile = rename,
    removeFile = rm,
  } = dependencies;
  const temporaryPath = path.join(
    path.dirname(outputPath),
    `.${path.basename(outputPath)}-${process.pid}-${randomUUID()}.tmp`,
  );
  try {
    await writeText(temporaryPath, `${JSON.stringify(payload, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    await chmodFile(temporaryPath, 0o600);
    await renameFile(temporaryPath, outputPath);
  } finally {
    await removeFile(temporaryPath, { force: true });
  }
}

export async function retainResourceDiagnostics(artifactRoot, dependencies = {}) {
  await retainResourceDiagnosticsAt(
    path.join(artifactRoot, "resource-diagnostics.json"),
    dependencies,
  );
}

async function main(arguments_) {
  if (arguments_[0] === "--validate" && arguments_.length === 2) {
    try {
      process.exitCode = isValidResourceDiagnostics(JSON.parse(await readFile(arguments_[1], "utf8")))
        ? 0
        : 1;
    } catch {
      process.exitCode = 1;
    }
    return;
  }
  const values = new Map();
  for (let index = 0; index < arguments_.length;) {
    const option = arguments_[index];
    const value = arguments_[index + 1];
    if (option === "--host-fallback" && !values.has(option)) {
      values.set(option, true);
      index += 1;
      continue;
    }
    if (!["--output", "--container-state", "--container-exit-code", "--container-oom-killed"].includes(option)
      || value === undefined || values.has(option)) {
      process.exitCode = 2;
      return;
    }
    values.set(option, value);
    index += 2;
  }
  const dependencies = {
    container: {
      state: values.get("--container-state") || null,
      exit_code: boundedInteger(values.get("--container-exit-code")),
      oom_killed: values.has("--container-oom-killed")
        ? { true: true, false: false }[values.get("--container-oom-killed")] ?? null
        : null,
    },
    collectHostResources: !values.has("--host-fallback"),
  };
  if (values.has("--output")) {
    await retainResourceDiagnosticsAt(values.get("--output"), dependencies);
  } else {
    await retainResourceDiagnostics(OUTPUT_ROOT, dependencies);
  }
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  await main(process.argv.slice(2));
}
