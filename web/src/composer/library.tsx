"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Alert } from "../../components/primitives";
import { saveTemplateDownload } from "../admin/operations";
import { ApiError } from "../api/transport";
import type { EffectiveUser } from "../auth/controller";
import {
  LIBRARY_ACCEPT,
  LibraryApi,
  type LibraryDocument,
  type LibraryExcerpt,
  type LibrarySearchHit,
  type LibraryVersion,
  readLibraryDownload,
} from "./library-api";

const defaultApi = new LibraryApi();

function errorMessage(reason: unknown, expire: () => void): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) {
      expire();
      return "Your session ended. Please sign in again.";
    }
    if (reason.status === 403 || reason.status === 404)
      return "This library document is no longer available to you.";
    if (reason.status === 409 || reason.status === 412)
      return "This library document changed. Review the latest version before retrying.";
    return reason.message;
  }
  return "The reference library is unavailable. Try again.";
}

export function LibraryWorkspace({
  api = defaultApi,
  expire,
  user,
  save = saveTemplateDownload,
}: {
  api?: LibraryApi;
  expire: () => void;
  user: EffectiveUser;
  save?: typeof saveTemplateDownload;
}) {
  const [documents, setDocuments] = useState<LibraryDocument[]>([]);
  const [filter, setFilter] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [uploadKey, setUploadKey] = useState(0);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<LibrarySearchHit[] | null>(null);
  const [excerpt, setExcerpt] = useState<LibraryExcerpt | null>(null);
  const [selected, setSelected] = useState<LibraryDocument | null>(null);
  const [versions, setVersions] = useState<LibraryVersion[]>([]);
  const [replacement, setReplacement] = useState<File | null>(null);
  const [grantUser, setGrantUser] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const run = useCallback(
    async (action: () => Promise<void>, success = "") => {
      setBusy(true);
      setError("");
      setNotice("");
      try {
        await action();
        if (success) setNotice(success);
      } catch (reason) {
        setError(errorMessage(reason, expire));
      } finally {
        setBusy(false);
      }
    },
    [expire],
  );

  const refresh = useCallback(
    async (current = filter) => {
      const listed = await api.list(current);
      setDocuments(listed);
      return listed;
    },
    [api, filter],
  );

  useEffect(() => {
    const controller = new AbortController();
    api
      .list("", 0, controller.signal)
      .then(setDocuments)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(reason, expire));
      });
    return () => controller.abort();
  }, [api, expire]);

  async function open(document: LibraryDocument) {
    setSelected(document);
    setVersions(await api.versions(document.id));
  }

  async function reopen(document: LibraryDocument) {
    const listed = await refresh();
    const current = listed.find((item) => item.id === document.id) ?? document;
    await open(current);
  }

  function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    void run(async () => {
      const created = await api.upload(file, title);
      setFile(null);
      setTitle("");
      setUploadKey((value) => value + 1);
      await reopen(created);
    }, "Library document uploaded.");
  }

  function search(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      setExcerpt(null);
      setHits(await api.search(query.trim()));
    });
  }

  const owned = selected?.owner_id === user.id;

  return (
    <div className="space-y-6">
      <nav aria-label="Composer breadcrumb">
        <Link className="text-accent underline" href="/composer">
          Composer
        </Link>
        {" / Library"}
      </nav>
      <div>
        <h1>Reference library</h1>
        <p>
          Keep private source documents that a model step may cite. Documents
          are private until you share them with a named account, and a citation
          never grants access by itself.
        </p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <p role="status">{notice}</p>}
      <section aria-label="Upload a library document" className="space-y-3">
        <h2>Upload a document</h2>
        <form className="space-y-3" onSubmit={upload}>
          <label className="grid gap-1">
            Source (.md, .txt, .docx, .pptx)
            <input
              accept={LIBRARY_ACCEPT}
              key={uploadKey}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              type="file"
            />
          </label>
          <label className="grid gap-1">
            Title (optional)
            <input
              maxLength={200}
              onChange={(event) => setTitle(event.target.value)}
              value={title}
            />
          </label>
          <button disabled={busy || !file} type="submit">
            Upload document
          </button>
        </form>
      </section>
      <section aria-label="Search the library" className="space-y-3">
        <h2>Search</h2>
        <form className="flex flex-wrap gap-3" onSubmit={search}>
          <label className="grid gap-1">
            Search terms
            <input
              maxLength={200}
              onChange={(event) => setQuery(event.target.value)}
              value={query}
            />
          </label>
          <button disabled={busy || !query.trim()} type="submit">
            Search library
          </button>
        </form>
        {hits !== null &&
          (hits.length === 0 ? (
            <p>No matching excerpts.</p>
          ) : (
            <ul aria-label="Search results" className="space-y-2">
              {hits.map((hit) => (
                <li key={`${hit.version_id}#${hit.chunk}`}>
                  <strong>{hit.title}</strong> · version {hit.version_number} ·
                  excerpt {hit.chunk + 1}
                  <p className="text-sm">{hit.snippet}</p>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        const [item] = await api.excerpts([
                          { version_id: hit.version_id, chunk: hit.chunk },
                        ]);
                        setExcerpt(item ?? null);
                      })
                    }
                    type="button"
                  >
                    Show excerpt
                  </button>
                </li>
              ))}
            </ul>
          ))}
        {excerpt && (
          <figure className="rounded-control border border-muted p-3">
            <blockquote className="whitespace-pre-wrap">
              {excerpt.text}
            </blockquote>
            <figcaption className="text-sm">
              {excerpt.title}, version {excerpt.version_number}, excerpt{" "}
              {excerpt.chunk + 1} · SHA-256 {excerpt.version_sha256}
            </figcaption>
          </figure>
        )}
      </section>
      <section aria-label="Library documents" className="space-y-3">
        <h2>Documents</h2>
        <form
          className="flex flex-wrap gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              await refresh(filter);
            });
          }}
        >
          <label className="grid gap-1">
            Filter titles
            <input
              onChange={(event) => setFilter(event.target.value)}
              value={filter}
            />
          </label>
          <button disabled={busy} type="submit">
            Filter
          </button>
        </form>
        {documents.length === 0 ? (
          <p>No library documents are available.</p>
        ) : (
          <ul className="space-y-2">
            {documents.map((document) => (
              <li key={document.id}>
                <button
                  aria-pressed={selected?.id === document.id}
                  disabled={busy}
                  onClick={() => void run(() => open(document))}
                  type="button"
                >
                  {document.title}
                </button>{" "}
                · version {document.version}
                {document.owner_id === user.id ? "" : " · shared with you"}
              </li>
            ))}
          </ul>
        )}
      </section>
      {selected && (
        <section aria-label="Library document details" className="space-y-4">
          <h2>{selected.title}</h2>
          <ul aria-label="Versions" className="space-y-1">
            {versions.map((version) => (
              <li key={version.id}>
                Version {version.number} · {version.chunk_count} excerpts ·{" "}
                {version.size} bytes{" "}
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      save(
                        await readLibraryDownload(
                          await api.download(selected.id, version.id),
                        ),
                      );
                    })
                  }
                  type="button"
                >
                  Download version {version.number}
                </button>
              </li>
            ))}
          </ul>
          {owned && (
            <>
              <form
                className="space-y-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!replacement) return;
                  void run(async () => {
                    const replaced = await api.replace(selected, replacement);
                    setReplacement(null);
                    await reopen(replaced.data);
                  }, "New library version published.");
                }}
              >
                <label className="grid gap-1">
                  Replacement source
                  <input
                    accept={LIBRARY_ACCEPT}
                    onChange={(event) =>
                      setReplacement(event.target.files?.[0] ?? null)
                    }
                    type="file"
                  />
                </label>
                <button disabled={busy || !replacement} type="submit">
                  Publish new version
                </button>
              </form>
              <div className="space-y-2">
                <h3>Share with named accounts</h3>
                <label className="grid gap-1">
                  Account ID
                  <input
                    onChange={(event) => setGrantUser(event.target.value)}
                    value={grantUser}
                  />
                </label>
                <button
                  disabled={busy || !grantUser.trim()}
                  onClick={() =>
                    void run(async () => {
                      const shared = await api.grant(
                        selected,
                        grantUser.trim(),
                      );
                      setGrantUser("");
                      await reopen(shared);
                    }, "Library document shared.")
                  }
                  type="button"
                >
                  Share document
                </button>
                <ul className="space-y-1">
                  {selected.shared_with.map((userId) => (
                    <li className="flex items-center gap-3" key={userId}>
                      {userId}
                      <button
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await reopen(await api.revoke(selected, userId));
                          }, "Library access revoked.")
                        }
                        type="button"
                      >
                        Revoke {userId}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await api.remove(selected);
                    setSelected(null);
                    setVersions([]);
                    await refresh();
                  }, "Library document deleted.")
                }
                type="button"
              >
                Delete document
              </button>
            </>
          )}
        </section>
      )}
    </div>
  );
}
