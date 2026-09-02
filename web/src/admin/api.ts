import type {
  IdleSessionPolicyResponse,
  TemplateAdministrationContextResponse,
  TemplatePageResponse,
  TemplateResponse,
  TemplateVersionResponse,
  UserResponse,
} from "../api/generated/types.gen";
import {
  vArchiveTemplateApiV1TemplatesTemplateIdArchivePostResponse,
  vClearPreferredTemplateApiV1TemplatePreferenceDeleteResponse,
  vCreateTemplateApiV1TemplatesPostResponse,
  vCreateUserApiV1AdminUsersPostResponse,
  vDeleteTemplateApiV1TemplatesTemplateIdDeleteResponse,
  vGetSessionPolicyApiV1AdminSessionPolicyGetResponse,
  vGetTemplateAdministrationContextApiV1TemplateContextGetResponse,
  vGetTemplateApiV1TemplatesTemplateIdGetResponse,
  vListTemplateVersionsApiV1TemplatesTemplateIdVersionsGetResponse,
  vListTemplatesApiV1TemplatesGetResponse,
  vListUsersApiV1AdminUsersGetResponse,
  vReplaceTemplateApiV1TemplatesTemplateIdContentPutResponse,
  vResetUserPasswordApiV1AdminUsersUserIdPasswordPostResponse,
  vRestoreTemplateVersionApiV1TemplatesTemplateIdVersionsVersionIdRestorePostResponse,
  vSetPreferredTemplateApiV1TemplatesTemplateIdPreferredPutResponse,
  vSetSystemFallbackTemplateApiV1TemplatesTemplateIdSystemFallbackPutResponse,
  vSetUserActiveApiV1AdminUsersUserIdActivePatchResponse,
  vSetUserPasswordChangeRequiredApiV1AdminUsersUserIdPasswordChangeRequiredPatchResponse,
  vUpdateTemplateApiV1TemplatesTemplateIdPatchResponse,
  vUpdateSessionPolicyApiV1AdminSessionPolicyPutResponse,
} from "../api/generated/valibot.gen";
import { ApiTransport, type ApiPath, type JsonResult } from "../api/transport";
import { appendExpectedFonts } from "./operations";

export interface TemplateFilters {
  description?: string;
  name?: string;
  ownerId?: string;
  status?: "active" | "archived";
}

export interface TemplateCreateInput {
  content: File;
  description: string;
  expectedFonts: string;
  name: string;
}

export class AdministrationApi {
  constructor(private readonly transport = new ApiTransport()) {}

  async templateContext(
    signal?: AbortSignal,
  ): Promise<TemplateAdministrationContextResponse> {
    return this.transport.json(
      "/api/v1/template-context",
      vGetTemplateAdministrationContextApiV1TemplateContextGetResponse,
      { signal },
    );
  }

  async allTemplates(
    filters: TemplateFilters,
    signal?: AbortSignal,
  ): Promise<TemplateResponse[]> {
    const items: TemplateResponse[] = [];
    let offset = 0;
    do {
      const query = new URLSearchParams({
        limit: "100",
        offset: String(offset),
      });
      if (filters.name) query.set("name", filters.name);
      if (filters.description) query.set("description", filters.description);
      if (filters.ownerId) query.set("owner_id", filters.ownerId);
      if (filters.status) query.set("status", filters.status);
      const page: TemplatePageResponse = await this.transport.json(
        `/api/v1/templates?${query}`,
        vListTemplatesApiV1TemplatesGetResponse,
        { signal },
      );
      items.push(...page.items);
      offset += page.items.length;
      if (page.items.length === 0 || offset >= page.total) return items;
    } while (true);
  }

