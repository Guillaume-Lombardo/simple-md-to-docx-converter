"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Alert } from "../../components/primitives";
import { ApiError } from "../api/transport";
import type { EffectiveUser } from "../auth/controller";
import {
  readTemplateDownload,
  saveTemplateDownload,
} from "../admin/operations";
import {
  FillTemplateApi,
  type FillField,
  type FillSchema,
  type FillTemplate,
  type FillTemplateVersion,
} from "./fill-template-api";

const defaultApi = new FillTemplateApi();
const emptySchema = (): FillSchema => ({ fields: [], repeats: [] });
const emptyField = (): FillField => ({
  name: "",
  type: "text",
  required: true,
  constraints: {},
});

function failureMessage(reason: unknown, expire: () => void): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) {
      expire();
      return "Your session ended. Please sign in again.";
    }
    if (reason.status === 403 || reason.status === 404)
      return "This typed template is no longer available to you.";
    if (reason.status === 409 || reason.status === 412)
      return "This template changed. Review the current version before retrying.";
    return reason.message;
  }
  return "The typed template request could not be completed. Try again.";
}

function FieldEditor({
  field,
  label,
  onChange,
  onRemove,
}: {
  field: FillField;
  label: string;
  onChange: (field: FillField) => void;
  onRemove: () => void;
}) {
  const [constraintsText, setConstraintsText] = useState(
    JSON.stringify(field.constraints),
  );
  const [defaultText, setDefaultText] = useState(
    field.default === undefined ? "" : JSON.stringify(field.default),
  );

  return (
    <fieldset className="grid gap-2 rounded-control border border-muted p-3">
      <legend>{label}</legend>
      <label className="grid gap-1">
        Field name
        <input
          required
          value={field.name}
          onChange={(event) => onChange({ ...field, name: event.target.value })}
        />
      </label>
      <label className="grid gap-1">
        Field type
        <select
          value={field.type}
          onChange={(event) =>
            onChange({
              ...field,
              type: event.target.value as FillField["type"],
            })
          }
        >
          <option value="text">Text</option>
          <option value="date">Date</option>
          <option value="boolean">Boolean</option>
          <option value="integer">Integer</option>
        </select>
      </label>
      <label>
        <input
          checked={field.required}
          onChange={(event) =>
            onChange({ ...field, required: event.target.checked })
          }
          type="checkbox"
        />{" "}
        Required
      </label>
      <label className="grid gap-1">
        Constraints (JSON object)
        <textarea
          value={constraintsText}
          onChange={(event) => {
            setConstraintsText(event.target.value);
            try {
              const parsed: unknown = JSON.parse(event.target.value);
              if (
                !parsed ||
                typeof parsed !== "object" ||
                Array.isArray(parsed)
              )
                throw new Error("Expected an object");
              event.target.setCustomValidity("");
              onChange({
                ...field,
                constraints: parsed as FillField["constraints"],
              });
            } catch {
              event.target.setCustomValidity("Enter a JSON object.");
            }
          }}
        />
      </label>
      <label className="grid gap-1">
        Explicit default (JSON value; blank means none)
        <input
          value={defaultText}
          onChange={(event) => {
            setDefaultText(event.target.value);
            if (!event.target.value.trim()) {
              event.target.setCustomValidity("");
              const withoutDefault = { ...field };
              delete withoutDefault.default;
              onChange(withoutDefault);
              return;
            }
            try {
              event.target.setCustomValidity("");
              onChange({ ...field, default: JSON.parse(event.target.value) });
            } catch {
              event.target.setCustomValidity(
                "Enter a JSON value or leave blank.",
              );
            }
          }}
        />
      </label>
      <button onClick={onRemove} type="button">
        Remove {label.toLowerCase()}
      </button>
    </fieldset>
  );
}

