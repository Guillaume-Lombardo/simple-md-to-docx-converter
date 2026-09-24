"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert } from "../../components/primitives";
import { ApiError } from "../api/transport";
import { AuthorDirectoryApi, type AuthorEntry } from "./author-directory-api";
import type { ComposerDraft, ComposerRevisionSummary } from "./workspace-api";
import {
  FillTemplateApi,
  type FillField,
  type FillPlan,
  type FillProvenance,
  type FillSchema,
  type FillTemplate,
  type FillTemplateVersion,
  type FillValues,
} from "./fill-template-api";

const defaultTemplates = new FillTemplateApi();
const defaultAuthors = new AuthorDirectoryApi();

function planKey(ownerId: string, draftId: string) {
  return `composer:fill-plan:${ownerId}:${draftId}`;
}

function selectedValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  return String(value);
}

function typedValue(field: FillField, text: string): unknown {
  if (field.type === "integer") return text === "" ? undefined : Number(text);
  if (field.type === "boolean")
    return text === "" ? undefined : text === "true";
  return text === "" ? undefined : text;
}

function repeatPathIndex(path: string, name: string): number | null {
  const prefix = `${name}[`;
  if (!path.startsWith(prefix)) return null;
  const close = path.indexOf("]", prefix.length);
  if (close < 0 || path[close + 1] !== ".") return null;
  const raw = path.slice(prefix.length, close);
  return /^(0|[1-9][0-9]*)$/.test(raw) ? Number(raw) : null;
}

function protectedSource(source: FillProvenance | undefined): boolean {
  return source?.kind === "human_approved" || source?.kind === "human_edited";
}

function shiftedProvenance(
  provenance: Record<string, FillProvenance>,
  name: string,
  removedIndex: number,
): Record<string, FillProvenance> {
  const shifted: Record<string, FillProvenance> = {};
  for (const [path, source] of Object.entries(provenance)) {
    const index = repeatPathIndex(path, name);
    if (index === removedIndex) continue;
    const nextPath =
      index !== null && index > removedIndex
        ? `${name}[${index - 1}]${path.slice(`${name}[${index}]`.length)}`
        : path;
    shifted[nextPath] = source;
  }
  return shifted;
}

function errorMessage(reason: unknown, expire: () => void) {
  if (reason instanceof ApiError) {
    if (reason.status === 401) {
      expire();
      return "Your session ended. Please sign in again.";
    }
    if ([403, 404].includes(reason.status))
      return "An author, template, or plan is no longer available. Refresh your selections.";
    if ([409, 412].includes(reason.status))
      return "The draft or plan changed. Reload the current version before retrying.";
    if (reason.status >= 500)
      return "The fill request could not be completed. Try again.";
    return reason.message;
  }
  return "The fill request could not be completed. Try again.";
}

function FieldValue({
  field,
  label,
  value,
  onChange,
  disabled = false,
}: {
  field: FillField;
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid gap-1">
      <span>
        {label} {field.required ? "(required)" : "(optional)"}
      </span>
      {field.type === "boolean" ? (
        <select
          aria-label={label}
          disabled={disabled}
          value={
            value === null
              ? "ambiguous"
              : value === undefined
                ? ""
                : String(value)
          }
          onChange={(event) =>
            onChange(
              event.target.value === "ambiguous"
                ? null
                : typedValue(field, event.target.value),
            )
          }
        >
          <option value="">Unanswered</option>
          <option value="true">True</option>
          <option value="false">False</option>
          <option value="ambiguous">Ambiguous: ask me</option>
        </select>
      ) : (
        <div className="flex gap-2">
          <input
            aria-label={label}
            className="min-w-0 flex-1"
            disabled={disabled}
            type={
              field.type === "integer"
                ? "number"
                : field.type === "date"
                  ? "date"
                  : "text"
            }
            value={value === null ? "" : selectedValue(value)}
            onChange={(event) =>
              onChange(typedValue(field, event.target.value))
            }
          />
          <label>
            <input
              checked={value === null}
              disabled={disabled}
              onChange={(event) =>
                onChange(event.target.checked ? null : undefined)
              }
              type="checkbox"
            />{" "}
            Ambiguous
          </label>
        </div>
      )}
    </div>
  );
}