  template(
    id: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<TemplateResponse>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/templates/${id}`,
      vGetTemplateApiV1TemplatesTemplateIdGetResponse,
      { signal },
    );
  }

  versions(
    id: string,
    signal?: AbortSignal,
  ): Promise<TemplateVersionResponse[]> {
    return this.transport.json(
      `/api/v1/templates/${id}/versions`,
      vListTemplateVersionsApiV1TemplatesTemplateIdVersionsGetResponse,
      { signal },
    );
  }

  templateContent(
    templateId: string,
    versionId?: string,
    signal?: AbortSignal,
  ): Promise<Response> {
    const path: ApiPath = versionId
      ? `/api/v1/templates/${templateId}/versions/${versionId}/content`
      : `/api/v1/templates/${templateId}/content`;
    return this.transport.download(path, { signal });
  }

  create(
    input: TemplateCreateInput,
    signal?: AbortSignal,
  ): Promise<TemplateResponse> {
    const form = new FormData();
    form.append("name", input.name);
    form.append("description", input.description);
    appendExpectedFonts(form, input.expectedFonts);
    form.append("content", input.content);
    return this.transport.multipart(
      "/api/v1/templates",
      form,
      vCreateTemplateApiV1TemplatesPostResponse,
      { csrf: true, signal },
    );
  }

  updateMetadata(
    id: string,
    etag: string,
    name: string,
    description: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<TemplateResponse>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/templates/${id}`,
      vUpdateTemplateApiV1TemplatesTemplateIdPatchResponse,
      {
        body: JSON.stringify({ description, name }),
        csrf: true,
        etag,
        method: "PATCH",
        signal,
      },
    );
  }

  replace(
    id: string,
    etag: string,
    content: File,
    expectedFonts: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<TemplateVersionResponse>> {
    const form = new FormData();
    form.append("content", content);
    appendExpectedFonts(form, expectedFonts);
    return this.transport.jsonWithMetadata(
      `/api/v1/templates/${id}/content`,
      vReplaceTemplateApiV1TemplatesTemplateIdContentPutResponse,
      { body: form, csrf: true, etag, method: "PUT", signal },
    );
  }

  restore(
    templateId: string,
    versionId: string,
    etag: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<TemplateVersionResponse>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/templates/${templateId}/versions/${versionId}/restore`,
      vRestoreTemplateVersionApiV1TemplatesTemplateIdVersionsVersionIdRestorePostResponse,
      { csrf: true, etag, method: "POST", signal },
    );
  }

  archive(
    id: string,
    etag: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<TemplateResponse>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/templates/${id}/archive`,
      vArchiveTemplateApiV1TemplatesTemplateIdArchivePostResponse,
      { csrf: true, etag, method: "POST", signal },
    );
  }

  async delete(id: string, etag: string, signal?: AbortSignal): Promise<void> {
    await this.transport.json(
      `/api/v1/templates/${id}`,
      vDeleteTemplateApiV1TemplatesTemplateIdDeleteResponse,
      { csrf: true, etag, method: "DELETE", signal },
    );
  }

  async setPreferred(id: string, signal?: AbortSignal): Promise<void> {
    await this.transport.json(
      `/api/v1/templates/${id}/preferred`,
      vSetPreferredTemplateApiV1TemplatesTemplateIdPreferredPutResponse,
      { csrf: true, method: "PUT", signal },
    );
  }

  async clearPreferred(signal?: AbortSignal): Promise<void> {
    await this.transport.json(
      "/api/v1/template-preference",
      vClearPreferredTemplateApiV1TemplatePreferenceDeleteResponse,
      { csrf: true, method: "DELETE", signal },
    );
  }

  async setFallback(id: string, signal?: AbortSignal): Promise<void> {
    await this.transport.json(
      `/api/v1/templates/${id}/system-fallback`,
      vSetSystemFallbackTemplateApiV1TemplatesTemplateIdSystemFallbackPutResponse,
      { csrf: true, method: "PUT", signal },
    );
  }

  users(signal?: AbortSignal): Promise<UserResponse[]> {
    return this.transport.json(
      "/api/v1/admin/users",
      vListUsersApiV1AdminUsersGetResponse,
      { signal },
    );
  }

  sessionPolicy(
    signal?: AbortSignal,
  ): Promise<JsonResult<IdleSessionPolicyResponse>> {
    return this.transport.jsonWithMetadata(
      "/api/v1/admin/session-policy",
      vGetSessionPolicyApiV1AdminSessionPolicyGetResponse,
      { signal },
    );
  }

  updateSessionPolicy(
    etag: string,
    userIdleMinutes: number,
    adminIdleMinutes: number,
    signal?: AbortSignal,
  ): Promise<JsonResult<IdleSessionPolicyResponse>> {
    return this.transport.jsonWithMetadata(
      "/api/v1/admin/session-policy",
      vUpdateSessionPolicyApiV1AdminSessionPolicyPutResponse,
      {
        body: JSON.stringify({
          admin_idle_minutes: adminIdleMinutes,
          user_idle_minutes: userIdleMinutes,
        }),
        csrf: true,
        etag,
        method: "PUT",
        signal,
      },
    );
  }

  createUser(
    username: string,
    password: string,
    passwordChangeRequired: boolean,
    signal?: AbortSignal,
  ): Promise<UserResponse> {
    return this.transport.json(
      "/api/v1/admin/users",
      vCreateUserApiV1AdminUsersPostResponse,
      {
        body: JSON.stringify({
          password,
          password_change_required: passwordChangeRequired,
          username,
        }),
        csrf: true,
        method: "POST",
        signal,
      },
    );
  }

  setActive(
    id: string,
    active: boolean,
    signal?: AbortSignal,
  ): Promise<UserResponse> {
    return this.transport.json(
      `/api/v1/admin/users/${id}/active`,
      vSetUserActiveApiV1AdminUsersUserIdActivePatchResponse,
      {
        body: JSON.stringify({ active }),
        csrf: true,
        method: "PATCH",
        signal,
      },
    );
  }

  async resetPassword(
    id: string,
    password: string,
    passwordChangeRequired: boolean,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.transport.json(
      `/api/v1/admin/users/${id}/password`,
      vResetUserPasswordApiV1AdminUsersUserIdPasswordPostResponse,
      {
        body: JSON.stringify({
          password,
          password_change_required: passwordChangeRequired,
        }),
        csrf: true,
        method: "POST",
        signal,
      },
    );
  }

  setPasswordChangeRequired(
    id: string,
    required: boolean,
    signal?: AbortSignal,
  ): Promise<UserResponse> {
    return this.transport.json(
      `/api/v1/admin/users/${id}/password-change-required`,
      vSetUserPasswordChangeRequiredApiV1AdminUsersUserIdPasswordChangeRequiredPatchResponse,
      {
        body: JSON.stringify({ required }),
        csrf: true,
        method: "PATCH",
        signal,
      },
    );
  }
}