function SchemaEditor({
  schema,
  onChange,
}: {
  schema: FillSchema;
  onChange: (schema: FillSchema) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3>Named fields</h3>
        <p>Names must match supported content controls in the DOCX file.</p>
      </div>
      {schema.fields.map((field, index) => (
        <FieldEditor
          field={field}
          key={index}
          label={`Field ${index + 1}`}
          onChange={(value) =>
            onChange({
              ...schema,
              fields: schema.fields.map((item, at) =>
                at === index ? value : item,
              ),
            })
          }
          onRemove={() =>
            onChange({
              ...schema,
              fields: schema.fields.filter((_, at) => at !== index),
            })
          }
        />
      ))}
      <button
        onClick={() =>
          onChange({ ...schema, fields: [...schema.fields, emptyField()] })
        }
        type="button"
      >
        Add field
      </button>
      <div>
        <h3>Repeatable sections</h3>
        <p>Each section has a bounded number of rows and named child fields.</p>
      </div>
      {schema.repeats.map((repeat, repeatIndex) => (
        <fieldset
          className="space-y-3 rounded-control border border-muted p-3"
          key={repeatIndex}
        >
          <legend>Repeat {repeatIndex + 1}</legend>
          <label className="grid gap-1">
            Repeat name
            <input
              required
              value={repeat.name}
              onChange={(event) =>
                onChange({
                  ...schema,
                  repeats: schema.repeats.map((item, at) =>
                    at === repeatIndex
                      ? { ...item, name: event.target.value }
                      : item,
                  ),
                })
              }
            />
          </label>
          <div className="flex gap-3">
            {(["min_items", "max_items"] as const).map((key) => (
              <label className="grid gap-1" key={key}>
                {key === "min_items" ? "Minimum rows" : "Maximum rows"}
                <input
                  min={key === "min_items" ? 0 : 1}
                  required
                  type="number"
                  value={repeat[key]}
                  onChange={(event) =>
                    onChange({
                      ...schema,
                      repeats: schema.repeats.map((item, at) =>
                        at === repeatIndex
                          ? { ...item, [key]: Number(event.target.value) }
                          : item,
                      ),
                    })
                  }
                />
              </label>
            ))}
          </div>
          {repeat.fields.map((field, fieldIndex) => (
            <FieldEditor
              field={field}
              key={fieldIndex}
              label={`Repeat ${repeatIndex + 1} field ${fieldIndex + 1}`}
              onChange={(value) =>
                onChange({
                  ...schema,
                  repeats: schema.repeats.map((item, at) =>
                    at === repeatIndex
                      ? {
                          ...item,
                          fields: item.fields.map((child, childAt) =>
                            childAt === fieldIndex ? value : child,
                          ),
                        }
                      : item,
                  ),
                })
              }
              onRemove={() =>
                onChange({
                  ...schema,
                  repeats: schema.repeats.map((item, at) =>
                    at === repeatIndex
                      ? {
                          ...item,
                          fields: item.fields.filter(
                            (_, childAt) => childAt !== fieldIndex,
                          ),
                        }
                      : item,
                  ),
                })
              }
            />
          ))}
          <div className="flex gap-3">
            <button
              onClick={() =>
                onChange({
                  ...schema,
                  repeats: schema.repeats.map((item, at) =>
                    at === repeatIndex
                      ? { ...item, fields: [...item.fields, emptyField()] }
                      : item,
                  ),
                })
              }
              type="button"
            >
              Add repeat field
            </button>
            <button
              onClick={() =>
                onChange({
                  ...schema,
                  repeats: schema.repeats.filter((_, at) => at !== repeatIndex),
                })
              }
              type="button"
            >
              Remove repeat
            </button>
          </div>
        </fieldset>
      ))}
      <button
        onClick={() =>
          onChange({
            ...schema,
            repeats: [
              ...schema.repeats,
              { name: "", min_items: 0, max_items: 1, fields: [emptyField()] },
            ],
          })
        }
        type="button"
      >
        Add repeatable section
      </button>
    </div>
  );
}