export function ComposerFillPanel({
  draft,
  expire,
  onPublished,
  ownerId,
  revisions,
  sourceRevisionId,
  templateApi = defaultTemplates,
  authorApi = defaultAuthors,
}: {
  draft: ComposerDraft;
  expire: () => void;
  onPublished: (revisionId: string) => void;
  ownerId: string;
  revisions: ComposerRevisionSummary[];
  sourceRevisionId: string | null;
  templateApi?: FillTemplateApi;
  authorApi?: AuthorDirectoryApi;
}) {
  const [templates, setTemplates] = useState<FillTemplate[]>([]);
  const [templateOffset, setTemplateOffset] = useState(0);
  const [moreTemplates, setMoreTemplates] = useState(false);
  const [authors, setAuthors] = useState<AuthorEntry[]>([]);
  const [authorOffset, setAuthorOffset] = useState(0);
  const [moreAuthors, setMoreAuthors] = useState(false);
  const [templateId, setTemplateId] = useState("");
  const [version, setVersion] = useState<FillTemplateVersion | null>(null);
  const [versions, setVersions] = useState<FillTemplateVersion[]>([]);
  const [authorIds, setAuthorIds] = useState<string[]>([]);
  const [values, setValues] = useState<FillValues>({});
  const [provenance, setProvenance] = useState<Record<string, FillProvenance>>(
    {},
  );
  const [plan, setPlan] = useState<FillPlan | null>(null);
  const [plans, setPlans] = useState<FillPlan[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [regenerateId, setRegenerateId] = useState("");
  const [dirty, setDirty] = useState(false);

  const choosePlan = useCallback(
    async (chosen: FillPlan): Promise<void> => {
      setPlan(chosen);
      setTemplateId(chosen.template_id);
      setValues(chosen.values);
      setProvenance(chosen.provenance);
      setDirty(false);
      try {
        setVersions(await templateApi.versions(chosen.template_id));
        setVersion(
          await templateApi.version(
            chosen.template_id,
            chosen.template_version_id,
          ),
        );
      } catch (reason) {
        setVersion(null);
        setError(errorMessage(reason, expire));
      }
    },
    [expire, templateApi],
  );

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      templateApi.list(0, controller.signal),
      authorApi.list(0, controller.signal),
      templateApi.plans(draft.id, 0, controller.signal),
    ])
      .then(([availableTemplates, availableAuthors, availablePlans]) => {
        if (controller.signal.aborted) return;
        setTemplates(availableTemplates);
        setTemplateOffset(0);
        setMoreTemplates(availableTemplates.length === 100);
        setAuthors(availableAuthors);
        setAuthorOffset(0);
        setMoreAuthors(availableAuthors.length === 100);
        setPlans(availablePlans);
        let saved: string | null = null;
        try {
          saved = sessionStorage.getItem(planKey(ownerId, draft.id));
        } catch {
          /* The server list still allows recovery. */
        }
        const restored =
          availablePlans.find((item) => item.id === saved) ??
          availablePlans.find((item) => item.state !== "published") ??
          availablePlans[0];
        if (restored) void choosePlan(restored);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(reason, expire));
      });
    return () => controller.abort();
  }, [authorApi, choosePlan, draft.id, expire, ownerId, templateApi]);

  async function loadMoreTemplates(): Promise<void> {
    const nextOffset = templateOffset + 100;
    setBusy(true);
    setError("");
    try {
      const page = await templateApi.list(nextOffset);
      setTemplates((previous) => {
        const known = new Set(previous.map((item) => item.id));
        return [...previous, ...page.filter((item) => !known.has(item.id))];
      });
      setTemplateOffset(nextOffset);
      setMoreTemplates(page.length === 100);
    } catch (reason) {
      setError(errorMessage(reason, expire));
    } finally {
      setBusy(false);
    }
  }

  async function loadMoreAuthors(): Promise<void> {
    const nextOffset = authorOffset + 100;
    setBusy(true);
    setError("");
    try {
      const page = await authorApi.list(nextOffset);
      setAuthors((previous) => {
        const known = new Set(previous.map((item) => item.id));
        return [...previous, ...page.filter((item) => !known.has(item.id))];
      });
      setAuthorOffset(nextOffset);
      setMoreAuthors(page.length === 100);
    } catch (reason) {
      setError(errorMessage(reason, expire));
    } finally {
      setBusy(false);
    }
  }

  async function chooseTemplate(id: string): Promise<void> {
    setTemplateId(id);
    setVersion(null);
    setVersions([]);
    setValues({});
    setProvenance({});
    setPlan(null);
    setDirty(false);
    const chosen = templates.find((item) => item.id === id);
    if (!chosen?.active_version_id) return;
    try {
      const availableVersions = await templateApi.versions(id);
      setVersions(availableVersions);
      const loaded =
        availableVersions.find(
          (item) => item.id === chosen.active_version_id,
        ) ?? (await templateApi.version(id, chosen.active_version_id));
      setVersion(loaded);
    } catch (reason) {
      setError(errorMessage(reason, expire));
    }
  }

  function editValue(
    path: string,
    value: unknown,
    topLevel?: { name: string; index: number; field: string },
  ) {
    if (value === undefined && protectedSource(plan?.provenance[path])) {
      setError(
        "A saved human-reviewed value cannot be cleared. Enter a replacement instead.",
      );
      return;
    }
    if (plan) setDirty(true);
    setValues((previous) => {
      if (topLevel) {
        const rows = Array.isArray(previous[topLevel.name])
          ? [...(previous[topLevel.name] as Record<string, unknown>[])]
          : [];
        const row = { ...(rows[topLevel.index] ?? {}) };
        if (value === undefined) delete row[topLevel.field];
        else row[topLevel.field] = value;
        rows[topLevel.index] = row;
        return { ...previous, [topLevel.name]: rows };
      }
      const next = { ...previous };
      if (value === undefined) delete next[path];
      else next[path] = value;
      return next;
    });
    setProvenance((previous) => {
      const next = { ...previous };
      if (value === undefined) delete next[path];
      else next[path] = { kind: plan ? "human_edited" : "supplied" };
      return next;
    });
  }

  function removalWouldDiscardReview(
    name: string,
    removedIndex: number,
  ): boolean {
    return Object.entries(plan?.provenance ?? {}).some(([path, source]) => {
      const index = repeatPathIndex(path, name);
      return index !== null && index >= removedIndex && protectedSource(source);
    });
  }

  function addRow(name: string): void {
    if (plan) setDirty(true);
    setValues((previous) => ({
      ...previous,
      [name]: [
        ...(Array.isArray(previous[name]) ? (previous[name] as unknown[]) : []),
        {},
      ],
    }));
  }

  function removeRow(name: string, index: number): void {
    if (removalWouldDiscardReview(name, index)) {
      setError(
        "A saved human-reviewed row cannot be removed or shifted. Edit its values instead.",
      );
      return;
    }
    if (plan) setDirty(true);
    setValues((previous) => ({
      ...previous,
      [name]: (Array.isArray(previous[name])
        ? (previous[name] as unknown[])
        : []
      ).filter((_, at) => at !== index),
    }));
    setProvenance((previous) => shiftedProvenance(previous, name, index));
  }

  async function operation(action: () => Promise<void>): Promise<void> {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (reason) {
      setError(errorMessage(reason, expire));
      if (
        plan &&
        reason instanceof ApiError &&
        [409, 412].includes(reason.status)
      ) {
        try {
          const current = await templateApi.getPlan(draft.id, plan.id);
          setPlan(current);
          setValues(current.values);
          setProvenance(current.provenance);
        } catch {
          setPlan(null);
        }
      }
    } finally {
      setBusy(false);
    }
  }

  async function createPlan(): Promise<void> {
    if (!sourceRevisionId || !version || !templateId) return;
    await operation(async () => {
      const created = await templateApi.createPlan(
        draft.id,
        draft.etag,
        crypto.randomUUID(),
        {
          source_revision_id: sourceRevisionId,
          template_id: templateId,
          template_version_id: version.id,
          author_ids: authorIds,
          values,
        },
      );
      setPlan(created);
      setPlans((previous) => [created, ...previous]);
      setValues(created.values);
      setProvenance(created.provenance);
      setDirty(false);
      try {
        sessionStorage.setItem(planKey(ownerId, draft.id), created.id);
      } catch {
        /* The plan itself remains durable on the server. */
      }
      setNotice(
        "Fill plan saved. Review questions and values before approval.",
      );
    });
  }

  async function saveAnswers(): Promise<void> {
    if (!plan) return;
    await operation(async () => {
      const updated = await templateApi.updatePlan(
        draft.id,
        plan,
        values,
        provenance,
        crypto.randomUUID(),
      );
      setPlan(updated);
      setPlans((previous) =>
        previous.map((item) => (item.id === updated.id ? updated : item)),
      );
      setValues(updated.values);
      setProvenance(updated.provenance);
      setDirty(false);
      setNotice("Answers and manual edits saved for review.");
    });
  }

  async function approve(): Promise<void> {
    if (!plan) return;
    await operation(async () => {
      const approved = await templateApi.approve(
        draft.id,
        plan,
        crypto.randomUUID(),
      );
      setPlan(approved);
      setPlans((previous) =>
        previous.map((item) => (item.id === approved.id ? approved : item)),
      );
      setNotice("Approved values are frozen for this plan.");
    });
  }

  async function publish(): Promise<void> {
    if (!plan) return;
    await operation(async () => {
      const revision = await templateApi.publish(
        draft.id,
        plan,
        crypto.randomUUID(),
      );
      setPlan(await templateApi.getPlan(draft.id, plan.id));
      setNotice(`Filled DOCX published as revision ${revision.number}.`);
      onPublished(revision.id);
    });
  }

  async function regenerate(): Promise<void> {
    if (!regenerateId) return;
    await operation(async () => {
      const revision = await templateApi.regenerate(
        draft.id,
        regenerateId,
        draft.etag,
        crypto.randomUUID(),
      );
      setNotice(`Regenerated DOCX published as revision ${revision.number}.`);
      onPublished(revision.id);
    });
  }

  const selectedTemplate = templates.find((item) => item.id === templateId);
  const schema: FillSchema | null = version?.schema ?? null;

  return (
    <section
      aria-label="Typed document filling"
      className="space-y-4 rounded-control border border-muted p-4"
    >
      <h2 className="text-xl font-semibold">Fill a typed DOCX template</h2>
      <p>
        Choose an authorized template and author. Review named values and answer
        missing or ambiguous facts before publishing an immutable revision.
      </p>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <p role="status">{notice}</p>}
      {plans.length > 0 && (
        <label className="grid gap-1">
          Saved fill plans
          <select
            value={plan?.id ?? ""}
            onChange={(event) => {
              const chosen = plans.find(
                (item) => item.id === event.target.value,
              );
              if (chosen) void choosePlan(chosen);
              else {
                setPlan(null);
                setValues({});
                setProvenance({});
                setDirty(false);
              }
            }}
          >
            <option value="">New fill plan</option>
            {plans.map((item) => (
              <option key={item.id} value={item.id}>
                {item.state} · {item.id}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="grid gap-1">
        Typed filling template
        <select
          disabled={busy}
          value={templateId}
          onChange={(event) => void chooseTemplate(event.target.value)}
        >
          <option value="">Choose a template</option>
          {templates
            .filter((item) => item.active_version_id)
            .map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
        </select>
      </label>
      {moreTemplates && (
        <button
          disabled={busy}
          onClick={() => void loadMoreTemplates()}
          type="button"
        >
          Load more typed templates
        </button>
      )}
      {selectedTemplate && version && (
        <p>
          Exact template version: {version.number} · {version.id}
        </p>
      )}
      {selectedTemplate && versions.length > 0 && (
        <label className="grid gap-1">
          Typed template version
          <select
            disabled={busy || Boolean(plan)}
            value={version?.id ?? ""}
            onChange={(event) => {
              const chosen = versions.find(
                (item) => item.id === event.target.value,
              );
              if (chosen) {
                setVersion(chosen);
                setValues({});
                setProvenance({});
              }
            }}
          >
            {versions.map((item) => (
              <option key={item.id} value={item.id}>
                Version {item.number} · {item.id}
              </option>
            ))}
          </select>
        </label>
      )}
      <fieldset className="space-y-2">
        <legend>Authorized authors to include</legend>
        {authors.length === 0 ? (
          <p>No author selected. You can enter values manually.</p>
        ) : (
          authors.map((author) => (
            <label className="block" key={author.id}>
              <input
                checked={authorIds.includes(author.id)}
                disabled={busy || Boolean(plan)}
                onChange={(event) =>
                  setAuthorIds((previous) =>
                    event.target.checked
                      ? [...previous, author.id]
                      : previous.filter((id) => id !== author.id),
                  )
                }
                type="checkbox"
              />{" "}
              {author.name}
            </label>
          ))
        )}
      </fieldset>
      {moreAuthors && (
        <button
          disabled={busy}
          onClick={() => void loadMoreAuthors()}
          type="button"
        >
          Load more fill authors
        </button>
      )}
      {schema && (
        <div className="space-y-3">
          <h3>Named values</h3>
          {schema.fields.map((field) => (
            <FieldValue
              disabled={busy || (plan !== null && plan.state !== "pending")}
              field={field}
              key={field.name}
              label={field.name}
              value={values[field.name]}
              onChange={(value) => editValue(field.name, value)}
            />
          ))}
          {schema.repeats.map((repeat) => {
            const rows = Array.isArray(values[repeat.name])
              ? (values[repeat.name] as Record<string, unknown>[])
              : [];
            return (
              <fieldset
                className="space-y-3 border border-muted p-3"
                key={repeat.name}
              >
                <legend>
                  {repeat.name} ({repeat.min_items}–{repeat.max_items} rows)
                </legend>
                {rows.some((_, index) =>
                  removalWouldDiscardReview(repeat.name, index),
                ) && (
                  <p>
                    Saved human-reviewed rows cannot be removed or shifted. Edit
                    their values instead.
                  </p>
                )}
                {rows.map((row, index) => (
                  <div
                    className="space-y-2 border-l-2 border-muted pl-3"
                    key={index}
                  >
                    <h4>Row {index + 1}</h4>
                    {repeat.fields.map((field) => {
                      const path = `${repeat.name}[${index}].${field.name}`;
                      return (
                        <FieldValue
                          disabled={
                            busy || (plan !== null && plan.state !== "pending")
                          }
                          field={field}
                          key={path}
                          label={path}
                          value={row[field.name]}
                          onChange={(value) =>
                            editValue(path, value, {
                              name: repeat.name,
                              index,
                              field: field.name,
                            })
                          }
                        />
                      );
                    })}
                    <button
                      disabled={
                        busy ||
                        (plan !== null && plan.state !== "pending") ||
                        rows.length <= repeat.min_items ||
                        removalWouldDiscardReview(repeat.name, index)
                      }
                      onClick={() => removeRow(repeat.name, index)}
                      type="button"
                    >
                      Remove row {index + 1}
                    </button>
                  </div>
                ))}
                <button
                  disabled={
                    busy ||
                    (plan !== null && plan.state !== "pending") ||
                    rows.length >= repeat.max_items
                  }
                  onClick={() => addRow(repeat.name)}
                  type="button"
                >
                  Add {repeat.name} row
                </button>
              </fieldset>
            );
          })}
        </div>
      )}
      {!plan && (
        <button
          disabled={busy || !sourceRevisionId || !version}
          onClick={() => void createPlan()}
          type="button"
        >
          Save fill plan
        </button>
      )}
      {!sourceRevisionId && <p>Capture a source revision before filling.</p>}
      {plan && (
        <div className="space-y-3">
          <p>
            Fill plan {plan.id} · {plan.state} · version {plan.version}
          </p>
          <h3>Questions and provenance</h3>
          {plan.questions.length === 0 ? (
            <p>No missing or ambiguous values remain.</p>
          ) : (
            <ul className="list-disc pl-5">
              {plan.questions.map((question) => (
                <li key={question.path}>
                  {question.path}: {question.text} ({question.reason})
                </li>
              ))}
            </ul>
          )}
          <ul className="list-disc pl-5">
            {Object.entries(provenance).map(([path, source]) => (
              <li key={path}>
                {path}: {source.kind}
                {source.source_reference ? ` · ${source.source_reference}` : ""}
              </li>
            ))}
          </ul>
          {plan.state === "pending" && (
            <div className="flex flex-wrap gap-3">
              <button
                disabled={busy}
                onClick={() => void saveAnswers()}
                type="button"
              >
                Save answers and edits
              </button>
              <button
                disabled={
                  busy || dirty || !version || plan.questions.length > 0
                }
                onClick={() => void approve()}
                type="button"
              >
                Approve reviewed values
              </button>
            </div>
          )}
          {plan.state === "approved" && (
            <button
              disabled={busy || !version}
              onClick={() => void publish()}
              type="button"
            >
              Fill DOCX from approved values
            </button>
          )}
        </div>
      )}
      <div className="space-y-2 border-t border-muted pt-3">
        <h3>Deterministic regeneration</h3>
        <p>
          Recreate a filled revision from its frozen template version and
          approved values without a model call.
        </p>
        <label className="grid gap-1">
          Filled revision
          <select
            value={regenerateId}
            onChange={(event) => setRegenerateId(event.target.value)}
          >
            <option value="">Choose a filled revision</option>
            {revisions
              .filter((item) => item.operation.includes("fill"))
              .map((item) => (
                <option key={item.id} value={item.id}>
                  Revision {item.number} · {item.id}
                </option>
              ))}
          </select>
        </label>
        <button
          disabled={busy || !regenerateId}
          onClick={() => void regenerate()}
          type="button"
        >
          Regenerate DOCX
        </button>
      </div>
    </section>
  );
}
