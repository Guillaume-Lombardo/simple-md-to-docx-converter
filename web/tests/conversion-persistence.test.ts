import {
  clearConversionInputs,
  ownerStorageEpoch,
  readConversionInputs,
  readReversionInputs,
  resumeOwnerStorage,
  writeConversionInputs,
  writeReversionInputs,
  type SavedConversionInputs,
} from "../src/conversion/persistence";

function browserStorage({ deferWrites = false } = {}) {
  resumeOwnerStorage("alice");
  const values = new Map<string, unknown>();
  const heldWrites: Array<() => void> = [];
  let holdWrites = deferWrites;
  const database = {
    close: vi.fn(),
    createObjectStore: vi.fn(),
    transaction: vi.fn(() => {
      const transaction: {
        onabort: (() => void) | null;
        oncomplete: (() => void) | null;
        onerror: (() => void) | null;
        error: DOMException | null;
        objectStore: () => object;
      } = {
        onabort: null,
        oncomplete: null,
        onerror: null,
        error: null,
        objectStore: () => ({
          get: (key: string) => request(() => values.get(key)),
          put: (value: unknown, key: string) =>
            request(() => values.set(key, value), true),
          delete: (key: string) => request(() => values.delete(key)),
        }),
      };
      function request(action: () => unknown, write = false) {
        const result: {
          result?: unknown;
          onsuccess: (() => void) | null;
        } = { onsuccess: null };
        const complete = () => {
          result.result = action();
          result.onsuccess?.();
          queueMicrotask(() => transaction.oncomplete?.());
        };
        if (write && holdWrites) heldWrites.push(complete);
        else queueMicrotask(complete);
        return result;
      }
      return transaction;
    }),
  };
  let created = false;
  vi.stubGlobal("indexedDB", {
    open: () => {
      const request: {
        result: typeof database;
        onupgradeneeded: (() => void) | null;
        onsuccess: (() => void) | null;
        onerror: (() => void) | null;
      } = {
        result: database,
        onupgradeneeded: null,
        onsuccess: null,
        onerror: null,
      };
      queueMicrotask(() => {
        if (!created) {
          request.onupgradeneeded?.();
          created = true;
        }
        request.onsuccess?.();
      });
      return request;
    },
  });
  return {
    heldWrites,
    releaseNextWrite: () => queueMicrotask(heldWrites.shift()!),
    pauseWrites: () => {
      holdWrites = true;
    },
    resumeWrites: () => {
      holdWrites = false;
    },
  };
}

afterEach(() => vi.unstubAllGlobals());

test("ZIP bytes and unsent settings survive storage and stay scoped to owner and workspace", async () => {
  browserStorage();
  const source = new File(["zip-with-assets"], "document.zip", {
    type: "application/zip",
    lastModified: 123,
  });
  const state = {
    source,
    output: "pptx-bundle",
    dialect: "marp",
    slideLevel: 3,
    selection: {
      id: "template",
      versionId: "immutable-version",
      name: "Slides",
      description: "",
      source: "selected",
    },
    activeJobId: "job",
  } satisfies Omit<SavedConversionInputs, "query">;
  await writeConversionInputs(
    "alice",
    true,
    state,
    "chosen template",
    ownerStorageEpoch("alice"),
  );
  const restored = await readConversionInputs("alice", true);
  expect(restored).toMatchObject({
    activeJobId: "job",
    output: "pptx-bundle",
    dialect: "marp",
    slideLevel: 3,
    query: "chosen template",
    selection: { versionId: "immutable-version" },
    source: { name: "document.zip", size: source.size, lastModified: 123 },
  });
  expect(await restored?.source?.text()).toBe("zip-with-assets");
  expect(await readConversionInputs("alice", false)).toBeUndefined();
  expect(await readConversionInputs("bob", true)).toBeUndefined();
  await clearConversionInputs("alice");
  expect(await readConversionInputs("alice", true)).toBeUndefined();
});

test("Revert stores original Office bytes and settings independently of Convert", async () => {
  browserStorage();
  const source = new File(["PPTX with slides"], "deck.pptx", {
    type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    lastModified: 456,
  });
  await writeReversionInputs(
    "alice",
    {
      source,
      options: {
        extraction: "marp",
        include_images: true,
        include_notes: false,
      },
      activeJobId: "reverse-job",
    },
    ownerStorageEpoch("alice"),
  );
  const restored = await readReversionInputs("alice");
  expect(restored).toMatchObject({
    activeJobId: "reverse-job",
    options: { extraction: "marp", include_notes: false },
    source: { name: "deck.pptx", size: source.size, lastModified: 456 },
  });
  expect(await restored?.source?.text()).toBe("PPTX with slides");
  expect(await readReversionInputs("bob")).toBeUndefined();
  expect(await readConversionInputs("alice", false)).toBeUndefined();
  await clearConversionInputs("alice");
  expect(await readReversionInputs("alice")).toBeUndefined();
});

test("owner cleanup drains in-flight writes and rejects stale queued writes", async () => {
  const storage = browserStorage();
  const source = new File(["private"], "source.md");
  await writeReversionInputs(
    "bob",
    { source: new File(["other owner"], "bob.docx") },
    ownerStorageEpoch("bob"),
  );
  storage.pauseWrites();
  const epoch = ownerStorageEpoch("alice");
  const input = {
    source,
    output: "docx" as const,
    dialect: "auto" as const,
    slideLevel: 2,
  };
  const inFlight = writeConversionInputs("alice", false, input, "", epoch);
  await vi.waitFor(() => expect(storage.heldWrites).toHaveLength(1));
  const queued = writeReversionInputs(
    "alice",
    { source: new File(["office"], "source.docx") },
    epoch,
  );
  const cleanup = clearConversionInputs("alice");
  const stale = writeConversionInputs("alice", true, input, "", epoch);
  storage.releaseNextWrite();
  await inFlight;
  await expect(queued).rejects.toThrow(/owner session changed/);
  await cleanup;
  await expect(stale).rejects.toThrow(/owner session changed/);
  expect(await readConversionInputs("alice", false)).toBeUndefined();
  expect(await readReversionInputs("alice")).toBeUndefined();
  expect((await readReversionInputs("bob"))?.source?.name).toBe("bob.docx");

  storage.resumeWrites();
  resumeOwnerStorage("alice");
  await expect(
    writeConversionInputs("alice", false, input, "", epoch),
  ).rejects.toThrow(/owner session changed/);
  await writeConversionInputs(
    "alice",
    false,
    input,
    "",
    ownerStorageEpoch("alice"),
  );
  expect((await readConversionInputs("alice", false))?.source?.name).toBe(
    "source.md",
  );
  await clearConversionInputs("alice");
});