export function FillTemplatesWorkspace({
  api = defaultApi,
  expire,
  user,
}: {
  api?: FillTemplateApi;
  expire: () => void;
  user: EffectiveUser;
}) {
  const [templates, setTemplates] = useState<FillTemplate[]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [selected, setSelected] = useState<FillTemplate | null>(null);
  const [activeVersion, setActiveVersion] =
    useState<FillTemplateVersion | null>(null);
  const [versions, setVersions] = useState<FillTemplateVersion[]>([]);
  const [versionOffset, setVersionOffset] = useState(0);
  const [moreVersions, setMoreVersions] = useState(false);
  const [inspectedVersionId, setInspectedVersionId] = useState("");
  const [name, setName] = useState("");
  const [schema, setSchema] = useState<FillSchema>(emptySchema);
  const [file, setFile] = useState<File | null>(null);
  const [grantUser, setGrantUser] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(
    async (nextOffset = 0, signal?: AbortSignal) => {
      const page = await api.list(nextOffset, signal);
      if (signal?.aborted) return;
      setTemplates((previous) =>
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
        if (!controller.signal.aborted)
          setError(failureMessage(reason, expire));
      }),
    );
    return () => controller.abort();
  }, [expire, load]);

  async function select(id: string): Promise<void> {
    setBusy(true);
    setError("");
    try {
      const [response, history] = await Promise.all([
        api.get(id),
        api.versions(id),
      ]);
      const template = response.data;
      setSelected(template);
      setName(template.name);
      setVersions(history);
      setVersionOffset(0);
      setMoreVersions(history.length === 100);
      setInspectedVersionId(template.active_version_id ?? "");
      if (template.active_version_id) {
        const version = await api.version(id, template.active_version_id);
        setActiveVersion(version);
        setSchema(version.schema);
      } else {
        setActiveVersion(null);
        setSchema(emptySchema());
      }
      setFile(null);
      setGrantUser("");
    } catch (reason) {
      setError(failureMessage(reason, expire));
    } finally {
      setBusy(false);
    }
  }

  function newTemplate(): void {
    setSelected(null);
    setActiveVersion(null);
    setVersions([]);
    setInspectedVersionId("");
    setName("");
    setSchema(emptySchema());
    setFile(null);
    setError("");
    setNotice("");
  }

  async function save(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (
      !file ||
      !file.name.toLowerCase().endsWith(".docx") ||
      file.size === 0
    ) {
      setError("Choose a non-empty DOCX file.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (selected) {
        await api.replace(selected.id, selected.etag, schema, file);
        await select(selected.id);
        setNotice("A new immutable typed template version was created.");
      } else {
        const created = await api.create(name.trim(), schema, file);
        await load();
        await select(created.data.id);
        setNotice("Typed filling template created.");
      }
    } catch (reason) {
      const message = failureMessage(reason, expire);
      if (
        selected &&
        reason instanceof ApiError &&
        [409, 412].includes(reason.status)
      )
        await select(selected.id);
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  async function changeGrant(userId: string, grant: boolean): Promise<void> {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      if (grant) await api.grant(selected.id, userId, selected.etag);
      else await api.revoke(selected.id, userId, selected.etag);
      await select(selected.id);
      setGrantUser("");
      setNotice(
        grant ? "Template access granted." : "Template access revoked.",
      );
    } catch (reason) {
      const message = failureMessage(reason, expire);
      if (reason instanceof ApiError && [409, 412].includes(reason.status))
        await select(selected.id);
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  async function download(): Promise<void> {
    if (!selected || !inspectedVersionId) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.download(selected.id, inspectedVersionId);
      saveTemplateDownload(await readTemplateDownload(response));
    } catch (reason) {
      setError(failureMessage(reason, expire));
    } finally {
      setBusy(false);
    }
  }

  const editable = !selected || selected.owner_id === user.id;

  return (
    <div className="space-y-6">
      <nav aria-label="Typed template breadcrumb">
        <Link className="text-accent underline" href="/composer">
          Composer
        </Link>
        {" / Typed filling templates"}
      </nav>
      <div>
        <h1>Typed filling templates</h1>
        <p>
          These DOCX templates contain named fields for Composer filling. They
          are separate from the Pandoc style-reference catalog.
        </p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <p role="status">{notice}</p>}
      <section aria-label="Authorized typed templates" className="space-y-3">
        <h2>Authorized typed templates</h2>
        <button disabled={busy} onClick={newTemplate} type="button">
          New typed template
        </button>
        {templates.length === 0 ? (
          <p>No typed filling templates are available.</p>
        ) : (
          <ul className="space-y-2">
            {templates.map((template) => (
              <li key={template.id}>
                <button
                  aria-current={
                    selected?.id === template.id ? "true" : undefined
                  }
                  disabled={busy}
                  onClick={() => void select(template.id)}
                  type="button"
                >
                  {template.name}{" "}
                  {template.owner_id === user.id ? "(mine)" : "(shared)"}
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
                setError(failureMessage(reason, expire)),
              )
            }
            type="button"
          >
            Load more templates
          </button>
        )}
      </section>
      <section aria-label="Typed template details" className="space-y-4">
        <h2>{selected ? selected.name : "Create typed template"}</h2>
        {selected && (
          <p>
            Owner: {selected.owner_id} · Catalog version: {selected.version}
          </p>
        )}
        {activeVersion && (
          <div className="space-y-2">
            <p>
              Active immutable version {activeVersion.number} ·{" "}
              {activeVersion.id}
            </p>
            <label className="grid gap-1">
              Inspect template version
              <select
                value={inspectedVersionId}
                onChange={(event) => setInspectedVersionId(event.target.value)}
              >
                {versions.map((item) => (
                  <option key={item.id} value={item.id}>
                    Version {item.number} · {item.id}
                  </option>
                ))}
              </select>
            </label>
            {moreVersions && selected && (
              <button
                disabled={busy}
                onClick={() => {
                  void api
                    .versions(selected.id, versionOffset + 100)
                    .then((page) => {
                      setVersions((previous) => [...previous, ...page]);
                      setVersionOffset((previous) => previous + 100);
                      setMoreVersions(page.length === 100);
                    })
                    .catch((reason: unknown) =>
                      setError(failureMessage(reason, expire)),
                    );
                }}
                type="button"
              >
                Load older versions
              </button>
            )}
            <p>
              DOCX SHA-256:{" "}
              {versions.find((item) => item.id === inspectedVersionId)
                ?.docx_sha256 ?? activeVersion.docx_sha256}
            </p>
            <button
              disabled={busy}
              onClick={() => void download()}
              type="button"
            >
              Download selected DOCX
            </button>
          </div>
        )}
        {editable && (
          <form className="space-y-4" onSubmit={(event) => void save(event)}>
            {!selected && (
              <label className="grid gap-1">
                Template name
                <input
                  disabled={busy}
                  required
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
            )}
            <SchemaEditor
              key={activeVersion?.id ?? "new"}
              schema={schema}
              onChange={setSchema}
            />
            <label className="grid gap-1">
              DOCX file
              <input
                accept=".docx"
                disabled={busy}
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                required
                type="file"
              />
            </label>
            <button disabled={busy} type="submit">
              {selected ? "Create new version" : "Create typed template"}
            </button>
          </form>
        )}
      </section>
      {selected && editable && (
        <section aria-label="Typed template sharing" className="space-y-3">
          <h2>Share with named accounts</h2>
          <label className="grid gap-1">
            Account ID
            <input
              value={grantUser}
              onChange={(event) => setGrantUser(event.target.value)}
            />
          </label>
          <button
            disabled={busy || !grantUser.trim()}
            onClick={() => void changeGrant(grantUser.trim(), true)}
            type="button"
          >
            Grant access
          </button>
          {selected.shared_with.length === 0 ? (
            <p>This template is private.</p>
          ) : (
            <ul>
              {selected.shared_with.map((userId) => (
                <li className="flex items-center gap-3" key={userId}>
                  <span>{userId}</span>
                  <button
                    disabled={busy}
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
