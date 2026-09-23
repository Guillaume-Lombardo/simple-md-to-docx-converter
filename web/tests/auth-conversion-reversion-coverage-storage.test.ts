import {
  clearConversionInputs,
  ownerStorageEpoch,
  readConversionInputs,
  readReversionInputs,
  resumeOwnerStorage,
  writeConversionInputs,
  writeReversionInputs,
} from "../src/conversion/persistence";

const owner = "coverage-storage-owner";

function storage() {
  const values = new Map<string, unknown>();
  let nextFailure: "abort" | "error" | undefined;
  let nextOpenFailure = false;
  const database = {
    close: vi.fn(),
    createObjectStore: vi.fn(),
    transaction: vi.fn(() => {
      const transaction: {
        error: DOMException | null;
        oncomplete: (() => void) | null;
        onerror: (() => void) | null;
        onabort: (() => void) | null;
        objectStore: () => object;
      } = {
        error: null,
        oncomplete: null,
        onerror: null,
        onabort: null,
        objectStore: () => ({
          get: (key: string) => request(() => values.get(key)),
          put: (value: unknown, key: string) =>
            request(() => values.set(key, value)),
          delete: (key: string) => request(() => values.delete(key)),
        }),
      };
      function request(action: () => unknown) {
        const result: { result?: unknown; onsuccess: (() => void) | null } = {
          onsuccess: null,
        };
        queueMicrotask(() => {
          const failure = nextFailure;
          nextFailure = undefined;
          if (failure) {
            transaction.error = new DOMException(
              "Storage failed",
              "QuotaExceededError",
            );
            if (failure === "abort") transaction.onabort?.();
            else transaction.onerror?.();
            return;
          }
          result.result = action();
          result.onsuccess?.();
          queueMicrotask(() => transaction.oncomplete?.());
        });
        return result;
      }
      return transaction;
    }),
  };
  vi.stubGlobal("indexedDB", {
    open: () => {
      const request: {
        result: typeof database;
        error: DOMException | null;
        onupgradeneeded: (() => void) | null;
        onsuccess: (() => void) | null;
        onerror: (() => void) | null;
      } = {
        result: database,
        error: null,
        onupgradeneeded: null,
        onsuccess: null,
        onerror: null,
      };
      queueMicrotask(() => {
        if (nextOpenFailure) {
          nextOpenFailure = false;
          request.error = new DOMException("Storage denied", "SecurityError");
          request.onerror?.();
        } else {
          request.onupgradeneeded?.();
          request.onsuccess?.();
        }
      });
      return request;
    },
  });
  return {
    database,
    failNextTransaction: (kind: "abort" | "error") => {
      nextFailure = kind;
    },
    failNextOpen: () => {
      nextOpenFailure = true;
    },
    values,
  };
}

afterEach(() => vi.unstubAllGlobals());

test("quota abort leaves the previous Markdown and assets intact and retry saves the new file", async () => {
  const browser = storage();
  resumeOwnerStorage(owner);
  const epoch = ownerStorageEpoch(owner);
  const oldSource = new File(["old asset manifest"], "old.zip", {
    type: "application/zip",
  });
  const replacement = new File(["new asset manifest"], "new.zip", {
    type: "application/zip",
  });
  const state = {
    output: "docx" as const,
    dialect: "auto" as const,
    slideLevel: 2,
  };
  await writeConversionInputs(
    owner,
    false,
    { ...state, source: oldSource },
    "old",
    epoch,
  );

  browser.failNextTransaction("abort");
  await expect(
    writeConversionInputs(
      owner,
      false,
      { ...state, source: replacement },
      "new",
      epoch,
    ),
  ).rejects.toMatchObject({ name: "QuotaExceededError" });
  expect((await readConversionInputs(owner, false))?.source?.name).toBe(
    "old.zip",
  );
  await writeConversionInputs(
    owner,
    false,
    { ...state, source: replacement },
    "new",
    epoch,
  );
  const restored = await readConversionInputs(owner, false);
  expect(restored?.query).toBe("new");
  expect(await restored?.source?.text()).toBe("new asset manifest");
  expect(browser.database.close).toHaveBeenCalled();
});

test("read and write errors propagate, then private Revert inputs recover", async () => {
  const browser = storage();
  resumeOwnerStorage(owner);
  const epoch = ownerStorageEpoch(owner);
  browser.failNextOpen();
  await expect(readReversionInputs(owner)).rejects.toMatchObject({
    name: "SecurityError",
  });

  browser.failNextTransaction("error");
  await expect(
    writeReversionInputs(
      owner,
      { source: new File(["office data"], "draft.docx") },
      epoch,
    ),
  ).rejects.toMatchObject({ name: "QuotaExceededError" });
  await writeReversionInputs(
    owner,
    { source: new File(["office data"], "draft.docx") },
    epoch,
  );
  expect(await (await readReversionInputs(owner))?.source?.text()).toBe(
    "office data",
  );
  await clearConversionInputs(owner);
  expect(await readReversionInputs(owner)).toBeUndefined();
});

test("a read queued behind a failed write returns the last committed source", async () => {
  const browser = storage();
  resumeOwnerStorage(owner);
  const epoch = ownerStorageEpoch(owner);
  const state = {
    output: "pdf" as const,
    dialect: "auto" as const,
    slideLevel: 2,
  };
  await writeConversionInputs(
    owner,
    false,
    { ...state, source: new File(["stable"], "stable.md") },
    "stable",
    epoch,
  );
  browser.failNextTransaction("abort");
  const failedWrite = writeConversionInputs(
    owner,
    false,
    { ...state, source: new File(["uncommitted"], "uncommitted.md") },
    "uncommitted",
    epoch,
  );
  const read = readConversionInputs(owner, false);
  await expect(failedWrite).rejects.toMatchObject({
    name: "QuotaExceededError",
  });
  expect((await read)?.source?.name).toBe("stable.md");
});
