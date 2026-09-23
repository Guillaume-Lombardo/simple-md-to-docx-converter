import {
  clearConversionInputs,
  resumeOwnerStorage,
} from "../conversion/persistence";

export function resumeOwnerDraftInputs(ownerId: string): void {
  resumeOwnerStorage(ownerId);
}

export async function clearOwnerDraftInputs(ownerId: string): Promise<void> {
  try {
    if (typeof sessionStorage !== "undefined") {
      for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
        const key = sessionStorage.key(index);
        if (key?.startsWith("composer:") && key.split(":")[2] === ownerId)
          sessionStorage.removeItem(key);
      }
    }
  } catch {
    // Storage access may be denied without affecting the session transition.
  }
  try {
    await clearConversionInputs(ownerId);
  } catch {
    // A browser storage failure must not keep an invalid session active.
  }
}
