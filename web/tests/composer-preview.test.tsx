import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ComposerPreview } from "../src/composer/preview/composer-preview";
import {
  assertRevisionUrl,
  readBoundedResponse,
  revisionArtifactPath,
  verifySha256,
} from "../src/composer/preview/bytes";

const draftId = "11111111-1111-1111-1111-111111111111";
const firstRevision = "22222222-2222-2222-2222-222222222222";
const nextRevision = "33333333-3333-3333-3333-333333333333";
const nextDraft = "44444444-4444-4444-4444-444444444444";
const contentType =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function officeResponse(bytes = new Uint8Array([1, 2, 3]), mime = contentType) {
  return new Response(new Uint8Array(bytes), {
    headers: { "Content-Type": mime },
  });
}

test("preview URLs must match the exact draft, revision, kind and origin", () => {
  const preview = revisionArtifactPath(draftId, firstRevision, "preview");
  expect(assertRevisionUrl(preview, preview)).toBe(preview);
  expect(() => assertRevisionUrl(preview + "?other=1", preview)).toThrow();
  expect(() =>
    assertRevisionUrl(
      revisionArtifactPath(draftId, nextRevision, "preview"),
      preview,
    ),
  ).toThrow();
  expect(() =>
    assertRevisionUrl("https://elsewhere.invalid/preview", preview),
  ).toThrow();
});

test("download reader enforces streamed size and media type", async () => {
  await expect(
    readBoundedResponse(officeResponse(), 2, contentType),
  ).rejects.toThrow(/size limit/);
  await expect(
    readBoundedResponse(officeResponse(), 4, "application/pdf"),
  ).rejects.toThrow(/file type/);
  expect(
    Array.from(
      new Uint8Array(
        await readBoundedResponse(officeResponse(), 4, contentType),
      ),
    ),
  ).toEqual([1, 2, 3]);
});

test("artifact digest is checked before render", async () => {
  const bytes = new Uint8Array([1, 2, 3]).buffer;
  const digest = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  await expect(verifySha256(bytes, digest)).resolves.toBeUndefined();
  await expect(verifySha256(bytes, "0".repeat(64))).rejects.toThrow(
    /does not match/,
  );
});

test("old preview stays visible and stale, forged or cross-frame results cannot replace it", async () => {
  const fetchMock = vi
    .fn()
    .mockImplementation(() => Promise.resolve(officeResponse()));
  vi.stubGlobal("fetch", fetchMock);
  const { rerender } = render(
    <ComposerPreview
      draftId={draftId}
      revisionId={firstRevision}
      format="docx"
    />,
  );
  const first = await screen.findByTitle(
    `DOCX revision ${firstRevision} preview`,
  );
  expect(
    screen.getByRole("link", {
      name: `Download requested revision ${firstRevision}`,
    }),
  ).toHaveAttribute(
    "href",
    revisionArtifactPath(draftId, firstRevision, "download"),
  );
  const firstWindow = (first as HTMLIFrameElement).contentWindow!;
  const firstPost = vi.spyOn(firstWindow, "postMessage");
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: firstWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() =>
    expect(
      firstPost.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const firstRender = firstPost.mock.calls.find(
    ([message]) => message.type === "composer-preview-render",
  )![0];
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: firstWindow,
      data: {
        ...firstRender,
        type: "composer-preview-result",
        status: "ready",
      },
    }),
  );
  await waitFor(() => expect(first).toHaveAttribute("aria-hidden", "false"));

  rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId={nextRevision}
      format="docx"
    />,
  );
  const second = await screen.findByTitle(
    `DOCX revision ${nextRevision} preview`,
  );
  const secondWindow = (second as HTMLIFrameElement).contentWindow!;
  const secondPost = vi.spyOn(secondWindow, "postMessage");
  expect(first).toHaveAttribute("aria-hidden", "false");
  expect(second).toHaveAttribute("aria-hidden", "true");
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute(
    "href",
    revisionArtifactPath(draftId, firstRevision, "download"),
  );
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: secondWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() =>
    expect(
      secondPost.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const secondRender = secondPost.mock.calls.find(
    ([message]) => message.type === "composer-preview-render",
  )![0];
  for (const [source, data] of [
    [
      firstWindow,
      { ...firstRender, type: "composer-preview-result", status: "ready" },
    ],
    [
      secondWindow,
      {
        ...secondRender,
        token: "forged",
        type: "composer-preview-result",
        status: "ready",
      },
    ],
    [
      secondWindow,
      {
        ...secondRender,
        revisionId: firstRevision,
        type: "composer-preview-result",
        status: "ready",
      },
    ],
  ] as const) {
    fireEvent(
      window,
      new MessageEvent("message", { origin: "null", source, data }),
    );
  }
  expect(first).toHaveAttribute("aria-hidden", "false");
  expect(second).toHaveAttribute("aria-hidden", "true");
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: secondWindow,
      data: {
        ...secondRender,
        type: "composer-preview-result",
        status: "ready",
      },
    }),
  );
  await waitFor(() => expect(second).toHaveAttribute("aria-hidden", "false"));
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute(
    "href",
    revisionArtifactPath(draftId, nextRevision, "download"),
  );
  expect(
    screen.queryByTitle(`DOCX revision ${firstRevision} preview`),
  ).toBeNull();
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    revisionArtifactPath(draftId, firstRevision, "preview"),
    revisionArtifactPath(draftId, nextRevision, "preview"),
  ]);
  vi.unstubAllGlobals();
});

