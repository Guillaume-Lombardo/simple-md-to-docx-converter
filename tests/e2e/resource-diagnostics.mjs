import { chmod, readdir, readFile, statfs, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const OUTPUT_ROOT = "/browser-artifacts";
const MEMORY_EVENT_FIELDS = ["low", "high", "max", "oom", "oom_kill", "oom_group_kill"];
const MAX_PROCESS_NAMES = 64;
const MAX_PROCESS_IDS = 4_096;
const SAFE_PROCESS_NAME = /^[A-Za-z0-9_.:+-]{1,63}$/;

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
} = {}) {
  const memoryCurrent = await readBounded(
    readText,
    "/sys/fs/cgroup/memory.current",
    128,
  );
  const memoryPeak = await readBounded(readText, "/sys/fs/cgroup/memory.peak", 128);
  const memoryEvents = await readBounded(
    readText, "/sys/fs/cgroup/memory.events", 4_096,
  );
  const pidCount = await readBounded(readText, "/sys/fs/cgroup/pids.current", 128);

  let processIds = [];
  try {
    processIds = (await listDirectory("/proc"))
      .filter((entry) => /^[1-9][0-9]*$/.test(entry))
      .sort((left, right) => Number(left) - Number(right))
      .slice(0, MAX_PROCESS_IDS);
  } catch {
    processIds = [];
  }
  const processNames = await Promise.all(
    processIds.map((pid) => readBounded(readText, `/proc/${pid}/comm`, 128)),
  );

  let sharedMemoryCapacity = null;
  let sharedMemoryUsed = null;
  try {
    const sharedMemory = await statFileSystem("/dev/shm", { bigint: true });
    const capacity = sharedMemory.blocks * sharedMemory.bsize;
    const used = (sharedMemory.blocks - sharedMemory.bfree) * sharedMemory.bsize;
    if (capacity <= BigInt(Number.MAX_SAFE_INTEGER)) sharedMemoryCapacity = Number(capacity);
    if (used <= BigInt(Number.MAX_SAFE_INTEGER)) sharedMemoryUsed = Number(used);
  } catch {
    // The fixed schema retains nulls when the kernel probe is unavailable.
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
  };
}

export async function retainResourceDiagnostics(artifactRoot, dependencies = {}) {
  const payload = await collectResourceDiagnostics(dependencies);
  const outputPath = path.join(artifactRoot, "resource-diagnostics.json");
  await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  await chmod(outputPath, 0o600);
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  await retainResourceDiagnostics(OUTPUT_ROOT);
}
