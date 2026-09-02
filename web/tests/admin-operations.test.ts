import { ApiError } from "../src/api/transport";
import {
  administrationError,
  idleMinutesError,
  appendExpectedFonts,
  expectedFonts,
  RequestFence,
  readTemplateDownload,
  saveTemplateDownload,
  SESSION_ENDED,
} from "../src/admin/operations";

const docxHeaders = {
  "Cache-Control": "private, no-store",
  "Content-Disposition": 'attachment; filename="template-id-v3.docx"',
  "Content-Type":
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "X-Content-Type-Options": "nosniff",
};

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

test("policy duration validation consumes returned bounds, granularity, and ceiling", () => {
  const bounds = {
    default_minutes: 17,
    maximum_minutes: 99,
    minimum_minutes: 7,
  };
  expect(idleMinutesError("Duration", "9", bounds, 2, 600)).toBeUndefined();
  expect(idleMinutesError("Duration", "8", bounds, 2, 600)).toContain(
    "2-minute increments",
  );
  expect(idleMinutesError("Duration", "6", bounds, 2, 600)).toContain(
    "between 7 and 10",
  );
  expect(idleMinutesError("Duration", "11", bounds, 2, 600)).toContain(
    "between 7 and 10",
  );
  for (const invalid of ["", "1.5", "-1", " 9", "9007199254740993"])
    expect(idleMinutesError("Duration", invalid, bounds, 2, 600)).toContain(
      "whole number",
    );
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

test("template downloads validate metadata, preserve filename, and revoke their URL", async () => {
  const response = new Response("docx bytes", { headers: docxHeaders });
  const download = await readTemplateDownload(response);
  expect(download.filename).toBe("template-id-v3.docx");
  expect(await download.blob.text()).toBe("docx bytes");

  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:template-download");
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  let clickedDownload: string | undefined;
  let clickedHref: string | undefined;
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(function (this: HTMLAnchorElement) {
      clickedDownload = this.download;
      clickedHref = this.href;
    });
  let deferred!: () => void;
  saveTemplateDownload(download, (callback) => {
    deferred = callback;
  });
  expect(createObjectURL).toHaveBeenCalledWith(download.blob);
  expect(click).toHaveBeenCalledOnce();
  expect(clickedDownload).toBe("template-id-v3.docx");
  expect(clickedHref).toBe("blob:template-download");
  expect(revokeObjectURL).not.toHaveBeenCalled();
  deferred();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:template-download");
  vi.restoreAllMocks();
});

test("template downloads decode the authoritative UTF-8 filename", async () => {
  const download = await readTemplateDownload(
    new Response("docx", {
      headers: {
        ...docxHeaders,
        "Content-Disposition":
          "attachment; filename*=UTF-8''template%20id-v3.docx",
      },
    }),
  );
  expect(download.filename).toBe("template id-v3.docx");
});

test.each([
  [{ ...docxHeaders, "Content-Type": "text/html" }],
  [
    {
      ...docxHeaders,
      "Content-Disposition": 'attachment; filename="../x.docx"',
    },
  ],
  [{ ...docxHeaders, "Content-Disposition": 'attachment; filename="x.txt"' }],
  [
    {
      ...docxHeaders,
      "Content-Disposition": "attachment; filename*=UTF-8''%GG",
    },
  ],
  [{ ...docxHeaders, "Cache-Control": "public" }],
  [{ ...docxHeaders, "X-Content-Type-Options": "" }],
])("template downloads reject unsafe response metadata", async (headers) => {
  await expect(
    readTemplateDownload(new Response("unsafe", { headers })),
  ).rejects.toThrow("Unexpected template download");
});
