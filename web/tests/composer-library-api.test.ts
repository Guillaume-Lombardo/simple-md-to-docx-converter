import type { ApiTransport } from "../src/api/transport";
import {
  LibraryApi,
  type LibraryDocument,
  readLibraryDownload,
} from "../src/composer/library-api";

const documentId = "00000000-0000-4000-8000-000000000301";
const versionId = "00000000-0000-4000-8000-000000000302";
const document: LibraryDocument = {
  id: documentId,
  owner_id: "owner",
  title: "Notes",
  version: 2,
  etag: '"2"',
  current_version_id: versionId,
  shared_with: [],
  created_at: "2026-10-21T00:00:00Z",
  updated_at: "2026-10-21T00:00:00Z",
};

test("library requests use exact routes, CSRF and document preconditions", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({
      documents: [],
      versions: [],
      results: [],
      excerpts: [],
    }),
    jsonWithMetadata: vi.fn(),
    multipart: vi.fn().mockResolvedValue(document),
    multipartWithMetadata: vi.fn().mockResolvedValue({ data: document }),
    download: vi.fn().mockResolvedValue(new Response("x")),
  };
  const api = new LibraryApi(transport as unknown as ApiTransport);
  const file = new File(["# Notes"], "notes.md");
  const base = "/api/v1/composer/library";

  await api.list(" field notes ", 100);
  await api.list();
  await api.upload(file, " Notes ");
  await api.upload(file, " ");
  await api.replace(document, file);
  await api.versions(documentId);
  await api.download(documentId, versionId);
  await api.grant(document, "user/1");
  await api.revoke(document, "user/1");
  await api.remove(document);
  await api.search("turbine", 5);
  await api.excerpts([{ version_id: versionId, chunk: 0 }]);

  expect(transport.json.mock.calls.map((call) => call[0])).toEqual([
    `${base}?limit=100&offset=100&query=field+notes`,
    `${base}?limit=100&offset=0`,
    `${base}/${documentId}/versions?limit=100&offset=0`,
    `${base}/${documentId}/grants/user%2F1`,
    `${base}/${documentId}/grants/user%2F1`,
    `${base}/${documentId}`,
    `${base}/search`,
    `${base}/excerpts`,
  ]);
  for (const index of [3, 4, 5])
    expect(transport.json.mock.calls[index]![2]).toMatchObject({
      csrf: true,
      etag: '"2"',
    });
  expect(transport.json.mock.calls[5]![2]).toMatchObject({ method: "DELETE" });
  expect(JSON.parse(transport.json.mock.calls[6]![2].body as string)).toEqual({
    query: "turbine",
    limit: 5,
  });
  expect(JSON.parse(transport.json.mock.calls[7]![2].body as string)).toEqual({
    references: [{ version_id: versionId, chunk: 0 }],
  });
  const titled = transport.multipart.mock.calls[0]![1] as FormData;
  expect(titled.get("title")).toBe("Notes");
  expect((transport.multipart.mock.calls[1]![1] as FormData).has("title")).toBe(
    false,
  );
  expect(transport.multipartWithMetadata.mock.calls[0]![0]).toBe(
    `${base}/${documentId}/versions`,
  );
  expect(transport.multipartWithMetadata.mock.calls[0]![3]).toMatchObject({
    csrf: true,
    etag: '"2"',
  });
  expect(transport.download.mock.calls[0]![0]).toBe(
    `${base}/${documentId}/versions/${versionId}/content`,
  );
});

test("library downloads require exact media, filename and nosniff", async () => {
  const headers = (overrides: Record<string, string>) =>
    new Response("# Notes", {
      headers: {
        "content-type": "text/markdown; charset=utf-8",
        "content-disposition": 'attachment; filename="notes.md"',
        "x-content-type-options": "nosniff",
        ...overrides,
      },
    });

  const download = await readLibraryDownload(headers({}));
  expect(download.filename).toBe("notes.md");
  expect(await download.blob.text()).toBe("# Notes");
  for (const overrides of <Record<string, string>[]>[
    { "content-type": "text/html" },
    { "content-disposition": 'attachment; filename="notes.txt"' },
    { "content-disposition": "attachment" },
    { "x-content-type-options": "" },
  ])
    await expect(readLibraryDownload(headers(overrides))).rejects.toThrow(
      "Unexpected library download response.",
    );
});
