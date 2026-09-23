import * as v from "valibot";
import { ApiTransport, type JsonResult } from "../api/transport";

export const connectionStatusSchema = v.picklist([
  "unconfigured",
  "disabled",
  "unauthorized",
  "ready",
  "outage",
]);

export type ConnectionStatus = v.InferOutput<typeof connectionStatusSchema>;

const capabilitiesSchema = v.object({
  instance_connections_manageable: v.boolean(),
  maximum_upload_bytes: v.nullable(v.number()),
  personal_connections_allowed: v.boolean(),
  status: connectionStatusSchema,
  status_message: v.nullable(v.string()),
});

const connectionSchema = v.object({
  allowed_user_ids: v.array(v.string()),
  authorized: v.boolean(),
  client_certificate_present: v.boolean(),
  credential_present: v.boolean(),
  enabled: v.boolean(),
  endpoint: v.string(),
  etag: v.string(),
  id: v.string(),
  identity_mode: v.picklist(["shared", "individual"]),
  internal_ca_present: v.boolean(),
  name: v.string(),
  permitted_models: v.array(v.string()),
  scope: v.picklist(["instance", "personal"]),
  selected_model: v.nullable(v.string()),
  status: connectionStatusSchema,
  status_message: v.nullable(v.string()),
});

const connectionListSchema = v.object({
  connections: v.array(connectionSchema),
  limit: v.number(),
  offset: v.number(),
});
const modelsSchema = v.object({ models: v.array(v.string()) });
const testSchema = v.object({
  status: connectionStatusSchema,
  status_message: v.nullable(v.string()),
});
const personalPermissionSchema = v.object({
  allowed: v.boolean(),
  etag: v.string(),
  user_id: v.string(),
  username: v.string(),
});
const personalPermissionsSchema = v.object({
  permissions: v.array(personalPermissionSchema),
  limit: v.number(),
  offset: v.number(),
});

export type ComposerCapabilities = v.InferOutput<typeof capabilitiesSchema>;
export type ComposerConnection = v.InferOutput<typeof connectionSchema>;
export type ConnectionTest = v.InferOutput<typeof testSchema>;
export type PersonalPermission = v.InferOutput<typeof personalPermissionSchema>;

export interface ConnectionInput {
  allowed_user_ids: string[];
  enabled: boolean;
  endpoint: string;
  identity_mode: "shared" | "individual";
  name: string;
  permitted_models: string[];
  scope: "instance" | "personal";
  selected_model: string | null;
}

export interface CredentialInput {
  api_key?: string;
  client_certificate?: string;
  client_private_key?: string;
  internal_ca?: string;
  revoke?: boolean;
}

export class ComposerConnectionsApi {
  constructor(private readonly transport = new ApiTransport()) {}

  capabilities(signal?: AbortSignal): Promise<ComposerCapabilities> {
    return this.transport.json(
      "/api/v1/composer/capabilities",
      capabilitiesSchema,
      { signal },
    );
  }

  async connections(
    offset = 0,
    limit = 50,
    signal?: AbortSignal,
  ): Promise<ComposerConnection[]> {
    const response = await this.transport.json(
      `/api/v1/composer/connections?offset=${offset}&limit=${limit}`,
      connectionListSchema,
      { signal },
    );
    return response.connections;
  }

  create(
    input: ConnectionInput,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerConnection>> {
    return this.transport.jsonWithMetadata(
      "/api/v1/composer/connections",
      connectionSchema,
      {
        body: JSON.stringify(input),
        csrf: true,
        method: "POST",
        signal,
      },
    );
  }

  update(
    id: string,
    etag: string,
    input: Partial<ConnectionInput>,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerConnection>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/connections/${id}`,
      connectionSchema,
      {
        body: JSON.stringify(input),
        csrf: true,
        etag,
        method: "PATCH",
        signal,
      },
    );
  }

  credentials(
    id: string,
    etag: string,
    input: CredentialInput,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerConnection>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/connections/${id}/credentials`,
      connectionSchema,
      {
        body: JSON.stringify(input),
        csrf: true,
        etag,
        method: "PUT",
        signal,
      },
    );
  }

  test(id: string, signal?: AbortSignal): Promise<ConnectionTest> {
    return this.transport.json(
      `/api/v1/composer/connections/${id}/test`,
      testSchema,
      { body: JSON.stringify({}), csrf: true, method: "POST", signal },
    );
  }

  models(id: string, signal?: AbortSignal): Promise<string[]> {
    return this.transport
      .json(`/api/v1/composer/connections/${id}/models`, modelsSchema, {
        signal,
      })
      .then((response) => response.models);
  }

  async revokeCredentials(
    id: string,
    etag: string,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.credentials(id, etag, { revoke: true }, signal);
  }

  delete(
    id: string,
    etag: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerConnection>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/connections/${id}`,
      connectionSchema,
      { csrf: true, etag, method: "DELETE", signal },
    );
  }

  async personalPermissions(
    offset = 0,
    limit = 50,
    signal?: AbortSignal,
  ): Promise<PersonalPermission[]> {
    const response = await this.transport.json(
      `/api/v1/composer/personal-permissions?offset=${offset}&limit=${limit}`,
      personalPermissionsSchema,
      { signal },
    );
    return response.permissions;
  }

  personalPermission(
    userId: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<PersonalPermission>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/personal-permissions/${userId}`,
      personalPermissionSchema,
      { signal },
    );
  }

  updatePersonalPermission(
    userId: string,
    allowed: boolean,
    etag: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<PersonalPermission>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/personal-permissions/${userId}`,
      personalPermissionSchema,
      {
        body: JSON.stringify({ allowed }),
        csrf: true,
        etag,
        method: "PUT",
        signal,
      },
    );
  }
}
