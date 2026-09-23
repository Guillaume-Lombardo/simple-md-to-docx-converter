"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Alert, LoadingStatus, TextField } from "../../components/primitives";
import { ApiError } from "../api/transport";
import type { EffectiveUser } from "../auth/controller";
import {
  ComposerConnectionsApi,
  type ComposerCapabilities,
  type ComposerConnection,
  type ConnectionInput,
  type CredentialInput,
  type PersonalPermission,
} from "./api";

const defaultApi = new ComposerConnectionsApi();
const pageSize = 50;

class PartialConnectionError extends Error {
  constructor(readonly connectionId: string) {
    super(
      `Connection ${connectionId} was created disabled, but its credentials were not saved. Open it below to rotate credentials and continue setup.`,
    );
  }
}

function values(value: FormDataEntryValue | null): string[] {
  return String(value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function safeError(reason: unknown, expire: () => void): string {
  if (reason instanceof PartialConnectionError) return reason.message;
  if (reason instanceof ApiError) {
    if (reason.status === 401) expire();
    return reason.message;
  }
  return "The connection operation could not be completed. Try again.";
}

export function ComposerConnectionsWorkspace({
  api = defaultApi,
  expire,
  user,
}: {
  api?: ComposerConnectionsApi;
  expire: () => void;
  user: EffectiveUser;
}) {
  const request = useRef<AbortController | undefined>(undefined);
  const [capabilities, setCapabilities] = useState<ComposerCapabilities>();
  const [connections, setConnections] = useState<ComposerConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [catalogs, setCatalogs] = useState<Record<string, string[]>>({});
  const [permissions, setPermissions] = useState<PersonalPermission[]>([]);
  const [connectionOffset, setConnectionOffset] = useState(0);
  const [permissionOffset, setPermissionOffset] = useState(0);

  const load = useCallback(async () => {
    request.current?.abort();
    const active = new AbortController();
    request.current = active;
    setLoading(true);
    try {
      const [nextCapabilities, nextConnections, nextPermissions] =
        await Promise.all([
          api.capabilities(active.signal),
          api.connections(connectionOffset, pageSize, active.signal),
          user.role === "admin"
            ? api.personalPermissions(permissionOffset, pageSize, active.signal)
            : Promise.resolve([]),
        ]);
      if (active.signal.aborted) return;
      setCapabilities(nextCapabilities);
      setConnections(nextConnections);
      setPermissions(nextPermissions);
      setError(undefined);
    } catch (reason) {
      if (!active.signal.aborted) setError(safeError(reason, expire));
    } finally {
      if (!active.signal.aborted) setLoading(false);
    }
  }, [api, connectionOffset, expire, permissionOffset, user.role]);

  useEffect(() => {
    let disposed = false;
    void Promise.resolve().then(() => {
      if (!disposed) void load();
    });
    return () => {
      disposed = true;
      request.current?.abort();
    };
  }, [load]);

  async function mutate(
    operation: (signal: AbortSignal) => Promise<void>,
    success: string,
  ): Promise<void> {
    if (pending) return;
    const active = new AbortController();
    setPending(true);
    setError(undefined);
    setNotice(undefined);
    try {
      await operation(active.signal);
      setNotice(success);
      await load();
    } catch (reason) {
      setError(safeError(reason, expire));
    } finally {
      setPending(false);
    }
  }

  async function create(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const scope = data.get("scope") === "instance" ? "instance" : "personal";
    const input: ConnectionInput = {
      allowed_user_ids:
        scope === "instance" ? values(data.get("allowed_users")) : [],
      enabled: false,
      endpoint: String(data.get("endpoint") ?? "").trim(),
      identity_mode:
        scope === "personal" || data.get("identity_mode") === "individual"
          ? "individual"
          : "shared",
      name: String(data.get("name") ?? "").trim(),
      permitted_models: values(data.get("permitted_models")),
      scope,
      selected_model: null,
    };
    let succeeded = false;
    try {
      await mutate(async (signal) => {
        const result = await api.create(input, signal);
        const secretInput = credentialsFrom(data);
        if (Object.keys(secretInput).length) {
          try {
            await api.credentials(
              result.data.id,
              result.data.etag,
              secretInput,
              signal,
            );
          } catch {
            await load().catch(() => undefined);
            throw new PartialConnectionError(result.data.id);
          }
        }
        succeeded = true;
      }, "Connection created disabled. Add credentials, then enable it before model discovery and testing. Submitted secrets were cleared from this page.");
    } finally {
      clearSecretFields(form);
      if (succeeded) form.reset();
    }
  }

  if (loading)
    return <LoadingStatus loading>Loading Composer connections…</LoadingStatus>;

  const canCreatePersonal = capabilities?.personal_connections_allowed === true;
  const canCreateInstance =
    user.role === "admin" &&
    capabilities?.instance_connections_manageable === true;

  return (
    <section className="space-y-6" aria-labelledby="connections-title">
      <div>
        <h1 id="connections-title">Composer connections</h1>
        <p>
          Configure an approved OpenAI-compatible endpoint. Credentials and
          private keys are write-only and disappear from this page after
          submission.
        </p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <Alert>{notice}</Alert>}
      {capabilities && (
        <Alert>
          Composer status: <strong>{capabilities.status}</strong>
          {capabilities.status_message
            ? ` — ${capabilities.status_message}`
            : ""}
          {capabilities.maximum_upload_bytes === null
            ? " — Source uploads disabled"
            : ` — Source upload limit ${capabilities.maximum_upload_bytes} bytes`}
        </Alert>
      )}

      {user.role === "admin" && (
        <PersonalPermissions
          api={api}
          mutate={mutate}
          pending={pending}
          permissions={permissions}
          offset={permissionOffset}
          onOffset={setPermissionOffset}
        />
      )}

      {(canCreatePersonal || canCreateInstance) && (
        <ConnectionForm
          canCreateInstance={canCreateInstance}
          canCreatePersonal={canCreatePersonal}
          onSubmit={(event) => void create(event)}
          pending={pending}
        />
      )}

      {!connections.length ? (
        <p>No authorized connections are visible to this account.</p>
      ) : (
        <div className="space-y-4">
          {connections.map((connection) => (
            <ConnectionCard
              api={api}
              catalogs={catalogs}
              connection={connection}
              key={connection.id}
              mutate={mutate}
              pending={pending}
              setCatalogs={setCatalogs}
            />
          ))}
          <Pagination
            label="Connection pages"
            offset={connectionOffset}
            onOffset={setConnectionOffset}
            pageLength={connections.length}
          />
        </div>
      )}
    </section>
  );
}

function PersonalPermissions({
  api,
  mutate,
  pending,
  permissions,
  offset,
  onOffset,
}: {
  api: ComposerConnectionsApi;
  mutate: (
    operation: (signal: AbortSignal) => Promise<void>,
    success: string,
  ) => Promise<void>;
  pending: boolean;
  permissions: PersonalPermission[];
  offset: number;
  onOffset: (offset: number) => void;
}) {
  return (
    <section
      className="space-y-3 rounded-control border border-muted p-4"
      aria-labelledby="personal-permissions-title"
    >
      <h2 id="personal-permissions-title">Personal connection permissions</h2>
      <p>
        Grant or revoke the ability to create a private personal connection.
      </p>
      {!permissions.length ? (
        <p>No users are available.</p>
      ) : (
        <ul className="space-y-2">
          {permissions.map((permission) => (
            <li
              className="flex items-center justify-between gap-3"
              key={permission.user_id}
            >
              <span>
                {permission.username} ({permission.user_id}) —{" "}
                {permission.allowed ? "Allowed" : "Denied"}
              </span>
              <button
                disabled={pending}
                onClick={() =>
                  void mutate(
                    async (signal) => {
                      await api.updatePersonalPermission(
                        permission.user_id,
                        !permission.allowed,
                        permission.etag,
                        signal,
                      );
                    },
                    permission.allowed
                      ? "Personal connection permission revoked."
                      : "Personal connection permission granted.",
                  )
                }
                type="button"
              >
                {permission.allowed ? "Revoke" : "Grant"}
              </button>
            </li>
          ))}
        </ul>
      )}
      <Pagination
        label="Personal permission pages"
        offset={offset}
        onOffset={onOffset}
        pageLength={permissions.length}
      />
    </section>
  );
}

function Pagination({
  label,
  offset,
  onOffset,
  pageLength,
}: {
  label: string;
  offset: number;
  onOffset: (offset: number) => void;
  pageLength: number;
}) {
  return (
    <nav aria-label={label} className="flex items-center gap-3">
      <button
        disabled={offset === 0}
        onClick={() => onOffset(Math.max(0, offset - pageSize))}
        type="button"
      >
        Previous
      </button>
      <span>Page {Math.floor(offset / pageSize) + 1}</span>
      <button
        disabled={pageLength < pageSize}
        onClick={() => onOffset(offset + pageSize)}
        type="button"
      >
        Next
      </button>
    </nav>
  );
}

function ConnectionForm({
  canCreateInstance,
  canCreatePersonal,
  onSubmit,
  pending,
}: {
  canCreateInstance: boolean;
  canCreatePersonal: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  pending: boolean;
}) {
  const [scope, setScope] = useState<"instance" | "personal">(
    canCreatePersonal ? "personal" : "instance",
  );
  return (
    <form
      className="grid gap-4 rounded-control border border-muted p-4"
      onSubmit={onSubmit}
    >
      <h2>Add a connection</h2>
      <label className="grid gap-2 font-medium">
        Scope
        <select
          className="rounded-control border border-muted bg-surface px-3 py-2"
          name="scope"
          onChange={(event) =>
            setScope(event.currentTarget.value as "instance" | "personal")
          }
          value={scope}
        >
          {canCreatePersonal && <option value="personal">Personal</option>}
          {canCreateInstance && <option value="instance">Instance</option>}
        </select>
      </label>
      <TextField label="Connection name" maxLength={128} name="name" required />
      <TextField
        label="Endpoint URL"
        maxLength={2048}
        name="endpoint"
        required
        type="url"
      />
      {scope === "instance" && (
        <label className="grid gap-2 font-medium">
          Identity mode
          <select
            className="rounded-control border border-muted bg-surface px-3 py-2"
            name="identity_mode"
          >
            <option value="shared">Shared server identity</option>
            <option value="individual">Individual user credentials</option>
          </select>
        </label>
      )}
      <TextField
        label="Permitted models (comma separated)"
        name="permitted_models"
      />
      {canCreateInstance && (
        <TextField
          label="Allowed user IDs (comma separated)"
          name="allowed_users"
        />
      )}
      <SecretFields />
      <button disabled={pending} type="submit">
        {pending ? "Saving…" : "Create connection"}
      </button>
    </form>
  );
}

function SecretFields() {
  return (
    <fieldset className="grid gap-3 rounded-control border border-muted p-3">
      <legend>Write-only credentials</legend>
      <TextField
        autoComplete="off"
        label="API key"
        name="api_key"
        type="password"
      />
      <label className="grid gap-2 font-medium">
        Client certificate (PEM)
        <textarea
          className="rounded-control border border-muted bg-surface p-2"
          name="client_certificate"
        />
      </label>
      <label className="grid gap-2 font-medium">
        Client private key (PEM)
        <textarea
          className="rounded-control border border-muted bg-surface p-2"
          name="client_private_key"
        />
      </label>
      <label className="grid gap-2 font-medium">
        Internal CA bundle (PEM)
        <textarea
          className="rounded-control border border-muted bg-surface p-2"
          name="internal_ca"
        />
      </label>
    </fieldset>
  );
}

function credentialsFrom(data: FormData): CredentialInput {
  const result: CredentialInput = {};
  for (const key of [
    "api_key",
    "client_certificate",
    "client_private_key",
    "internal_ca",
  ] as const) {
    const value = String(data.get(key) ?? "");
    if (value) result[key] = value;
  }
  return result;
}

function clearSecretFields(form: HTMLFormElement): void {
  for (const name of [
    "api_key",
    "client_certificate",
    "client_private_key",
    "internal_ca",
  ]) {
    const field = form.elements.namedItem(name);
    if (
      field instanceof HTMLInputElement ||
      field instanceof HTMLTextAreaElement
    )
      field.value = "";
  }
}

function ConnectionCard({
  api,
  catalogs,
  connection,
  mutate,
  pending,
  setCatalogs,
}: {
  api: ComposerConnectionsApi;
  catalogs: Record<string, string[]>;
  connection: ComposerConnection;
  mutate: (
    operation: (signal: AbortSignal) => Promise<void>,
    success: string,
  ) => Promise<void>;
  pending: boolean;
  setCatalogs: React.Dispatch<React.SetStateAction<Record<string, string[]>>>;
}) {
  const catalog = catalogs[connection.id];
  return (
    <article className="space-y-3 rounded-control border border-muted p-4">
      <div>
        <h2>{connection.name}</h2>
        <p>
          {connection.scope} · {connection.identity_mode} · {connection.status}
        </p>
        <p>{connection.endpoint}</p>
        {connection.status_message && (
          <p role="status">{connection.status_message}</p>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-2">
        <dt>API credential</dt>
        <dd>{connection.credential_present ? "Configured" : "Missing"}</dd>
        <dt>Client certificate</dt>
        <dd>
          {connection.client_certificate_present ? "Configured" : "Missing"}
        </dd>
        <dt>Internal CA</dt>
        <dd>
          {connection.internal_ca_present ? "Configured" : "Default trust"}
        </dd>
        <dt>Selected model</dt>
        <dd>{connection.selected_model ?? "None"}</dd>
      </dl>
      <div className="flex flex-wrap gap-2">
        <button
          disabled={pending || !connection.enabled}
          onClick={() =>
            void mutate(async (signal) => {
              await api.test(connection.id, signal);
            }, "Connection test completed.")
          }
          type="button"
        >
          Test connection
        </button>
        <button
          disabled={pending || !connection.enabled}
          onClick={() =>
            void mutate(async (signal) => {
              const models = await api.models(connection.id, signal);
              setCatalogs((current) => ({
                ...current,
                [connection.id]: models,
              }));
            }, "Model catalog refreshed.")
          }
          type="button"
        >
          Discover models
        </button>
        <button
          disabled={pending}
          onClick={() =>
            void mutate(
              async (signal) => {
                await api.update(
                  connection.id,
                  connection.etag,
                  { enabled: !connection.enabled },
                  signal,
                );
              },
              connection.enabled
                ? "Connection disabled."
                : "Connection enabled.",
            )
          }
          type="button"
        >
          {connection.enabled ? "Disable" : "Enable"}
        </button>
      </div>
      {catalog && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const selected = String(
              new FormData(event.currentTarget).get("model") ?? "",
            );
            void mutate(async (signal) => {
              await api.update(
                connection.id,
                connection.etag,
                { selected_model: selected },
                signal,
              );
            }, "Selected model updated.");
          }}
        >
          <label className="grid gap-2 font-medium">
            Authorized model
            <select
              className="rounded-control border border-muted bg-surface px-3 py-2"
              name="model"
              defaultValue={connection.selected_model ?? ""}
            >
              <option disabled value="">
                Select a model
              </option>
              {catalog.map((model) => (
                <option key={model}>{model}</option>
              ))}
            </select>
          </label>
          <button disabled={pending} type="submit">
            Save model
          </button>
        </form>
      )}
      <details>
        <summary>Rotate or revoke credentials</summary>
        <form
          className="mt-3 grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const input = credentialsFrom(new FormData(form));
            void (async () => {
              try {
                await mutate(async (signal) => {
                  await api.credentials(
                    connection.id,
                    connection.etag,
                    input,
                    signal,
                  );
                }, "Credentials rotated. Submitted secrets were cleared from this page.");
              } finally {
                clearSecretFields(form);
              }
            })();
          }}
        >
          <SecretFields />
          <button disabled={pending} type="submit">
            Rotate credentials
          </button>
          <button
            disabled={pending}
            onClick={(event) => {
              const form = event.currentTarget.form;
              void (async () => {
                try {
                  await mutate(async (signal) => {
                    await api.revokeCredentials(
                      connection.id,
                      connection.etag,
                      signal,
                    );
                  }, "Credentials revoked.");
                } finally {
                  if (form) clearSecretFields(form);
                }
              })();
            }}
            type="button"
          >
            Revoke credentials
          </button>
        </form>
      </details>
      <button
        disabled={pending}
        onClick={() =>
          void mutate(async (signal) => {
            await api.delete(connection.id, connection.etag, signal);
          }, "Connection revoked.")
        }
        type="button"
      >
        Revoke connection
      </button>
    </article>
  );
}