test("invalid artifact paths never fetch or offer a download", () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  render(
    <ComposerPreview
      draftId={draftId}
      revisionId={firstRevision}
      format="docx"
      previewUrl="https://attacker.invalid/preview"
    />,
  );
  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(
    screen.queryByTitle(`DOCX revision ${firstRevision} preview`),
  ).toBeNull();
  expect(
    screen.queryByRole("link", { name: "Download this revision" }),
  ).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
  expect(fetchMock).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});

test("only the current Office preview fetch reports HTTP 401", async () => {
  const onUnauthorized = vi.fn();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response("expired", { status: 401 }))
    .mockResolvedValueOnce(new Response("forbidden", { status: 403 }));
  vi.stubGlobal("fetch", fetchMock);
  const { rerender } = render(
    <ComposerPreview
      draftId={draftId}
      revisionId={firstRevision}
      format="docx"
      onUnauthorized={onUnauthorized}
    />,
  );
  const first = (await screen.findByTitle(
    `DOCX revision ${firstRevision} preview`,
  )) as HTMLIFrameElement;
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: first.contentWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("alert")).toHaveTextContent("unavailable");

  rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId={nextRevision}
      format="docx"
      onUnauthorized={onUnauthorized}
    />,
  );
  const second = (await screen.findByTitle(
    `DOCX revision ${nextRevision} preview`,
  )) as HTMLIFrameElement;
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: second.contentWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(onUnauthorized).toHaveBeenCalledTimes(1);
  vi.unstubAllGlobals();
});

test.each([
  { selection: "revision", draft: draftId, revision: nextRevision },
  { selection: "draft", draft: nextDraft, revision: firstRevision },
])(
  "an outdated Office 401 cannot expire a newer $selection",
  async ({ draft, revision }) => {
    const onUnauthorized = vi.fn();
    let finishFetch!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finishFetch = resolve;
          }),
      ),
    );
    const { rerender } = render(
      <ComposerPreview
        draftId={draftId}
        revisionId={firstRevision}
        format="docx"
        onUnauthorized={onUnauthorized}
      />,
    );
    const first = (await screen.findByTitle(
      `DOCX revision ${firstRevision} preview`,
    )) as HTMLIFrameElement;
    fireEvent(
      window,
      new MessageEvent("message", {
        origin: "null",
        source: first.contentWindow,
        data: { type: "composer-preview-ready" },
      }),
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    rerender(
      <ComposerPreview
        draftId={draft}
        revisionId={revision}
        format="docx"
        onUnauthorized={onUnauthorized}
      />,
    );
    await screen.findByTitle(`DOCX revision ${revision} preview`);
    finishFetch(new Response("expired", { status: 401 }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onUnauthorized).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  },
);

test("download callback receives the displayed artifact while another revision loads", async () => {
  const onDownload = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => officeResponse()),
  );
  const { rerender } = render(
    <ComposerPreview
      draftId={draftId}
      revisionId={firstRevision}
      format="docx"
      onDownload={onDownload}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", {
      name: `Download requested revision ${firstRevision}`,
    }),
  );
  expect(onDownload).toHaveBeenLastCalledWith(
    expect.objectContaining({
      draftId,
      revisionId: firstRevision,
      downloadUrl: revisionArtifactPath(draftId, firstRevision, "download"),
    }),
  );
  const frame = (await screen.findByTitle(
    `DOCX revision ${firstRevision} preview`,
  )) as HTMLIFrameElement;
  const post = vi.spyOn(frame.contentWindow!, "postMessage");
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frame.contentWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() =>
    expect(
      post.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const renderMessage = post.mock.calls.find(
    ([message]) => message.type === "composer-preview-render",
  )![0];
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frame.contentWindow,
      data: {
        ...renderMessage,
        type: "composer-preview-result",
        status: "ready",
      },
    }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Download this revision" }),
    ).toBeInTheDocument(),
  );
  rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId={nextRevision}
      format="docx"
      onDownload={onDownload}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Download this revision" }),
  );
  expect(onDownload).toHaveBeenLastCalledWith(
    expect.objectContaining({
      draftId,
      revisionId: firstRevision,
      downloadUrl: revisionArtifactPath(draftId, firstRevision, "download"),
    }),
  );
  expect(
    screen.queryByRole("button", {
      name: `Download requested revision ${nextRevision}`,
    }),
  ).toBeNull();
  vi.unstubAllGlobals();
});

