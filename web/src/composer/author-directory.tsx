"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Alert } from "../../components/primitives";
import { ApiError } from "../api/transport";
import type { EffectiveUser } from "../auth/controller";
import {
  AuthorDirectoryApi,
  type AuthorEntry,
  type AuthorField,
} from "./author-directory-api";

interface FieldRow {
  key: string;
  value: string | null;
  provenance: AuthorField["provenance"];
  sourceReference: string;
  lastValue: string;
  lastProvenance: AuthorField["provenance"];
}

const defaultApi = new AuthorDirectoryApi();
const blankField = (): FieldRow => ({
  key: "",
  value: "",
  provenance: "supplied",
  sourceReference: "",
  lastValue: "",
  lastProvenance: "supplied",
});

function rows(fields: Record<string, AuthorField>): FieldRow[] {
  const entries = Object.entries(fields).map(([key, field]) => ({
    key,
    value: field.value,
    provenance: field.provenance,
    sourceReference: field.source_reference ?? "",
    lastValue: field.value ?? "",
    lastProvenance:
      field.provenance === "unresolved" ? "supplied" : field.provenance,
  }));
  return entries.length > 0 ? entries : [blankField()];
}

function errorMessage(reason: unknown, expire: () => void): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) {
      expire();
      return "Your session ended. Please sign in again.";
    }
    if (reason.status === 403 || reason.status === 404)
      return "This author entry is no longer available to you.";
    if (reason.status === 409 || reason.status === 412)
      return "This author entry changed. Review the latest version before retrying.";
    return reason.message;
  }
  return "The author directory is unavailable. Try again.";
}

