import type { TemplateSelection } from "./controller";
import type { ReversionSubmissionOptions } from "../reversion/controller";
import type {
  JobOutput,
  PresentationDialect,
} from "../api/generated/types.gen";

const DATABASE_NAME = "markweave-conversion-inputs";
const STORE_NAME = "owner-workspaces";

type OwnerLane = {
  epoch: number;
  sealed: boolean;
  tail: Promise<void>;
};

const ownerLanes = new Map<string, OwnerLane>();

function lane(ownerId: string): OwnerLane {
  let current = ownerLanes.get(ownerId);
  if (!current) {
    current = { epoch: 0, sealed: false, tail: Promise.resolve() };
    ownerLanes.set(ownerId, current);
  }
  return current;
}

export function ownerStorageEpoch(ownerId: string): number {
  return lane(ownerId).epoch;
}

export function resumeOwnerStorage(ownerId: string): void {
  lane(ownerId).sealed = false;
}

function enqueueWrite(
  ownerId: string,
  expectedEpoch: number,
  operation: () => Promise<void>,
): Promise<void> {
  const current = lane(ownerId);
  const run = current.tail.then(async () => {
    if (current.sealed || current.epoch !== expectedEpoch)
      throw new Error(
        "The owner session changed before browser storage completed.",
      );
    await operation();
  });
  current.tail = run.catch(() => undefined);
  return run;
}

export type SavedConversionInputs = {
  source?: File;
  output: JobOutput;
  selection?: TemplateSelection;
  dialect: PresentationDialect;
  slideLevel: number;
  query: string;
  activeJobId?: string;
};

export type SavedReversionInputs = {
  source?: File;
  options?: ReversionSubmissionOptions;
  activeJobId?: string;
};

type StoredInputs = Omit<SavedConversionInputs, "source"> & {
  sourceBytes?: Blob;
  sourceName?: string;
  sourceLastModified?: number;
};

type StoredReversionInputs = Omit<SavedReversionInputs, "source"> & {
  sourceBytes?: Blob;
  sourceName?: string;
  sourceLastModified?: number;
};

function key(ownerId: string, presentation: boolean): string {
  return `${ownerId}:${presentation ? "presentations" : "convert"}`;
}

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined")
    return Promise.reject(new Error("Browser storage is unavailable"));
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE_NAME);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function transact<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore, resolve: (value: T) => void) => void,
): Promise<T> {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, mode);
    let result: T;
    transaction.oncomplete = () => {
      database.close();
      resolve(result);
    };
    transaction.onerror = () => {
      database.close();
      reject(transaction.error);
    };
    transaction.onabort = () => {
      database.close();
      reject(transaction.error);
    };
    run(transaction.objectStore(STORE_NAME), (value) => {
      result = value;
    });
  });
}

export async function readConversionInputs(
  ownerId: string,
  presentation: boolean,
): Promise<SavedConversionInputs | undefined> {
  await lane(ownerId).tail;
  const stored = await transact<StoredInputs | undefined>(
    "readonly",
    (store, resolve) => {
      const request = store.get(key(ownerId, presentation));
      request.onsuccess = () =>
        resolve(request.result as StoredInputs | undefined);
    },
  );
  if (!stored) return undefined;
  const { sourceBytes, sourceName, sourceLastModified, ...inputs } = stored;
  return {
    ...inputs,
    ...(sourceBytes && sourceName
      ? {
          source: new File([sourceBytes], sourceName, {
            type: sourceBytes.type,
            lastModified: sourceLastModified,
          }),
        }
      : {}),
  };
}

export function writeConversionInputs(
  ownerId: string,
  presentation: boolean,
  state: Omit<SavedConversionInputs, "query">,
  query: string,
  expectedEpoch: number,
): Promise<void> {
  const stored: StoredInputs = {
    output: state.output,
    dialect: state.dialect,
    slideLevel: state.slideLevel,
    query,
    ...(state.selection ? { selection: state.selection } : {}),
    ...(state.activeJobId ? { activeJobId: state.activeJobId } : {}),
    ...(state.source
      ? {
          sourceBytes: state.source,
          sourceName: state.source.name,
          sourceLastModified: state.source.lastModified,
        }
      : {}),
  };
  return enqueueWrite(ownerId, expectedEpoch, () =>
    transact<void>("readwrite", (store, resolve) => {
      const request = store.put(stored, key(ownerId, presentation));
      request.onsuccess = () => resolve();
    }),
  );
}

export async function readReversionInputs(
  ownerId: string,
): Promise<SavedReversionInputs | undefined> {
  await lane(ownerId).tail;
  const stored = await transact<StoredReversionInputs | undefined>(
    "readonly",
    (store, resolve) => {
      const request = store.get(`${ownerId}:revert`);
      request.onsuccess = () =>
        resolve(request.result as StoredReversionInputs | undefined);
    },
  );
  if (!stored) return undefined;
  const { sourceBytes, sourceName, sourceLastModified, ...inputs } = stored;
  return {
    ...inputs,
    ...(sourceBytes && sourceName
      ? {
          source: new File([sourceBytes], sourceName, {
            type: sourceBytes.type,
            lastModified: sourceLastModified,
          }),
        }
      : {}),
  };
}

export function writeReversionInputs(
  ownerId: string,
  state: SavedReversionInputs,
  expectedEpoch: number,
): Promise<void> {
  const stored: StoredReversionInputs = {
    ...(state.options ? { options: state.options } : {}),
    ...(state.activeJobId ? { activeJobId: state.activeJobId } : {}),
    ...(state.source
      ? {
          sourceBytes: state.source,
          sourceName: state.source.name,
          sourceLastModified: state.source.lastModified,
        }
      : {}),
  };
  return enqueueWrite(ownerId, expectedEpoch, () =>
    transact<void>("readwrite", (store, resolve) => {
      const request = store.put(stored, `${ownerId}:revert`);
      request.onsuccess = () => resolve();
    }),
  );
}

export function clearConversionInputs(ownerId: string): Promise<void> {
  const current = lane(ownerId);
  current.sealed = true;
  current.epoch += 1;
  const run = current.tail.then(() =>
    transact<void>("readwrite", (store, resolve) => {
      const first = store.delete(key(ownerId, false));
      first.onsuccess = () => {
        const second = store.delete(key(ownerId, true));
        second.onsuccess = () => {
          const third = store.delete(`${ownerId}:revert`);
          third.onsuccess = () => resolve();
        };
      };
    }),
  );
  current.tail = run.catch(() => undefined);
  return run;
}