test("a child render failure removes only the pending frame", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => officeResponse()),
  );
  render(
    <ComposerPreview
      draftId={draftId}
      revisionId={firstRevision}
      format="docx"
    />,
  );
  const frame = (await screen.findByTitle(
    `DOCX revision ${firstRevision} preview`,
  )) as HTMLIFrameElement;
  const post = vi.spyOn(frame.contentWindow!, "postMessage");
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frame.contentWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() =>
    expect(
      post.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const renderMessage = post.mock.calls.find(
    ([message]) => message.type === "composer-preview-render",
  )![0];
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frame.contentWindow,
      data: {
        ...renderMessage,
        type: "composer-preview-result",
        status: "error",
      },
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(
      "cannot be shown safely",
    ),
  );
  expect(
    screen.queryByTitle(`DOCX revision ${firstRevision} preview`),
  ).toBeNull();
  expect(
    screen.queryByRole("link", { name: "Download this revision" }),
  ).toBeNull();
  expect(
    screen.getByRole("link", {
      name: `Download requested revision ${firstRevision}`,
    }),
  ).toHaveAttribute(
    "href",
    revisionArtifactPath(draftId, firstRevision, "download"),
  );
  vi.unstubAllGlobals();
});

test("failed revision replacement retains the displayed revision's download", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => officeResponse()),
  );
  const { rerender } = render(
    <ComposerPreview
      draftId={draftId}
      revisionId={firstRevision}
      format="docx"
    />,
  );
  const first = (await screen.findByTitle(
    `DOCX revision ${firstRevision} preview`,
  )) as HTMLIFrameElement;
  const firstPost = vi.spyOn(first.contentWindow!, "postMessage");
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: first.contentWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() =>
    expect(
      firstPost.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const firstRender = firstPost.mock.calls.find(
    ([message]) => message.type === "composer-preview-render",
  )![0];
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: first.contentWindow,
      data: {
        ...firstRender,
        type: "composer-preview-result",
        status: "ready",
      },
    }),
  );
  await waitFor(() => expect(first).toHaveAttribute("aria-hidden", "false"));

  rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId={nextRevision}
      format="docx"
    />,
  );
  const second = (await screen.findByTitle(
    `DOCX revision ${nextRevision} preview`,
  )) as HTMLIFrameElement;
  const secondPost = vi.spyOn(second.contentWindow!, "postMessage");
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: second.contentWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() =>
    expect(
      secondPost.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const secondRender = secondPost.mock.calls.find(
    ([message]) => message.type === "composer-preview-render",
  )![0];
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: second.contentWindow,
      data: {
        ...secondRender,
        type: "composer-preview-result",
        status: "error",
      },
    }),
  );
  await waitFor(() =>
    expect(
      screen.queryByTitle(`DOCX revision ${nextRevision} preview`),
    ).toBeNull(),
  );
  expect(first).toHaveAttribute("aria-hidden", "false");
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute(
    "href",
    revisionArtifactPath(draftId, firstRevision, "download"),
  );
  expect(
    screen.queryByRole("link", {
      name: `Download requested revision ${nextRevision}`,
    }),
  ).toBeNull();
  vi.unstubAllGlobals();
});

test("zoom and position messages apply only to the active Office frame", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      officeResponse(
        undefined,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      ),
    ),
  );
  render(
    <ComposerPreview
      draftId={draftId}
      revisionId={firstRevision}
      format="pptx"
    />,
  );
  const frame = (await screen.findByTitle(
    `PPTX revision ${firstRevision} preview`,
  )) as HTMLIFrameElement;
  const post = vi.spyOn(frame.contentWindow!, "postMessage");
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frame.contentWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() =>
    expect(
      post.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const renderMessage = post.mock.calls.find(
    ([message]) => message.type === "composer-preview-render",
  )![0];
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frame.contentWindow,
      data: {
        ...renderMessage,
        type: "composer-preview-result",
        status: "ready",
      },
    }),
  );
  await waitFor(() => expect(frame).toHaveAttribute("aria-hidden", "false"));
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByText("125%")).toBeInTheDocument();
  expect(
    post.mock.calls.some(
      ([message]) =>
        message.type === "composer-preview-zoom" && message.zoom === 1.25,
    ),
  ).toBe(true);
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frame.contentWindow,
      data: {
        ...renderMessage,
        token: "forged",
        type: "composer-preview-runtime-error",
      },
    }),
  );
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frame.contentWindow,
      data: { ...renderMessage, type: "composer-preview-runtime-error" },
    }),
  );
  expect(screen.getByRole("alert")).toHaveTextContent("cannot be shown safely");
  vi.unstubAllGlobals();
});
