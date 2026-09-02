"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  TemplateAdministrationContextResponse,
  TemplateResponse,
  TemplateVersionResponse,
} from "../api/generated/types.gen";
import type { EffectiveUser } from "../auth/controller";
import {
  Alert,
  Dialog,
  ItemList,
  LoadingStatus,
  TextField,
} from "../../components/primitives";
import { AdministrationApi } from "./api";
import { ApiError } from "../api/transport";
import {
  administrationError,
  readTemplateDownload,
  RequestFence,
  saveTemplateDownload,
} from "./operations";

interface ManagedTemplate {
  etag: string;
  template: TemplateResponse;
  versions: TemplateVersionResponse[];
}

type Confirmation = "archive" | "delete" | undefined;
const defaultAdministrationApi = new AdministrationApi();

export function TemplatesWorkspace({
  api = defaultAdministrationApi,
  expire,
  user,
}: {
  api?: AdministrationApi;
  expire: () => void;
  user: EffectiveUser;
}) {
  const fence = useRef(new RequestFence());
  const [templates, setTemplates] = useState<TemplateResponse[]>([]);
  const [context, setContext] =
    useState<TemplateAdministrationContextResponse>();
  const [managed, setManaged] = useState<ManagedTemplate>();
  const [query, setQuery] = useState("");
  const [mine, setMine] = useState(false);
  const [status, setStatus] = useState<"all" | "active" | "archived">("all");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [confirmation, setConfirmation] = useState<Confirmation>();

  const load = useCallback(async () => {
    const request = fence.current.startRead();
    setLoading(true);
    try {
      const [library, selection] = await Promise.all([
        api.allTemplates({}, request.controller.signal),
        api.templateContext(request.controller.signal),
      ]);
      if (!fence.current.current(request.generation)) return;
      setTemplates(library);
      setContext(selection);
      setError(undefined);
    } catch (reason) {
      if (!fence.current.current(request.generation)) return;
      setError(
        administrationError(
          reason,
          expire,
          "Templates could not be loaded. Try again.",
        ),
      );
    } finally {
      if (fence.current.current(request.generation)) setLoading(false);
    }
  }, [api, expire]);

  useEffect(() => {
    const activeFence = fence.current;
    let disposed = false;
    void Promise.resolve().then(() => {
      if (!disposed) void load();
    });
    return () => {
      disposed = true;
      activeFence.dispose();
    };
  }, [load]);

  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return templates.filter((template) => {
      if (mine && template.owner_id !== user.id) return false;
      if (status !== "all" && template.status !== status) return false;
      if (!normalized) return true;
      return [
        template.name,
        template.description,
        template.owner_username,
      ].some((value) => value.toLocaleLowerCase().includes(normalized));
    });
  }, [mine, query, status, templates, user.id]);

  async function fetchManaged(
    templateId: string,
    signal: AbortSignal,
  ): Promise<ManagedTemplate | undefined> {
    const [snapshot, versions] = await Promise.all([
      api.template(templateId, signal),
      api.versions(templateId, signal),
    ]);
    if (!snapshot.etag) return undefined;
    return { etag: snapshot.etag, template: snapshot.data, versions };
  }

  async function manage(template: TemplateResponse): Promise<void> {
    const request = fence.current.startRead();
    setLoading(true);
    setError(undefined);
    try {
      const snapshot = await fetchManaged(
        template.id,
        request.controller.signal,
      );
      if (!fence.current.current(request.generation)) return;
      if (!snapshot) {
        setManaged(undefined);
        setError(
          "The server did not provide the template revision. Reload and try again.",
        );
        return;
      }
      setManaged(snapshot);
    } catch (reason) {
      if (!fence.current.current(request.generation)) return;
      setError(
        administrationError(
          reason,
          expire,
          "The template could not be loaded. Try again.",
        ),
      );
    } finally {
      if (fence.current.current(request.generation)) setLoading(false);
    }
  }

  async function mutation(
    action: (signal: AbortSignal) => Promise<unknown>,
    success: string,
  ): Promise<boolean> {
    const request = fence.current.startMutation();
    if (!request) return false;
    setPending(true);
    setError(undefined);
    setNotice(undefined);
    try {
      await action(request.controller.signal);
      if (!fence.current.finishMutation(request.generation)) return false;
      setPending(false);
      setManaged(undefined);
      setConfirmation(undefined);
      setNotice(success);
      await load();
      return true;
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 412 && managed) {
        try {
          const latest = await fetchManaged(
            managed.template.id,
            request.controller.signal,
          );
          if (!fence.current.finishMutation(request.generation)) return false;
          setPending(false);
          setConfirmation(undefined);
          if (!latest) {
            setManaged(undefined);
            setError(
              "The server did not provide the template revision. Reload and try again.",
            );
            return false;
          }
          setManaged(latest);
          setError(
            "This item changed on the server. Review the latest version and try again.",
          );
        } catch (refreshReason) {
          if (!fence.current.finishMutation(request.generation)) return false;
          setPending(false);
          setManaged(undefined);
          setError(
            administrationError(
              refreshReason,
              expire,
              "The latest template could not be loaded. Reload and try again.",
            ),
          );
        }
        return false;
      }
      if (!fence.current.finishMutation(request.generation)) return false;
      setPending(false);
      setError(
        administrationError(
          reason,
          expire,
          "The template change could not be completed. Try again.",
        ),
      );
      return false;
    }
  }

  async function downloadTemplate(
    templateId: string,
    versionId?: string,
  ): Promise<void> {
    const request = fence.current.startMutation();
    if (!request) return;
    setPending(true);
    setError(undefined);
    setNotice(undefined);
    try {
      const response = await api.templateContent(
        templateId,
        versionId,
        request.controller.signal,
      );
      const download = await readTemplateDownload(response);
      if (!fence.current.finishMutation(request.generation)) return;
      setPending(false);
      saveTemplateDownload(download);
    } catch (reason) {
      if (!fence.current.finishMutation(request.generation)) return;
      setPending(false);
      setError(
        administrationError(
          reason,
          expire,
          "The template could not be downloaded. Try again.",
        ),
      );
    }
  }

  function validateFile(file: File | undefined): string | undefined {
    if (!file || file.size === 0) return "Choose one non-empty DOCX file.";
    if (!file.name.toLocaleLowerCase().endsWith(".docx"))
      return "Choose a file with the .docx extension.";
    if (context && file.size > context.template_max_archive_bytes)
      return `The selected file exceeds the configured ${context.template_max_archive_bytes} byte limit.`;
    return undefined;
  }

  async function createTemplate(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const content = formElement.elements.namedItem("content");
    const file =
      content instanceof HTMLInputElement ? content.files?.[0] : undefined;
    const validation = validateFile(file);
    if (validation || !file) {
      setError(validation);
      return;
    }
    const created = await mutation(
      (signal) =>
        api.create(
          {
            content: file,
            description: String(form.get("description") ?? ""),
            expectedFonts: String(form.get("expected-fonts") ?? ""),
            name: String(form.get("name") ?? ""),
          },
          signal,
        ),
      "Template created.",
    );
    if (created) formElement.reset();
  }

  const canManage =
    managed && (managed.template.owner_id === user.id || user.role === "admin");

  return (
    <section className="space-y-6" aria-labelledby="templates-title">
      <div>
        <h1 className="text-3xl font-semibold" id="templates-title">
          Templates
        </h1>
        <p>Manage visible Word templates. FastAPI validates every operation.</p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <Alert>{notice}</Alert>}
      <section className="space-y-4" aria-labelledby="create-template-title">
        <h2 className="text-xl font-semibold" id="create-template-title">
          Create a template
        </h2>
        <form
          className="grid gap-4"
          onSubmit={(event) => void createTemplate(event)}
        >
          <TextField label="Name" name="name" required />
          <label className="grid gap-2 font-medium">
            Description
            <textarea
              className="rounded-control border border-muted bg-surface px-3 py-2 font-normal"
              name="description"
              required
            />
          </label>
          <TextField
            label="Expected fonts (comma separated)"
            name="expected-fonts"
          />
          <label className="grid gap-2 font-medium">
            DOCX file
            <input
              accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              name="content"
              required
              type="file"
            />
          </label>
          {context && (
            <p className="text-sm text-muted">
              Maximum upload: {context.template_max_archive_bytes} bytes.
            </p>
          )}
          <button disabled={pending} type="submit">
            {pending ? "Saving…" : "Create template"}
          </button>
        </form>
      </section>
      <section className="space-y-4" aria-labelledby="library-title">
        <h2 className="text-xl font-semibold" id="library-title">
          Template library
        </h2>
        <TextField
          label="Search name, description, or owner"
          name="template-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <label>
          <input
            checked={mine}
            onChange={(event) => setMine(event.target.checked)}
            type="checkbox"
          />{" "}
          My templates
        </label>
        <label className="grid gap-2 font-medium">
          Status
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as typeof status)}
          >
            <option value="all">All</option>
            <option value="active">Active</option>
            <option value="archived">Archived</option>
          </select>
        </label>
        <LoadingStatus loading={loading}>
          {visible.length === 0 ? (
            <p>No templates match these filters.</p>
          ) : (
            <ItemList label="Visible templates">
              {visible.map((template) => (
                <li className="space-y-2 py-4" key={template.id}>
                  <h3 className="font-semibold">{template.name}</h3>
                  <p>{template.description}</p>
                  <p>
                    Owner: {template.owner_username} · Status: {template.status}
                  </p>
                  <div className="flex flex-wrap gap-3">
                    {template.status === "active" && (
                      <button
                        disabled={pending}
                        onClick={() => void downloadTemplate(template.id)}
                        type="button"
                      >
                        Download current DOCX
                      </button>
                    )}
                    {template.status === "active" && (
                      <button
                        disabled={pending}
                        onClick={() =>
                          void mutation(
                            (signal) => api.setPreferred(template.id, signal),
                            "Preferred template updated.",
                          )
                        }
                        type="button"
                      >
                        {context?.preferred_template_id === template.id
                          ? "Preferred"
                          : "Make preferred"}
                      </button>
                    )}
                    {(template.owner_id === user.id ||
                      user.role === "admin") && (
                      <button
                        disabled={pending}
                        onClick={() => void manage(template)}
                        type="button"
                      >
                        Manage
                      </button>
                    )}
                    {user.role === "admin" && template.status === "active" && (
                      <button
                        disabled={pending}
                        onClick={() =>
                          void mutation(
                            (signal) => api.setFallback(template.id, signal),
                            "System fallback updated.",
                          )
                        }
                        type="button"
                      >
                        {context?.system_fallback_template_id === template.id
                          ? "System fallback"
                          : "Set system fallback"}
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ItemList>
          )}
        </LoadingStatus>
        {context?.preferred_template_id && (
          <button
            disabled={pending}
            onClick={() =>
              void mutation(
                (signal) => api.clearPreferred(signal),
                "Preferred template cleared.",
              )
            }
            type="button"
          >
            Clear preferred template
          </button>
        )}
      </section>
      {managed && canManage && (
        <section className="space-y-5" aria-labelledby="manage-template-title">
          <h2 className="text-xl font-semibold" id="manage-template-title">
            Manage {managed.template.name}
          </h2>
          <MetadataForm
            disabled={pending}
            key={`metadata-${managed.etag}`}
            managed={managed}
            submit={(name, description) =>
              mutation(
                (signal) =>
                  api.updateMetadata(
                    managed.template.id,
                    managed.etag,
                    name,
                    description,
                    signal,
                  ),
                "Template details updated.",
              )
            }
          />
          {managed.template.status === "active" && (
            <ReplacementForm
              disabled={pending}
              invalid={setError}
              key={`replacement-${managed.etag}-${managed.template.current_version_id}`}
              managed={managed}
              validate={validateFile}
              submit={(file, fonts) =>
                mutation(
                  (signal) =>
                    api.replace(
                      managed.template.id,
                      managed.etag,
                      file,
                      fonts,
                      signal,
                    ),
                  "Template content replaced.",
                )
              }
            />
          )}
          <h3 className="font-semibold">Version history</h3>
          <ItemList label="Template versions">
            {managed.versions.map((version) => (
              <li className="flex flex-wrap gap-3 py-3" key={version.id}>
                <span>
                  Version {version.number} · {version.size} bytes
                </span>
                <button
                  disabled={pending}
                  onClick={() =>
                    void downloadTemplate(managed.template.id, version.id)
                  }
                  type="button"
                >
                  Download version {version.number}
                </button>
                {managed.template.status === "active" &&
                  version.id !== managed.template.current_version_id && (
                    <button
                      disabled={pending}
                      onClick={() =>
                        void mutation(
                          (signal) =>
                            api.restore(
                              managed.template.id,
                              version.id,
                              managed.etag,
                              signal,
                            ),
                          `Version ${version.number} restored as a new version.`,
                        )
                      }
                      type="button"
                    >
                      Restore version {version.number}
                    </button>
                  )}
              </li>
            ))}
          </ItemList>
          <div className="flex flex-wrap gap-3">
            {managed.template.status === "active" ? (
              <button
                disabled={pending}
                onClick={() => setConfirmation("archive")}
                type="button"
              >
                Archive template
              </button>
            ) : (
              <button
                disabled={pending}
                onClick={() => setConfirmation("delete")}
                type="button"
              >
                Delete template permanently
              </button>
            )}
            <button onClick={() => setManaged(undefined)} type="button">
              Close management
            </button>
          </div>
        </section>
      )}
      <Dialog
        open={confirmation !== undefined}
        onClose={() => setConfirmation(undefined)}
        title={
          confirmation === "delete"
            ? "Delete template permanently?"
            : "Archive template?"
        }
      >
        <p>
          {confirmation === "delete"
            ? "Deletion succeeds only when server reference and retention guards allow it."
            : "Archived templates cannot be selected for new conversions."}
        </p>
        <div className="mt-4 flex gap-3">
          <button
            disabled={pending}
            onClick={() => {
              if (!managed || !confirmation) return;
              void mutation(
                (signal) =>
                  confirmation === "delete"
                    ? api.delete(managed.template.id, managed.etag, signal)
                    : api.archive(managed.template.id, managed.etag, signal),
                confirmation === "delete"
                  ? "Template deleted."
                  : "Template archived.",
              );
            }}
            type="button"
          >
            Confirm
          </button>
          <button
            disabled={pending}
            onClick={() => setConfirmation(undefined)}
            type="button"
          >
            Cancel
          </button>
        </div>
      </Dialog>
    </section>
  );
}

function MetadataForm({
  disabled,
  managed,
  submit,
}: {
  disabled: boolean;
  managed: ManagedTemplate;
  submit: (name: string, description: string) => Promise<unknown>;
}) {
  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        void submit(
          String(form.get("metadata-name") ?? ""),
          String(form.get("metadata-description") ?? ""),
        );
      }}
    >
      <TextField
        defaultValue={managed.template.name}
        label="Template name"
        name="metadata-name"
        required
      />
      <label className="grid gap-2 font-medium">
        Template description
        <textarea
          defaultValue={managed.template.description}
          name="metadata-description"
          required
        />
      </label>
      <button disabled={disabled} type="submit">
        Save details
      </button>
    </form>
  );
}

function ReplacementForm({
  disabled,
  invalid,
  managed,
  submit,
  validate,
}: {
  disabled: boolean;
  invalid: (message: string) => void;
  managed: ManagedTemplate;
  submit: (file: File, fonts: string) => Promise<unknown>;
  validate: (file: File | undefined) => string | undefined;
}) {
  const current = managed.versions.find(
    (version) => version.id === managed.template.current_version_id,
  );
  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        const formElement = event.currentTarget;
        const form = new FormData(formElement);
        const entry = formElement.elements.namedItem("replacement");
        const file =
          entry instanceof HTMLInputElement ? entry.files?.[0] : undefined;
        const message = validate(file);
        if (!file || message) {
          invalid(message ?? "Choose one non-empty DOCX file.");
          return;
        }
        void submit(file, String(form.get("replacement-fonts") ?? ""));
      }}
    >
      <TextField
        defaultValue={current?.declared_fonts.join(", ") ?? ""}
        label="Replacement expected fonts (comma separated)"
        name="replacement-fonts"
      />
      <label className="grid gap-2 font-medium">
        Replacement DOCX
        <input
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          name="replacement"
          required
          type="file"
        />
      </label>
      <button disabled={disabled} type="submit">
        Replace content
      </button>
    </form>
  );
}