export function AuthorDirectory({
  api = defaultApi,
  expire,
  user,
}: {
  api?: AuthorDirectoryApi;
  expire: () => void;
  user: EffectiveUser;
}) {
  const [authors, setAuthors] = useState<AuthorEntry[]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [selected, setSelected] = useState<AuthorEntry | null>(null);
  const [etag, setEtag] = useState("");
  const [name, setName] = useState("");
  const [fields, setFields] = useState<FieldRow[]>([blankField()]);
  const [grantUser, setGrantUser] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(
    async (nextOffset = 0, signal?: AbortSignal) => {
      const page = await api.list(nextOffset, signal);
      if (signal?.aborted) return;
      setAuthors((previous) =>
        nextOffset === 0 ? page : [...previous, ...page],
      );
      setOffset(nextOffset);
      setHasMore(page.length === 100);
    },
    [api],
  );

  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve().then(() =>
      load(0, controller.signal).catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(reason, expire));
      }),
    );
    return () => controller.abort();
  }, [expire, load]);

  async function select(id: string): Promise<void> {
    setBusy(true);
    setError("");
    try {
      const response = await api.get(id);
      if (!response.etag) throw new Error("Missing author revision");
      setSelected(response.data);
      setEtag(response.etag);
      setName(response.data.name);
      setFields(rows(response.data.fields));
      setGrantUser("");
    } catch (reason) {
      setError(errorMessage(reason, expire));
    } finally {
      setBusy(false);
    }
  }

  function newEntry(): void {
    setSelected(null);
    setEtag("");
    setName("");
    setFields([blankField()]);
    setError("");
    setNotice("");
  }

  function inputFields(): Record<string, AuthorField> | null {
    const result: Record<string, AuthorField> = {};
    for (const field of fields) {
      const key = field.key.trim();
      if (
        !key &&
        field.value === "" &&
        field.provenance === "supplied" &&
        !field.sourceReference.trim()
      )
        continue;
      if (!key || Object.hasOwn(result, key)) {
        setError("Each filled author field needs a unique name.");
        return null;
      }
      if (field.value !== null && !field.value.trim()) {
        setError("Enter a value or mark the author field unresolved.");
        return null;
      }
      result[key] = {
        value: field.value,
        provenance: field.provenance,
        ...(field.sourceReference.trim()
          ? { source_reference: field.sourceReference.trim() }
          : {}),
      };
    }
    return result;
  }

  async function save(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const savedFields = inputFields();
    if (!savedFields) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const input = { name: name.trim(), fields: savedFields };
      const response = selected
        ? await api.update(selected.id, etag, input)
        : { data: await api.create(input) };
      await load();
      await select(response.data.id);
      setNotice(selected ? "Author entry updated." : "Author entry created.");
    } catch (reason) {
      const message = errorMessage(reason, expire);
      if (reason instanceof ApiError && [409, 412].includes(reason.status)) {
        if (selected) await select(selected.id);
      }
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  async function changeGrant(userId: string, grant: boolean): Promise<void> {
    if (!selected || !etag) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (grant) await api.grant(selected.id, userId, etag);
      else await api.revoke(selected.id, userId, etag);
      await select(selected.id);
      setGrantUser("");
      setNotice(grant ? "Author access granted." : "Author access revoked.");
    } catch (reason) {
      const message = errorMessage(reason, expire);
      if (reason instanceof ApiError && [409, 412].includes(reason.status))
        await select(selected.id);
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  function updateField(index: number, update: Partial<FieldRow>): void {
    setFields((previous) =>
      previous.map((field, at) =>
        at === index ? { ...field, ...update } : field,
      ),
    );
  }

  const editable = !selected || selected.owner_id === user.id;

  return (
    <div className="space-y-6">
      <nav aria-label="Composer breadcrumb">
        <Link className="text-accent underline" href="/composer">
          Composer
        </Link>
        {" / Authors"}
      </nav>
      <div>
        <h1>Author directory</h1>
        <p>
          Keep author facts structured and choose an authorized entry when
          preparing a document. Entries are private until you share them with a
          named account.
        </p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <p role="status">{notice}</p>}
      <section aria-label="Authorized authors" className="space-y-3">
        <h2>Authorized authors</h2>
        <button disabled={busy} onClick={newEntry} type="button">
          New author
        </button>
        {authors.length === 0 ? (
          <p>No authorized author entries are available.</p>
        ) : (
          <ul className="space-y-2">
            {authors.map((author) => (
              <li key={author.id}>
                <button
                  aria-current={selected?.id === author.id ? "true" : undefined}
                  disabled={busy}
                  onClick={() => void select(author.id)}
                  type="button"
                >
                  {author.name}{" "}
                  {author.owner_id === user.id ? "(mine)" : "(shared)"}
                </button>
              </li>
            ))}
          </ul>
        )}
        {hasMore && (
          <button
            disabled={busy}
            onClick={() =>
              void load(offset + 100).catch((reason: unknown) =>
                setError(errorMessage(reason, expire)),
              )
            }
            type="button"
          >
            Load more authors
          </button>
        )}
      </section>
      <section aria-label="Author details" className="space-y-4">
        <h2>{selected ? `Author: ${selected.name}` : "Create author"}</h2>
        {selected && (
          <p>
            Owner: {selected.owner_id} · Version: {selected.version}
          </p>
        )}
        <form className="space-y-4" onSubmit={(event) => void save(event)}>
          <label className="grid gap-1">
            Author name
            <input
              disabled={!editable || busy}
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <h3>Named facts and provenance</h3>
          {fields.map((field, index) => (
            <fieldset
              className="grid gap-2 rounded-control border border-muted p-3"
              disabled={!editable || busy}
              key={index}
            >
              <legend>Field {index + 1}</legend>
              <label className="grid gap-1">
                Field name
                <input
                  value={field.key}
                  onChange={(event) =>
                    updateField(index, { key: event.target.value })
                  }
                />
              </label>
              <label className="grid gap-1">
                Value
                <textarea
                  value={field.value ?? ""}
                  onChange={(event) =>
                    updateField(index, {
                      value: event.target.value,
                      lastValue: event.target.value,
                      ...(field.provenance === "unresolved"
                        ? { provenance: field.lastProvenance }
                        : {}),
                    })
                  }
                />
              </label>
              <label>
                <input
                  checked={field.value === null}
                  onChange={(event) =>
                    updateField(index, {
                      value: event.target.checked ? null : field.lastValue,
                      provenance: event.target.checked
                        ? "unresolved"
                        : field.lastProvenance,
                      ...(event.target.checked
                        ? {
                            lastValue: field.value ?? field.lastValue,
                            lastProvenance:
                              field.provenance === "unresolved"
                                ? field.lastProvenance
                                : field.provenance,
                          }
                        : {}),
                    })
                  }
                  type="checkbox"
                />{" "}
                Unknown or unresolved
              </label>
              <label className="grid gap-1">
                Provenance
                <select
                  value={field.provenance}
                  onChange={(event) => {
                    const provenance = event.target
                      .value as AuthorField["provenance"];
                    updateField(index, {
                      provenance,
                      value:
                        provenance === "unresolved"
                          ? null
                          : (field.value ?? field.lastValue),
                      ...(provenance === "unresolved"
                        ? {
                            lastValue: field.value ?? field.lastValue,
                            lastProvenance:
                              field.provenance === "unresolved"
                                ? field.lastProvenance
                                : field.provenance,
                          }
                        : { lastProvenance: provenance }),
                    });
                  }}
                >
                  <option value="supplied">Supplied fact</option>
                  <option value="cited">Cited source</option>
                  <option value="model_suggested">Model suggestion</option>
                  <option value="human_approved">Human approved</option>
                  <option value="human_edited">Human correction</option>
                  <option value="unresolved">Unresolved</option>
                </select>
              </label>
              <label className="grid gap-1">
                Source reference (optional)
                <input
                  value={field.sourceReference}
                  onChange={(event) =>
                    updateField(index, { sourceReference: event.target.value })
                  }
                />
              </label>
              {editable && fields.length > 1 && (
                <button
                  onClick={() =>
                    setFields((previous) =>
                      previous.filter((_, at) => at !== index),
                    )
                  }
                  type="button"
                >
                  Remove field {index + 1}
                </button>
              )}
            </fieldset>
          ))}
          {editable && (
            <div className="flex gap-3">
              <button
                disabled={busy}
                onClick={() =>
                  setFields((previous) => [...previous, blankField()])
                }
                type="button"
              >
                Add field
              </button>
              <button disabled={busy} type="submit">
                {selected ? "Save author" : "Create author"}
              </button>
            </div>
          )}
        </form>
      </section>
      {selected && editable && (
        <section aria-label="Author sharing" className="space-y-3">
          <h2>Share with named accounts</h2>
          <label className="grid gap-1">
            Account ID
            <input
              value={grantUser}
              onChange={(event) => setGrantUser(event.target.value)}
            />
          </label>
          <button
            disabled={busy || !grantUser.trim() || !etag}
            onClick={() => void changeGrant(grantUser.trim(), true)}
            type="button"
          >
            Grant access
          </button>
          {selected.shared_with.length === 0 ? (
            <p>This entry is private.</p>
          ) : (
            <ul>
              {selected.shared_with.map((userId) => (
                <li className="flex items-center gap-3" key={userId}>
                  <span>{userId}</span>
                  <button
                    disabled={busy || !etag}
                    onClick={() => void changeGrant(userId, false)}
                    type="button"
                  >
                    Revoke {userId}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
