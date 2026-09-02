import { ApiError } from "../src/api/transport";
import {
  administrationError,
  appendExpectedFonts,
  expectedFonts,
  RequestFence,
  SESSION_ENDED,
} from "../src/admin/operations";

test("expected fonts preserve trimmed order and explicit clearing", () => {
  expect(expectedFonts(" Carlito, , Liberation Serif, Carlito ")).toEqual([
    "Carlito",
    "Liberation Serif",
    "Carlito",
  ]);
  const populated = new FormData();
  appendExpectedFonts(populated, " Carlito, Liberation Serif ");
  expect(populated.getAll("expected_fonts")).toEqual([
    "Carlito",
    "Liberation Serif",
  ]);
  const cleared = new FormData();
  appendExpectedFonts(cleared, " , ");
  expect(cleared.getAll("expected_fonts")).toEqual([""]);
});

test("request fence aborts superseded work, blocks duplicates, and ignores late completion", () => {
  const fence = new RequestFence();
  const first = fence.startRead();
  const mutation = fence.startMutation()!;
  expect(first.controller.signal.aborted).toBe(true);
  expect(fence.startMutation()).toBeUndefined();
  expect(fence.current(first.generation)).toBe(false);
  expect(fence.finishMutation(first.generation)).toBe(false);
  expect(fence.finishMutation(mutation.generation)).toBe(true);
  const final = fence.startRead();
  fence.dispose();
  expect(final.controller.signal.aborted).toBe(true);
});

test.each([
  [new ApiError(401, "AUTHENTICATION_REQUIRED", "unsafe"), SESSION_ENDED],
  [new ApiError(0, "CSRF_MISSING", "unsafe"), SESSION_ENDED],
  [new ApiError(412, "STALE", "unsafe"), "This item changed on the server"],
  [new ApiError(428, "MISSING", "unsafe"), "The current server revision"],
  [new ApiError(403, "FORBIDDEN", "unsafe"), "not allowed"],
  [new ApiError(413, "TOO_LARGE", "unsafe"), "configured upload limit"],
  [new ApiError(409, "CONFLICT", "unsafe"), "already exists"],
  [
    new ApiError(422, "INVALID", "Safe server validation"),
    "Safe server validation",
  ],
  [new TypeError("private"), "Fixed fallback"],
])(
  "administration errors are bounded and revoke on authoritative session loss",
  (error, expected) => {
    const expire = vi.fn();
    expect(administrationError(error, expire, "Fixed fallback")).toContain(
      expected,
    );
    expect(expire).toHaveBeenCalledTimes(expected === SESSION_ENDED ? 1 : 0);
  },
);

test("aborted administration requests publish no error", () => {
  expect(
    administrationError(
      new DOMException("ignored", "AbortError"),
      vi.fn(),
      "fallback",
    ),
  ).toBeUndefined();
});
