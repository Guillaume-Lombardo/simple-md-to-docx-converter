"use client";

import {
  type ChangeEvent,
  type DragEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { AppShell, Alert } from "../../components/primitives";
import { ApiError } from "../api/transport";
import { useAuth } from "../auth/context";
import { type ComposerCapabilities, type ComposerConnection } from "./api";
import {
  ComposerPreview,
  type ActivePreviewRevision,
} from "./preview/composer-preview";
import { readBoundedResponse, verifySha256 } from "./preview/bytes";
import {
  ComposerWorkspaceApi,
  type ComposerGeneration,
  type ComposerTemplate,
  type ComposerDiff,
  type ComposerDraft,
  type ComposerDraftSummary,
  type ComposerMessage,
  type ComposerProposal,
  type ComposerQuestion,
  type ComposerRevision,
  type ComposerRevisionSummary,
  type ComposerStep,
} from "./workspace-api";

const settled = new Set(["completed", "failed", "cancelled"]);
const generationSettled = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "expired",
]);
const pageSize = 100;
type PageKind = "drafts" | "messages" | "proposals" | "questions" | "revisions";
type PageState = Record<PageKind, { loaded: number; hasMore: boolean }>;

function pageState(length: number) {
  return { loaded: length, hasMore: length === pageSize };
}

function appendUnique<T extends { id: string }>(
  current: T[],
  incoming: T[],
): T[] {
  const ids = new Set(current.map((item) => item.id));
  return [...current, ...incoming.filter((item) => !ids.has(item.id))];
}

function prependUnique<T extends { id: string }>(
  current: T[],
  incoming: T[],
): T[] {
  const ids = new Set(current.map((item) => item.id));
  return [...incoming.filter((item) => !ids.has(item.id)), ...current];
}
const availability: Record<string, string> = {
  unconfigured:
    "No Composer connection is configured. Existing drafts and exports remain available.",
  disabled:
    "Composer model use is disabled. Existing drafts and exports remain available.",
  unauthorized:
    "Model use has not been granted to this account. Existing drafts and exports remain available.",
  outage:
    "The model connection is temporarily unavailable. Existing drafts and exports remain available.",
};

function safeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "The Composer request could not be completed. Try again.";
}

function fileFormat(mediaType: string): "docx" | "pptx" | "pdf" | null {
  if (
    mediaType ===
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  )
    return "docx";
  if (
    mediaType ===
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  )
    return "pptx";
  if (mediaType === "application/pdf") return "pdf";
  return null;
}

function artifactUrl(
  draftId: string,
  revisionId: string,
  kind: "preview" | "download",
) {
  return `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}/artifacts/${kind}`;
}

function localKey(
  field:
    | "connection"
    | "content"
    | "intent"
    | "message"
    | "model"
    | "selected-model"
    | "output"
    | "template"
    | "dialect"
    | "slide-level"
    | "title"
    | "tokens",
  ownerId: string,
  draftId: string,
): string {
  return `composer:${field}:${ownerId}:${draftId}`;
}

function readLocal(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key: string, value: string): boolean {
  try {
    sessionStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function removeLocal(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* The in-memory value remains usable. */
  }
}

type View = {
  drafts: ComposerDraftSummary[];
  draft: ComposerDraft | null;
  messages: ComposerMessage[];
  proposals: ComposerProposal[];
  questions: ComposerQuestion[];
  revisions: ComposerRevisionSummary[];
  revision: ComposerRevision | null;
};

const emptyView: View = {
  drafts: [],
  draft: null,
  messages: [],
  proposals: [],
  questions: [],
  revisions: [],
  revision: null,
};

export function ComposerWorkspace({ api }: { api?: ComposerWorkspaceApi }) {
  const { state } = useAuth();
  if (state.phase !== "authenticated") return null;
  return <ComposerWorkspaceInner key={state.user.id} api={api} />;
}

function ComposerWorkspaceInner({
  api: supplied,
}: {
  api?: ComposerWorkspaceApi;
}) {
  const [api] = useState(() => supplied ?? new ComposerWorkspaceApi());
  const { controller: auth, state: authState } = useAuth();
  const [view, setView] = useState<View>(emptyView);
  const [capabilities, setCapabilities] = useState<ComposerCapabilities | null>(
    null,
  );
  const [connections, setConnections] = useState<ComposerConnection[]>([]);
  const [connectionId, setConnectionId] = useState("");
  const [model, setModel] = useState("");
  const [requestedTokens, setRequestedTokens] = useState("");
  const [message, setMessage] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [modelContent, setModelContent] = useState("");
  const [stepIntent, setStepIntent] = useState<"proposal" | "question">(
    "proposal",
  );
  const [resumeQuestionId, setResumeQuestionId] = useState("");
  const [questionAnswers, setQuestionAnswers] = useState<
    Record<string, string>
  >({});
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [step, setStep] = useState<ComposerStep | null>(null);
  const [generation, setGeneration] = useState<ComposerGeneration | null>(null);
  const [activePreview, setActivePreview] = useState<{
    draftId: string;
    revisionId: string;
    format: "docx" | "pptx" | "pdf";
    downloadUrl: string;
  } | null>(null);
  const [output, setOutput] = useState<"docx" | "pdf" | "pptx">("docx");
  const [templateId, setTemplateId] = useState("");
  const [templates, setTemplates] = useState<ComposerTemplate[]>([]);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [templateError, setTemplateError] = useState("");
  const [templateRefresh, setTemplateRefresh] = useState(0);
  const [dialect, setDialect] = useState<"auto" | "markdown" | "marp">("auto");
  const [slideLevel, setSlideLevel] = useState(2);
  const [diff, setDiff] = useState<{
    revisionId: string;
    value: ComposerDiff;
  } | null>(null);
  const [textPreview, setTextPreview] = useState<{
    revisionId: string;
    content: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState<PageKind | null>(null);
  const [pages, setPages] = useState<PageState>({
    drafts: pageState(0),
    messages: pageState(0),
    proposals: pageState(0),
    questions: pageState(0),
    revisions: pageState(0),
  });
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [connectionWarning, setConnectionWarning] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const requestCounter = useRef(0);
  const busyRef = useRef(false);
  const selectedId = useRef<string | null>(null);
  const selectionCounter = useRef(0);
  const refreshCounter = useRef(0);
  const loadingMoreRef = useRef<PageKind | null>(null);
  const publishingGeneration = useRef<string | null>(null);
  const recoveringGeneration = useRef<string | null>(null);
  const mounted = useRef(false);

  const ownerId =
    authState.phase === "authenticated" ? authState.user.id : null;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const currentOwner = useCallback(() => {
    const session = auth.snapshot();
    return (
      mounted.current &&
      ownerId !== null &&
      session.phase === "authenticated" &&
      session.user.id === ownerId
    );
  }, [auth, ownerId]);
  const expireIfUnauthorized = useCallback(
    (failure: unknown): boolean => {
      if (!(failure instanceof ApiError) || failure.status !== 401)
        return false;
      if (currentOwner()) auth.expire();
      return true;
    },
    [auth, currentOwner],
  );
  const chosen = connections.find((item) => item.id === connectionId);
  const readyConnections = connections.filter(
    (item) => item.authorized && item.enabled && item.status === "ready",
  );
  const modelChoices = chosen?.permitted_models ?? [];
  const modelReady = Boolean(
    capabilities?.status === "ready" &&
    chosen?.authorized &&
    chosen.enabled &&
    chosen.status === "ready" &&
    modelChoices.includes(model),
  );
  const onActiveRevisionChange = useCallback(
    (
      active: {
        draftId: string;
        revisionId: string;
        format: "docx" | "pptx" | "pdf";
        downloadUrl: string;
      } | null,
    ) => setActivePreview(active),
    [],
  );

  const load = useCallback(
    async (draftId?: string | null, preferredRevisionId?: string) => {
      const request = ++refreshCounter.current;
      setLoading(true);
      try {
        const [capResult, availableResult, draftsResult] =
          await Promise.allSettled([
            api.connections.capabilities(),
            api.connections.connections(),
            api.drafts(),
          ]);
        for (const result of [capResult, availableResult, draftsResult]) {
          if (
            result.status === "rejected" &&
            expireIfUnauthorized(result.reason)
          )
            return;
        }
        if (draftsResult.status === "rejected") throw draftsResult.reason;
        const drafts = draftsResult.value;
        const cap: ComposerCapabilities =
          capResult.status === "fulfilled"
            ? capResult.value
            : {
                status: "outage",
                status_message: "Model settings are temporarily unavailable.",
                instance_connections_manageable: false,
                maximum_upload_bytes: null,
                maximum_output_tokens: null,
                personal_connections_allowed: false,
              };
        const available =
          availableResult.status === "fulfilled" ? availableResult.value : [];
        if (request !== refreshCounter.current || !currentOwner()) return;
        setCapabilities(cap);
        setConnections(available);
        setConnectionWarning(
          capResult.status === "rejected" ||
            availableResult.status === "rejected"
            ? "Model connection details could not be loaded. Saved drafts and exports remain available."
            : "",
        );
        const requested = draftId ?? selectedId.current;
        const id = requested || drafts[0]?.id;
        const preferredConnectionId =
          id && ownerId ? readLocal(localKey("connection", ownerId, id)) : null;
        const nextConnection =
          available.find(
            (item) =>
              item.id === preferredConnectionId &&
              item.authorized &&
              item.status === "ready",
          ) ??
          available.find((item) => item.authorized && item.status === "ready");
        setConnectionId(nextConnection?.id ?? "");
        const preferredModel =
          id && ownerId
            ? readLocal(localKey("selected-model", ownerId, id))
            : null;
        setModel(
          preferredModel &&
            nextConnection?.permitted_models.includes(preferredModel)
            ? preferredModel
            : (nextConnection?.selected_model ?? ""),
        );
        if (!id) {
          selectedId.current = null;
          setView({ ...emptyView, drafts });
          setPages({
            drafts: pageState(drafts.length),
            messages: pageState(0),
            proposals: pageState(0),
            questions: pageState(0),
            revisions: pageState(0),
          });
          setGeneration(null);
          setActivePreview(null);
          setError("");
          return;
        }
        const [draft, messages, proposals, questions, revisions] =
          await Promise.all([
            api.draft(id),
            api.messages(id),
            api.proposals(id),
            api.questions(id),
            api.revisions(id),
          ]);
        const revisionId =
          preferredRevisionId ??
          (view.revision?.draft_id === id
            ? view.revision.id
            : draft.current_revision_id);
        const revision = revisionId ? await api.revision(id, revisionId) : null;
        if (request !== refreshCounter.current || !currentOwner()) return;
        selectedId.current = id;
        const visibleRevisions =
          revision && !revisions.some((item) => item.id === revision.id)
            ? [revision, ...revisions]
            : revisions;
        const visibleDrafts = drafts.some((item) => item.id === id)
          ? drafts
          : [
              {
                id: draft.id,
                title: draft.title,
                version: draft.version,
                current_revision_id: draft.current_revision_id,
                source_kind: draft.source_kind,
                source_media_type: draft.source_media_type,
                updated_at: draft.updated_at,
                etag: draft.etag,
              },
              ...drafts,
            ];
        setView({
          drafts: visibleDrafts,
          draft,
          messages: messages.toReversed(),
          proposals,
          questions,
          revisions: visibleRevisions,
          revision,
        });
        setPages({
          drafts: pageState(drafts.length),
          messages: pageState(messages.length),
          proposals: pageState(proposals.length),
          questions: pageState(questions.length),
          revisions: pageState(revisions.length),
        });
        if (ownerId) {
          const savedStepId = readLocal(`composer:step:${ownerId}:${id}`);
          if (savedStepId)
            void api
              .step(id, savedStepId)
              .then((restored) => {
                if (request === refreshCounter.current && currentOwner())
                  setStep(restored);
              })
              .catch((failure) => {
                if (expireIfUnauthorized(failure)) return;
                removeLocal(`composer:step:${ownerId}:${id}`);
              });
          const savedGenerationId = readLocal(
            `composer:generation:${ownerId}:${id}`,
          );
          void (
            savedGenerationId
              ? api.generation(id, savedGenerationId).catch(async (failure) => {
                  if (expireIfUnauthorized(failure)) return null;
                  removeLocal(`composer:generation:${ownerId}:${id}`);
                  const recent = await api.generations(id);
                  return (
                    recent.find((item) => !item.result_revision_id) ?? null
                  );
                })
              : api
                  .generations(id)
                  .then(
                    (recent) =>
                      recent.find((item) => !item.result_revision_id) ?? null,
                  )
          )
            .then((restored) => {
              if (request !== refreshCounter.current || !currentOwner()) return;
              if (restored?.result_revision_id === revision?.id) {
                removeLocal(`composer:generation:${ownerId}:${id}`);
                setGeneration(null);
                return;
              }
              setGeneration(restored);
              if (restored)
                writeLocal(`composer:generation:${ownerId}:${id}`, restored.id);
            })
            .catch((failure) => {
              if (expireIfUnauthorized(failure)) return;
              if (request === refreshCounter.current) setGeneration(null);
            });
        }
        const savedOutput = ownerId
          ? readLocal(localKey("output", ownerId, id))
          : null;
        setOutput(
          savedOutput === "pdf" || savedOutput === "pptx"
            ? savedOutput
            : "docx",
        );
        setDialect(
          ownerId && readLocal(localKey("dialect", ownerId, id)) === "marp"
            ? "marp"
            : ownerId &&
                readLocal(localKey("dialect", ownerId, id)) === "markdown"
              ? "markdown"
              : "auto",
        );
        const savedSlideLevel = ownerId
          ? Number(readLocal(localKey("slide-level", ownerId, id)))
          : 2;
        setSlideLevel(
          Number.isInteger(savedSlideLevel) &&
            savedSlideLevel >= 1 &&
            savedSlideLevel <= 6
            ? savedSlideLevel
            : 2,
        );
        setDraftContent(
          ownerId
            ? (readLocal(localKey("content", ownerId, id)) ?? draft.content)
            : draft.content,
        );
        setDraftTitle(
          ownerId
            ? (readLocal(localKey("title", ownerId, id)) ?? draft.title)
            : draft.title,
        );
        setMessage(
          ownerId ? (readLocal(localKey("message", ownerId, id)) ?? "") : "",
        );
        setModelContent(
          ownerId ? (readLocal(localKey("model", ownerId, id)) ?? "") : "",
        );
        setResumeQuestionId(
          ownerId ? (readLocal(`composer:resume:${ownerId}:${id}`) ?? "") : "",
        );
        setStepIntent(
          ownerId && readLocal(localKey("intent", ownerId, id)) === "question"
            ? "question"
            : "proposal",
        );
        setRequestedTokens(
          ownerId ? (readLocal(localKey("tokens", ownerId, id)) ?? "") : "",
        );
        setError("");
        if (ownerId) writeLocal(`composer:selected:${ownerId}`, id);
      } catch (failure) {
        if (request !== refreshCounter.current || !currentOwner()) return;
        if (expireIfUnauthorized(failure)) return;
        setError(safeError(failure));
      } finally {
        if (request === refreshCounter.current) setLoading(false);
      }
    },
    [api, currentOwner, expireIfUnauthorized, ownerId, view.revision],
  );

  useEffect(() => {
    if (!ownerId) return;
    selectionCounter.current += 1;
    const fromUrl = new URLSearchParams(window.location.search).get("draft");
    const stored = readLocal(`composer:selected:${ownerId}`);
    selectedId.current = fromUrl || stored;
    void load(selectedId.current);
    return () => {
      refreshCounter.current += 1;
    };
    // Initial load is tied to identity. Later refreshes are explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownerId]);

  useEffect(() => {
    const draftId = view.draft?.id;
    if (!draftId || !ownerId) return;
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) setTemplateLoading(true);
    });
    void Promise.all([
      api.conversionOptions(output, controller.signal),
      api.templates(output, controller.signal),
    ])
      .then(([options, available]) => {
        if (
          controller.signal.aborted ||
          selectedId.current !== draftId ||
          !currentOwner()
        )
          return;
        setTemplates(available);
        setTemplateError("");
        const remembered = readLocal(localKey("template", ownerId, draftId));
        const preferred = available.find(
          (item) => item.id === remembered && item.current_version_id,
        );
        const resolved = available.find(
          (item) =>
            item.id === options.resolved_template?.id &&
            item.current_version_id === options.template_version_id,
        );
        setTemplateId(
          remembered === "pandoc" ? "" : (preferred?.id ?? resolved?.id ?? ""),
        );
      })
      .catch((failure) => {
        if (controller.signal.aborted) return;
        if (expireIfUnauthorized(failure)) return;
        setTemplates([]);
        setTemplateId("");
        setTemplateError(safeError(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setTemplateLoading(false);
      });
    return () => controller.abort();
  }, [
    api,
    currentOwner,
    expireIfUnauthorized,
    output,
    ownerId,
    templateRefresh,
    view.draft?.id,
  ]);

  useEffect(() => {
    if (!step || settled.has(step.status) || !view.draft) return;
    const draftId = view.draft.id;
    const stepId = step.id;
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.step(draftId, stepId);
        if (selectedId.current !== draftId || !currentOwner()) return;
        setStep(result);
        if (settled.has(result.status)) void load(draftId);
      } catch (failure) {
        if (expireIfUnauthorized(failure)) return;
        if (selectedId.current !== draftId) return;
        setError(safeError(failure));
      }
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [api, currentOwner, expireIfUnauthorized, load, step, view.draft]);

  useEffect(() => {
    const draftId = view.draft?.id;
    if (!generation || !draftId || generation.draft_id !== draftId) return;
    const selectedAtStart = selectionCounter.current;
    const stillSelected = () =>
      selectedId.current === draftId &&
      selectionCounter.current === selectedAtStart &&
      currentOwner();
    if (generation.result_revision_id) {
      if (view.revision?.id === generation.result_revision_id) {
        queueMicrotask(() => {
          if (!stillSelected()) return;
          if (ownerId) removeLocal(`composer:generation:${ownerId}:${draftId}`);
          setGeneration(null);
          setNotice("Generated revision is ready to preview and download.");
        });
        return;
      }
      if (recoveringGeneration.current === generation.id) return;
      recoveringGeneration.current = generation.id;
      void load(draftId, generation.result_revision_id).finally(() => {
        recoveringGeneration.current = null;
      });
      return;
    }
    if (generation.status === "succeeded" && generation.publishable) {
      if (
        draftContent !== view.draft?.content ||
        draftTitle !== view.draft?.title
      )
        return;
      if (publishingGeneration.current === generation.id) return;
      publishingGeneration.current = generation.id;
      const keyName = `composer:generation-publish:${ownerId}:${draftId}:${generation.id}`;
      const key = readLocal(keyName) ?? crypto.randomUUID();
      writeLocal(keyName, key);
      void api
        .draft(draftId)
        .then((freshDraft) =>
          api.publishGeneration(freshDraft, generation.id, key),
        )
        .then((published) => {
          if (!stillSelected()) return;
          removeLocal(keyName);
          if (ownerId) removeLocal(`composer:generation:${ownerId}:${draftId}`);
          setGeneration(null);
          setNotice("Generated revision is ready to preview and download.");
          void load(draftId, published.data.id);
        })
        .catch((failure) => {
          if (expireIfUnauthorized(failure)) return;
          if (!stillSelected()) return;
          setError(
            failure instanceof ApiError && failure.status === 412
              ? "The draft changed while generating. Review it before generating again; the previous preview remains available."
              : safeError(failure),
          );
        })
        .finally(() => {
          publishingGeneration.current = null;
        });
      return;
    }
    if (generationSettled.has(generation.status)) return;
    const timer = window.setTimeout(() => {
      void api
        .generation(draftId, generation.id)
        .then((result) => {
          if (stillSelected()) setGeneration(result);
        })
        .catch((failure) => {
          if (expireIfUnauthorized(failure)) return;
          if (stillSelected()) setError(safeError(failure));
        });
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [
    api,
    currentOwner,
    draftContent,
    draftTitle,
    expireIfUnauthorized,
    generation,
    load,
    ownerId,
    view.draft?.content,
    view.draft?.id,
    view.draft?.title,
    view.revision?.id,
  ]);

  useEffect(() => {
    const draft = view.draft;
    const revision = view.revision;
    if (!draft || !revision) return;
    const controller = new AbortController();
    const previous = view.revisions.find(
      (item) => item.number === revision.number - 1,
    );
    if (previous)
      void api
        .diff(draft.id, revision.id, previous.id, controller.signal)
        .then((result) => {
          if (!controller.signal.aborted && currentOwner())
            setDiff({ revisionId: revision.id, value: result });
        })
        .catch((failure) => {
          if (expireIfUnauthorized(failure)) return;
          if (!controller.signal.aborted) setError(safeError(failure));
        });
    if (
      revision.artifacts.find((item) => item.kind === "preview")?.media_type ===
      "text/markdown"
    ) {
      void api
        .download(draft.id, revision.id, "preview", controller.signal)
        .then((response) => response.text())
        .then((content) => {
          if (!controller.signal.aborted && currentOwner())
            setTextPreview({ revisionId: revision.id, content });
        })
        .catch((failure) => {
          if (expireIfUnauthorized(failure)) return;
          if (!controller.signal.aborted) setError(safeError(failure));
        });
    }
    return () => controller.abort();
  }, [
    api,
    currentOwner,
    expireIfUnauthorized,
    view.draft,
    view.revision,
    view.revisions,
  ]);

  async function mutate(
    action: (stillSelected: () => boolean) => Promise<unknown>,
    success: string,
    id = view.draft?.id,
  ) {
    if (!id || busyRef.current) return;
    const selectedAtStart = selectionCounter.current;
    const stillSelected = () =>
      selectedId.current === id &&
      selectionCounter.current === selectedAtStart &&
      currentOwner();
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await action(stillSelected);
      if (!stillSelected()) return;
      setNotice(success);
      const publishedRevision =
        result &&
        typeof result === "object" &&
        "data" in result &&
        result.data &&
        typeof result.data === "object" &&
        "artifacts" in result.data &&
        "id" in result.data &&
        typeof result.data.id === "string"
          ? result.data.id
          : undefined;
      await load(id, publishedRevision);
    } catch (failure) {
      if (expireIfUnauthorized(failure)) return;
      if (!stillSelected()) return;
      if (failure instanceof ApiError && failure.status === 412) await load(id);
      if (stillSelected()) setError(safeError(failure));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function loadMore(kind: PageKind) {
    if (loadingMoreRef.current || !pages[kind].hasMore) return;
    const draftId = view.draft?.id ?? null;
    if (kind !== "drafts" && !draftId) return;
    const selectedAtStart = selectionCounter.current;
    const refreshAtStart = refreshCounter.current;
    const offset = pages[kind].loaded;
    loadingMoreRef.current = kind;
    setLoadingMore(kind);
    try {
      const page =
        kind === "drafts"
          ? await api.drafts(undefined, offset)
          : kind === "messages"
            ? await api.messages(draftId!, undefined, offset)
            : kind === "proposals"
              ? await api.proposals(draftId!, undefined, offset)
              : kind === "questions"
                ? await api.questions(draftId!, undefined, offset)
                : await api.revisions(draftId!, undefined, offset);
      if (
        selectedAtStart !== selectionCounter.current ||
        refreshAtStart !== refreshCounter.current ||
        selectedId.current !== draftId ||
        !currentOwner()
      )
        return;
      setView((previous) => {
        if (kind !== "drafts" && previous.draft?.id !== draftId)
          return previous;
        if (kind === "drafts")
          return {
            ...previous,
            drafts: appendUnique(
              previous.drafts,
              page as ComposerDraftSummary[],
            ),
          };
        if (kind === "messages")
          return {
            ...previous,
            messages: prependUnique(
              previous.messages,
              (page as ComposerMessage[]).toReversed(),
            ),
          };
        if (kind === "proposals")
          return {
            ...previous,
            proposals: appendUnique(
              previous.proposals,
              page as ComposerProposal[],
            ),
          };
        if (kind === "questions")
          return {
            ...previous,
            questions: appendUnique(
              previous.questions,
              page as ComposerQuestion[],
            ),
          };
        return {
          ...previous,
          revisions: appendUnique(
            previous.revisions,
            page as ComposerRevisionSummary[],
          ),
        };
      });
      setPages((previous) => ({
        ...previous,
        [kind]: {
          loaded: offset + page.length,
          hasMore: page.length === pageSize,
        },
      }));
    } catch (failure) {
      if (expireIfUnauthorized(failure)) return;
      if (
        selectedAtStart === selectionCounter.current &&
        refreshAtStart === refreshCounter.current
      )
        setError(safeError(failure));
    } finally {
      loadingMoreRef.current = null;
      setLoadingMore(null);
    }
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
  }

  async function dropConversation(event: DragEvent<HTMLTextAreaElement>) {
    event.preventDefault();
    const draftId = view.draft?.id;
    if (!draftId) return;
    const selectedAtStart = selectionCounter.current;
    const droppedFile = event.dataTransfer.files[0];
    let droppedText = event.dataTransfer.getData("text/plain");
    if (droppedFile) {
      if (!/\.(md|markdown)$/i.test(droppedFile.name)) {
        setError("Drop a Markdown file or plain text into the conversation.");
        return;
      }
      if (
        capabilities?.maximum_upload_bytes == null ||
        droppedFile.size > capabilities.maximum_upload_bytes
      ) {
        setError("The Markdown file exceeds the available upload limit.");
        return;
      }
      try {
        droppedText = await droppedFile.text();
      } catch {
        if (selectionCounter.current === selectedAtStart)
          setError(
            "The Markdown file could not be read. Your text is unchanged.",
          );
        return;
      }
    }
    if (
      !droppedText ||
      !currentOwner() ||
      selectedId.current !== draftId ||
      selectionCounter.current !== selectedAtStart
    )
      return;
    setMessage((current) => {
      const next = current ? `${current}\n\n${droppedText}` : droppedText;
      if (ownerId) remember(localKey("message", ownerId, draftId), next);
      return next;
    });
  }

  function remember(key: string, value: string): void {
    if (!currentOwner()) return;
    if (!writeLocal(key, value))
      setNotice(
        "Browser storage is unavailable. Your unsaved text remains on this page until you leave it.",
      );
  }

  function correctionFor(proposal: ComposerProposal): string {
    const saved =
      ownerId && view.draft
        ? readLocal(
            `composer:correction:${ownerId}:${view.draft.id}:${proposal.id}`,
          )
        : null;
    return editing[proposal.id] ?? saved ?? proposal.proposed_value;
  }

  function answerFor(question: ComposerQuestion): string {
    const saved =
      ownerId && view.draft
        ? readLocal(
            `composer:answer:${ownerId}:${view.draft.id}:${question.id}`,
          )
        : null;
    return questionAnswers[question.id] ?? saved ?? "";
  }

  function prepareAnsweredQuestion(question: ComposerQuestion, answer: string) {
    const content = `Question: ${question.text}\nAnswer: ${answer}`;
    setModelContent(content);
    setStepIntent("proposal");
    setResumeQuestionId(question.id);
    if (ownerId && view.draft) {
      remember(localKey("model", ownerId, view.draft.id), content);
      remember(localKey("intent", ownerId, view.draft.id), "proposal");
      remember(`composer:resume:${ownerId}:${view.draft.id}`, question.id);
    }
  }

  async function decideProposal(
    proposal: ComposerProposal,
    state: "accepted" | "edited" | "rejected",
  ) {
    const draft = view.draft;
    if (!draft) return;
    const value = state === "edited" ? correctionFor(proposal) : undefined;
    await mutate(
      async (stillSelected) => {
        const result = await api.decide(draft, proposal.id, state, value);
        if (ownerId)
          removeLocal(
            `composer:correction:${ownerId}:${draft.id}:${proposal.id}`,
          );
        if (stillSelected())
          setEditing((previous) => {
            const next = { ...previous };
            delete next[proposal.id];
            return next;
          });
        return result;
      },
      state === "rejected"
        ? "Suggestion rejected. No document changed."
        : "Your decision was saved; publish it to create a revision.",
    );
  }

  async function createDraft() {
    if (!file || busyRef.current) return;
    const selectedAtStart = selectionCounter.current;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api.createDraft(file);
      if (selectionCounter.current !== selectedAtStart || !currentOwner())
        return;
      selectionCounter.current += 1;
      selectedId.current = result.data.id;
      window.history.replaceState(
        null,
        "",
        `/composer?draft=${result.data.id}`,
      );
      setFile(null);
      setNotice(
        "The source passed upload validation and was saved as a draft.",
      );
      await load(result.data.id);
    } catch (failure) {
      if (expireIfUnauthorized(failure)) return;
      if (selectionCounter.current === selectedAtStart)
        setError(safeError(failure));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function generateDocument() {
    const original = view.draft;
    if (!original || !publishableMarkdown || busyRef.current) return;
    const draftId = original.id;
    const selectedAtStart = selectionCounter.current;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      let current = original;
      let source: ComposerRevision | null = null;
      const edited =
        draftTitle !== original.title || draftContent !== original.content;
      if (edited) {
        const saved = await api.saveDraft(current, draftTitle, draftContent);
        current = saved.data;
        if (ownerId) {
          removeLocal(localKey("title", ownerId, draftId));
          removeLocal(localKey("content", ownerId, draftId));
        }
      }
      const approvable = !edited
        ? view.proposals.find(
            (proposal) =>
              (proposal.state === "accepted" || proposal.state === "edited") &&
              current.version === proposal.base_version + 2,
          )
        : undefined;
      if (approvable && current.source_media_type === "text/markdown") {
        source = (
          await api.publishProposal(current, approvable.id, crypto.randomUUID())
        ).data;
        current = await api.draft(draftId);
      } else if (!edited && current.current_revision_id) {
        const active =
          view.revision?.id === current.current_revision_id
            ? view.revision
            : await api.revision(draftId, current.current_revision_id);
        let approvedContent: unknown;
        try {
          approvedContent = JSON.parse(active.approved_values).content;
        } catch {
          approvedContent = undefined;
        }
        if (
          (active.operation === "publish_draft" ||
            active.operation.startsWith("publish_proposal:")) &&
          approvedContent === current.content &&
          active.artifacts.some(
            (artifact) =>
              artifact.kind === "download" &&
              artifact.media_type === "text/markdown",
          )
        )
          source = active;
      }
      if (!source) {
        source = (await api.publishDraft(current, crypto.randomUUID())).data;
        current = await api.draft(draftId);
      }
      const template = templates.find((item) => item.id === templateId);
      if (templateId && !template?.current_version_id)
        throw new ApiError(
          0,
          "TEMPLATE_STALE",
          "The selected template is no longer active. Choose a current template before generating.",
        );
      const accepted = await api.startGeneration(
        current,
        source.id,
        {
          output,
          template_id: template?.id ?? null,
          template_version_id: template?.current_version_id ?? null,
          presentation_dialect: output === "pptx" ? dialect : null,
          slide_level: output === "pptx" ? slideLevel : null,
        },
        crypto.randomUUID(),
      );
      if (
        selectedId.current !== draftId ||
        selectionCounter.current !== selectedAtStart ||
        !currentOwner()
      )
        return;
      setGeneration(accepted.data);
      if (ownerId)
        remember(`composer:generation:${ownerId}:${draftId}`, accepted.data.id);
      setNotice(
        `Generating ${output.toUpperCase()} from approved content. The current preview remains available.`,
      );
      await load(draftId, view.revision?.id);
    } catch (failure) {
      if (expireIfUnauthorized(failure)) return;
      if (
        selectedId.current !== draftId ||
        selectionCounter.current !== selectedAtStart
      )
        return;
      if (failure instanceof ApiError && failure.status === 412)
        await load(draftId);
      if (selectionCounter.current === selectedAtStart)
        setError(safeError(failure));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function chooseRevision(id: string) {
    if (!view.draft) return;
    const counter = ++requestCounter.current;
    const selectedAtStart = selectionCounter.current;
    try {
      const revision = await api.revision(view.draft.id, id);
      if (
        counter === requestCounter.current &&
        selectionCounter.current === selectedAtStart &&
        currentOwner()
      )
        setView((previous) => ({ ...previous, revision }));
    } catch (failure) {
      if (expireIfUnauthorized(failure)) return;
      if (selectionCounter.current === selectedAtStart)
        setError(safeError(failure));
    }
  }

  async function downloadRevision(
    draftId: string,
    revisionId: string,
    extension: "docx" | "pptx" | "pdf" | "md" | "zip" | "bin",
  ) {
    if (!currentOwner() || selectedId.current !== draftId) return;
    try {
      const revision =
        view.revision?.id === revisionId && view.revision.draft_id === draftId
          ? view.revision
          : await api.revision(draftId, revisionId);
      if (!currentOwner() || selectedId.current !== draftId) return;
      const artifact = revision.artifacts.find(
        (item) => item.kind === "download",
      );
      if (
        !artifact ||
        !Number.isSafeInteger(artifact.size) ||
        artifact.size < 0
      )
        throw new Error("The revision download is unavailable.");
      const response = await api.download(draftId, revisionId, "download");
      const bytes = await readBoundedResponse(
        response,
        artifact.size,
        artifact.media_type,
      );
      if (bytes.byteLength !== artifact.size)
        throw new Error("The downloaded revision has an unexpected size.");
      await verifySha256(bytes, artifact.sha256);
      if (!currentOwner() || selectedId.current !== draftId) return;
      const blob = new Blob([bytes], { type: artifact.media_type });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `revision-${revisionId}.${extension}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (failure) {
      if (expireIfUnauthorized(failure)) return;
      if (currentOwner()) setError(safeError(failure));
    }
  }

  function downloadActiveRevision(active: ActivePreviewRevision) {
    if (
      active.draftId !== view.draft?.id ||
      active.downloadUrl !==
        artifactUrl(active.draftId, active.revisionId, "download")
    )
      return;
    void downloadRevision(active.draftId, active.revisionId, active.format);
  }

  if (authState.phase !== "authenticated") return null;
  const draft = view.draft;
  const revision = view.revision;
  const publishableMarkdown =
    draft?.source_media_type === "text/markdown" ||
    draft?.source_media_type === "application/zip";
  const activeDiff =
    diff && revision && diff.revisionId === revision.id ? diff.value : null;
  const activeTextPreview =
    textPreview && revision && textPreview.revisionId === revision.id
      ? textPreview.content
      : null;
  const previewArtifact = revision?.artifacts.find(
    (item) => item.kind === "preview",
  );
  const format = previewArtifact
    ? fileFormat(previewArtifact.media_type)
    : null;

  return (
    <AppShell
      current="Composer"
      nativeNavigation
      user={authState.user}
      pending={authState.pending}
      onLogout={() => void auth.logout()}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold">Composer</h1>
          <p>
            Prepare a document with reviewed suggestions and exact revisions.
          </p>
        </div>
        <a className="text-accent underline" href="/composer/connections">
          My connections
        </a>
      </div>
      {loading && <p aria-live="polite">Loading Composer…</p>}
      {error && <Alert tone="danger">{error}</Alert>}
      {connectionWarning && <Alert>{connectionWarning}</Alert>}
      {notice && <p role="status">{notice}</p>}
      {capabilities && capabilities.status !== "ready" && (
        <Alert>
          {availability[capabilities.status] ??
            capabilities.status_message ??
            "Composer model use is unavailable."}
        </Alert>
      )}
      <section
        aria-label="Choose a draft"
        className="flex flex-wrap items-end gap-3"
      >
        <label className="grid gap-1">
          Saved drafts
          <select
            aria-label="Saved drafts"
            className="rounded-control border border-muted p-2"
            value={draft?.id ?? ""}
            onChange={(event) => {
              selectionCounter.current += 1;
              selectedId.current = event.target.value || null;
              requestCounter.current += 1;
              setStep(null);
              setGeneration(null);
              setActivePreview(null);
              setView((previous) => ({
                ...emptyView,
                drafts: previous.drafts,
              }));
              window.history.replaceState(
                null,
                "",
                event.target.value
                  ? `/composer?draft=${event.target.value}`
                  : "/composer",
              );
              void load(event.target.value);
            }}
          >
            {!draft && <option value="">Select a draft</option>}
            {view.drafts.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
        {pages.drafts.hasMore && (
          <button
            type="button"
            disabled={loadingMore !== null}
            onClick={() => void loadMore("drafts")}
          >
            {loadingMore === "drafts" ? "Loading drafts…" : "Load older drafts"}
          </button>
        )}
        <label
          className="grid gap-1 rounded-control border border-muted p-2"
          onDragOver={(event) => event.preventDefault()}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={(event) => {
            event.preventDefault();
            setDragging(false);
          }}
          onDrop={(event: DragEvent<HTMLLabelElement>) => {
            event.preventDefault();
            setDragging(false);
            setFile(event.dataTransfer.files[0] ?? null);
          }}
        >
          Markdown or Office source
          <input
            accept=".md,.zip,.docx,.pptx,.pdf"
            aria-label="Markdown or Office source"
            onChange={chooseFile}
            type="file"
          />
          <span>
            {dragging
              ? "Drop the file now."
              : file
                ? `${file.name} (${file.size} bytes)`
                : "Choose or drop a source file."}
          </span>
        </label>
        <button
          type="button"
          disabled={
            !file ||
            busy ||
            capabilities?.status !== "ready" ||
            readyConnections.length === 0
          }
          onClick={() => void createDraft()}
        >
          Create draft
        </button>
      </section>
      {draft && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <section
            aria-label="Composer conversation"
            className="min-w-0 space-y-5"
          >
            <div className="rounded-control border border-muted p-4">
              <p className="text-sm text-muted">
                Draft {draft.id} · {draft.source_media_type}
              </p>
              <label className="grid gap-1">
                Draft title{" "}
                <input
                  className="rounded-control border border-muted p-2"
                  value={draftTitle}
                  onChange={(event) => {
                    setDraftTitle(event.target.value);
                    if (ownerId)
                      remember(
                        localKey("title", ownerId, draft.id),
                        event.target.value,
                      );
                  }}
                />
              </label>
              <label className="mt-3 grid gap-1">
                {publishableMarkdown ? "Editable Markdown" : "Draft notes"}{" "}
                <textarea
                  className="min-h-24 rounded-control border border-muted p-2"
                  value={draftContent}
                  onChange={(event) => {
                    setDraftContent(event.target.value);
                    if (ownerId)
                      remember(
                        localKey("content", ownerId, draft.id),
                        event.target.value,
                      );
                  }}
                />
              </label>
              {!publishableMarkdown && (
                <p className="mt-2 text-sm">
                  Office source files and their revisions remain available to
                  preview and download. These notes do not edit the Office file;
                  <a className="text-accent underline" href="/revert">
                    open 2md
                  </a>{" "}
                  to convert it to Markdown before publishing edited content.
                </p>
              )}
              <button
                type="button"
                disabled={
                  busy ||
                  !draftTitle.trim() ||
                  (draftTitle === draft.title && draftContent === draft.content)
                }
                onClick={() =>
                  void mutate(async () => {
                    const result = await api.saveDraft(
                      draft,
                      draftTitle,
                      draftContent,
                    );
                    if (ownerId) {
                      removeLocal(localKey("title", ownerId, draft.id));
                      removeLocal(localKey("content", ownerId, draft.id));
                    }
                    return result;
                  }, "Draft saved.")
                }
              >
                Save draft
              </button>
              {publishableMarkdown && (
                <button
                  type="button"
                  disabled={
                    busy ||
                    !draft.content.trim() ||
                    draftTitle !== draft.title ||
                    draftContent !== draft.content
                  }
                  onClick={() =>
                    void mutate(
                      () => api.publishDraft(draft, crypto.randomUUID()),
                      "Saved Markdown published as an immutable revision.",
                    )
                  }
                >
                  Publish saved Markdown
                </button>
              )}
            </div>
            <div className="rounded-control border border-muted p-4">
              <h2 className="text-xl font-semibold">Conversation</h2>
              <ol
                className="max-h-80 space-y-3 overflow-y-auto"
                aria-label="Messages"
              >
                {view.messages.map((item) => (
                  <li key={item.id} className="rounded-control bg-surface p-2">
                    <strong>
                      {item.role === "user" ? "You" : "Assistant"}
                    </strong>
                    <p className="whitespace-pre-wrap">{item.content}</p>
                  </li>
                ))}
              </ol>
              {pages.messages.hasMore && (
                <button
                  type="button"
                  disabled={loadingMore !== null}
                  onClick={() => void loadMore("messages")}
                >
                  {loadingMore === "messages"
                    ? "Loading messages…"
                    : "Load older messages"}
                </button>
              )}
              <label className="mt-3 grid gap-1">
                Message or answer
                <textarea
                  className="min-h-24 rounded-control border border-muted p-2"
                  value={message}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => void dropConversation(event)}
                  onChange={(event) => {
                    setMessage(event.target.value);
                    if (ownerId)
                      remember(
                        localKey("message", ownerId, draft.id),
                        event.target.value,
                      );
                  }}
                />
              </label>
              <button
                type="button"
                disabled={busy || !message.trim()}
                onClick={() =>
                  void mutate(async (stillSelected) => {
                    await api.addMessage(
                      draft,
                      message.trim(),
                      crypto.randomUUID(),
                    );
                    if (stillSelected()) {
                      setModelContent(message.trim());
                      setMessage("");
                    }
                    if (ownerId)
                      remember(
                        localKey("model", ownerId, draft.id),
                        message.trim(),
                      );
                    if (ownerId)
                      removeLocal(localKey("message", ownerId, draft.id));
                  }, "Message saved. Review the exact text below before asking a model.")
                }
              >
                Save message
              </button>
            </div>
            <div className="rounded-control border border-muted p-4">
              <h2 className="text-xl font-semibold">Ask a model</h2>
              <p className="text-sm">
                Only the text below is sent. Review the destination and model
                first. Draft files are not attached.
              </p>
              <label className="mt-3 grid gap-1">
                Model step purpose
                <select
                  value={stepIntent}
                  onChange={(event) => {
                    setStepIntent(
                      event.target.value as "proposal" | "question",
                    );
                    if (ownerId)
                      remember(
                        localKey("intent", ownerId, draft.id),
                        event.target.value,
                      );
                    if (event.target.value === "question") {
                      setResumeQuestionId("");
                      if (ownerId)
                        removeLocal(`composer:resume:${ownerId}:${draft.id}`);
                    }
                  }}
                >
                  <option value="proposal">Suggest a change</option>
                  <option value="question">Ask for missing information</option>
                </select>
              </label>
              <label className="mt-3 grid gap-1">
                Connection
                <select
                  value={connectionId}
                  onChange={(event) => {
                    const next = connections.find(
                      (item) => item.id === event.target.value,
                    );
                    setConnectionId(event.target.value);
                    setModel(next?.selected_model ?? "");
                    if (ownerId) {
                      remember(
                        localKey("connection", ownerId, draft.id),
                        event.target.value,
                      );
                      remember(
                        localKey("selected-model", ownerId, draft.id),
                        next?.selected_model ?? "",
                      );
                    }
                  }}
                >
                  <option value="">Select an authorized connection</option>
                  {readyConnections.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="mt-3 grid gap-1">
                Model
                <select
                  value={model}
                  onChange={(event) => {
                    setModel(event.target.value);
                    if (ownerId)
                      remember(
                        localKey("selected-model", ownerId, draft.id),
                        event.target.value,
                      );
                  }}
                >
                  <option value="">Select a model</option>
                  {modelChoices.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              {chosen && (
                <p className="mt-3">
                  Destination: <code>{chosen.endpoint}</code>
                </p>
              )}
              <label className="mt-3 grid gap-1">
                Exact approved text for transmission
                <textarea
                  className="min-h-32 rounded-control border border-muted p-2"
                  value={modelContent}
                  onChange={(event) => {
                    setModelContent(event.target.value);
                    if (ownerId)
                      remember(
                        localKey("model", ownerId, draft.id),
                        event.target.value,
                      );
                  }}
                />
              </label>
              <label className="mt-3 grid gap-1">
                Maximum output tokens (1–
                {capabilities?.maximum_output_tokens ?? "unavailable"})
                <input
                  type="number"
                  min={1}
                  max={capabilities?.maximum_output_tokens ?? undefined}
                  value={requestedTokens}
                  onChange={(event) => {
                    setRequestedTokens(event.target.value);
                    if (ownerId)
                      remember(
                        localKey("tokens", ownerId, draft.id),
                        event.target.value,
                      );
                  }}
                />
              </label>
              <button
                type="button"
                disabled={
                  busy ||
                  !modelReady ||
                  !modelContent.trim() ||
                  !Number.isSafeInteger(Number(requestedTokens)) ||
                  Number(requestedTokens) < 1 ||
                  Number(requestedTokens) >
                    (capabilities?.maximum_output_tokens ?? 0)
                }
                onClick={() =>
                  void mutate(async (stillSelected) => {
                    const started = await api.startStep(
                      draft,
                      {
                        connection_id: chosen!.id,
                        approved_endpoint: chosen!.endpoint,
                        approved_model: model,
                        content: modelContent.trim(),
                        max_output_tokens: Number(requestedTokens),
                        intent: stepIntent,
                        ...(resumeQuestionId && stepIntent === "proposal"
                          ? { answered_question_id: resumeQuestionId }
                          : {}),
                      },
                      crypto.randomUUID(),
                    );
                    if (stillSelected()) setStep(started);
                    if (ownerId) {
                      remember(
                        `composer:step:${ownerId}:${draft.id}`,
                        started.id,
                      );
                      removeLocal(localKey("model", ownerId, draft.id));
                      removeLocal(`composer:resume:${ownerId}:${draft.id}`);
                    }
                    if (stillSelected()) setResumeQuestionId("");
                  }, "Model step queued for review.")
                }
              >
                Send reviewed text
              </button>
              {step?.draft_id === draft.id && (
                <details
                  className="mt-3 rounded-control border border-muted p-3"
                  open={!settled.has(step.status)}
                >
                  <summary>
                    {step.intent === "question" ? "Question" : "Suggestion"}{" "}
                    step {step.status} · {step.model_identity}
                  </summary>
                  <p>Source: text explicitly reviewed for this model step.</p>
                  <p>
                    Result:{" "}
                    {step.question_id
                      ? "A question awaits your answer below."
                      : step.proposal_id
                        ? "A proposal is ready for human review below."
                        : "No published change."}
                  </p>
                  <p>Changes: none until you approve and publish a proposal.</p>
                  {step.error_code && <p>Safe error: {step.error_code}</p>}
                  {!settled.has(step.status) && (
                    <button
                      type="button"
                      onClick={() =>
                        void mutate(async (stillSelected) => {
                          const cancelled = await api.cancelStep(
                            draft.id,
                            step.id,
                          );
                          if (stillSelected()) setStep(cancelled);
                        }, "Model step cancelled.")
                      }
                    >
                      Cancel step
                    </button>
                  )}
                </details>
              )}
            </div>
            <section aria-label="Questions" className="space-y-3">
              <h2 className="text-xl font-semibold">Missing information</h2>
              {pages.questions.hasMore && (
                <button
                  type="button"
                  disabled={loadingMore !== null}
                  onClick={() => void loadMore("questions")}
                >
                  {loadingMore === "questions"
                    ? "Loading questions…"
                    : "Load older questions"}
                </button>
              )}
              {view.questions.length === 0 && (
                <p>No unanswered model questions for this draft.</p>
              )}
              {view.questions.map((question) => (
                <details
                  key={question.id}
                  className="rounded-control border border-muted p-4"
                  open={question.state === "pending"}
                >
                  <summary>
                    Question · {question.state} · version{" "}
                    {question.base_version}
                  </summary>
                  <p className="mt-3 whitespace-pre-wrap">{question.text}</p>
                  {question.state === "pending" ? (
                    <>
                      <label className="mt-3 grid gap-1">
                        Your answer
                        <textarea
                          className="min-h-24 rounded-control border border-muted p-2"
                          value={answerFor(question)}
                          onChange={(event) => {
                            setQuestionAnswers((previous) => ({
                              ...previous,
                              [question.id]: event.target.value,
                            }));
                            if (ownerId)
                              remember(
                                `composer:answer:${ownerId}:${draft.id}:${question.id}`,
                                event.target.value,
                              );
                          }}
                        />
                      </label>
                      <button
                        type="button"
                        disabled={busy || !answerFor(question).trim()}
                        onClick={() =>
                          void mutate(async (stillSelected) => {
                            const answer = answerFor(question).trim();
                            const result = await api.answerQuestion(
                              draft,
                              question.id,
                              answer,
                              crypto.randomUUID(),
                            );
                            if (ownerId)
                              removeLocal(
                                `composer:answer:${ownerId}:${draft.id}:${question.id}`,
                              );
                            if (stillSelected()) {
                              setQuestionAnswers((previous) => {
                                const next = { ...previous };
                                delete next[question.id];
                                return next;
                              });
                              prepareAnsweredQuestion(question, answer);
                            }
                            return result;
                          }, "Your answer was saved. Review the exact text before resuming the model.")
                        }
                      >
                        Save answer
                      </button>
                    </>
                  ) : (
                    <>
                      <p className="mt-2 whitespace-pre-wrap">
                        Your answer: {question.answer_content}
                      </p>
                      <button
                        type="button"
                        disabled={busy || !question.answer_content}
                        onClick={() =>
                          prepareAnsweredQuestion(
                            question,
                            question.answer_content ?? "",
                          )
                        }
                      >
                        Prepare answer for model
                      </button>
                    </>
                  )}
                </details>
              ))}
            </section>
            <section aria-label="Proposals" className="space-y-3">
              <h2 className="text-xl font-semibold">Proposals and questions</h2>
              {pages.proposals.hasMore && (
                <button
                  type="button"
                  disabled={loadingMore !== null}
                  onClick={() => void loadMore("proposals")}
                >
                  {loadingMore === "proposals"
                    ? "Loading proposals…"
                    : "Load older proposals"}
                </button>
              )}
              {view.proposals.length === 0 && (
                <p>
                  No suggestions yet. Missing or ambiguous facts need your
                  answer before publication.
                </p>
              )}
              {view.proposals.map((proposal) => (
                <details
                  key={proposal.id}
                  className="rounded-control border border-muted p-4"
                >
                  <summary>
                    Suggestion · {proposal.state} · version{" "}
                    {proposal.base_version}
                  </summary>
                  <div className="mt-3 space-y-2">
                    <p>
                      <strong>Result:</strong>{" "}
                      <span className="whitespace-pre-wrap">
                        {proposal.proposed_value}
                      </span>
                    </p>
                    <p>
                      <strong>Source and confidence:</strong>{" "}
                      {proposal.provenance}. Model assertions are unverified
                      until you approve them; no citation is implied.
                    </p>
                    <p>
                      <strong>Change:</strong> Approval records your chosen
                      text. Publication creates a new immutable revision only
                      for supported content.
                    </p>
                    <p>
                      <strong>Validation:</strong>{" "}
                      {proposal.state === "pending"
                        ? "Awaiting human decision"
                        : proposal.state}
                    </p>
                    {proposal.state === "pending" && (
                      <>
                        <label className="grid gap-1">
                          Corrected value
                          <textarea
                            className="min-h-24 rounded-control border border-muted p-2"
                            value={correctionFor(proposal)}
                            onChange={(event) => {
                              setEditing((previous) => ({
                                ...previous,
                                [proposal.id]: event.target.value,
                              }));
                              if (ownerId)
                                remember(
                                  `composer:correction:${ownerId}:${draft.id}:${proposal.id}`,
                                  event.target.value,
                                );
                            }}
                          />
                        </label>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              void decideProposal(proposal, "accepted")
                            }
                          >
                            Accept
                          </button>
                          <button
                            type="button"
                            disabled={
                              busy ||
                              !correctionFor(proposal).trim() ||
                              correctionFor(proposal) ===
                                proposal.proposed_value
                            }
                            onClick={() =>
                              void decideProposal(proposal, "edited")
                            }
                          >
                            Save correction
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              void decideProposal(proposal, "rejected")
                            }
                          >
                            Reject
                          </button>
                        </div>
                      </>
                    )}
                    {publishableMarkdown &&
                      (proposal.state === "accepted" ||
                        proposal.state === "edited") && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void mutate(
                              () =>
                                api.publishProposal(
                                  draft,
                                  proposal.id,
                                  crypto.randomUUID(),
                                ),
                              "Approved text published as an immutable revision.",
                            )
                          }
                        >
                          Publish approved text
                        </button>
                      )}
                  </div>
                </details>
              ))}
            </section>
          </section>
          <section aria-label="Revision preview" className="min-w-0 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-xl font-semibold">Revision preview</h2>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void mutate(
                    () => api.captureSource(draft, crypto.randomUUID()),
                    "Original source captured as an immutable revision.",
                  )
                }
              >
                Capture source revision
              </button>
            </div>
            {publishableMarkdown && (
              <div className="space-y-3 rounded-control border border-muted p-4">
                <h3 className="font-semibold">
                  Generate from approved content
                </h3>
                <p>
                  Your current edits or approved suggestion become a frozen
                  source. Generation runs in the background; the current
                  revision remains available until the new one is ready.
                </p>
                <div className="flex flex-wrap gap-3">
                  <label className="grid gap-1">
                    Output format
                    <select
                      aria-label="Output format"
                      value={output}
                      onChange={(event) => {
                        const next = event.target.value as
                          | "docx"
                          | "pdf"
                          | "pptx";
                        setOutput(next);
                        setTemplateLoading(true);
                        setTemplateError("");
                        setTemplateId("");
                        setTemplates([]);
                        if (ownerId)
                          remember(localKey("output", ownerId, draft.id), next);
                      }}
                    >
                      <option value="docx">Word document (DOCX)</option>
                      <option value="pdf">PDF</option>
                      <option value="pptx">PowerPoint (PPTX)</option>
                    </select>
                  </label>
                  <label className="grid gap-1">
                    Style template
                    <select
                      aria-label="Style template"
                      disabled={templateLoading}
                      value={templateId}
                      onChange={(event) => {
                        setTemplateId(event.target.value);
                        if (ownerId)
                          remember(
                            localKey("template", ownerId, draft.id),
                            event.target.value || "pandoc",
                          );
                      }}
                    >
                      <option value="">Pandoc default</option>
                      {templates
                        .filter((item) => item.current_version_id)
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name}
                          </option>
                        ))}
                    </select>
                  </label>
                </div>
                {output === "pptx" && (
                  <div className="flex flex-wrap gap-3">
                    <label className="grid gap-1">
                      Markdown format
                      <select
                        value={dialect}
                        onChange={(event) => {
                          const next = event.target.value as
                            | "auto"
                            | "markdown"
                            | "marp";
                          setDialect(next);
                          if (ownerId)
                            remember(
                              localKey("dialect", ownerId, draft.id),
                              next,
                            );
                        }}
                      >
                        <option value="auto">Detect Markdown or Marp</option>
                        <option value="markdown">Markdown</option>
                        <option value="marp">Marp</option>
                      </select>
                    </label>
                    <label className="grid gap-1">
                      Slide heading level
                      <select
                        value={slideLevel}
                        onChange={(event) => {
                          const next = Number(event.target.value);
                          setSlideLevel(next);
                          if (ownerId)
                            remember(
                              localKey("slide-level", ownerId, draft.id),
                              String(next),
                            );
                        }}
                      >
                        {[1, 2, 3, 4, 5, 6].map((level) => (
                          <option key={level} value={level}>
                            {"#".repeat(level)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                )}
                {templateError && (
                  <div role="alert">
                    <p>
                      Templates could not be loaded. Generation is paused to
                      preserve your selected style.
                    </p>
                    <button
                      type="button"
                      onClick={() => setTemplateRefresh((value) => value + 1)}
                    >
                      Retry templates
                    </button>
                  </div>
                )}
                <button
                  type="button"
                  disabled={
                    busy ||
                    templateLoading ||
                    Boolean(templateError) ||
                    !draftTitle.trim() ||
                    (generation !== null &&
                      !generationSettled.has(generation.status) &&
                      generation.result_revision_id === null)
                  }
                  onClick={() => void generateDocument()}
                >
                  {busy ? "Preparing…" : `Generate ${output.toUpperCase()}`}
                </button>
                {generation && generation.draft_id === draft.id && (
                  <details
                    className="rounded-control border border-muted p-3"
                    open={
                      !generationSettled.has(generation.status) ||
                      (generation.status === "succeeded" &&
                        generation.publishable &&
                        (draftContent !== draft.content ||
                          draftTitle !== draft.title))
                    }
                  >
                    <summary>
                      Generation · {generation.output.toUpperCase()} ·{" "}
                      {generation.status}
                    </summary>
                    <p>Source revision: {generation.source_revision_id}</p>
                    {generation.result_revision_id && (
                      <p>Ready revision: {generation.result_revision_id}</p>
                    )}
                    {generation.result_revision_id &&
                      revision?.id !== generation.result_revision_id && (
                        <button
                          type="button"
                          onClick={() =>
                            void load(draft.id, generation.result_revision_id!)
                          }
                        >
                          Open generated revision
                        </button>
                      )}
                    {generation.status === "succeeded" &&
                      !generation.publishable &&
                      !generation.result_revision_id && (
                        <p>
                          The draft changed during generation. Review its
                          current content before generating again.
                        </p>
                      )}
                    {generation.status === "succeeded" &&
                      generation.publishable &&
                      (draftContent !== draft.content ||
                        draftTitle !== draft.title) && (
                        <p>
                          You have unsaved document edits. Save and generate
                          again after review; this result will not replace them.
                        </p>
                      )}
                    {!generationSettled.has(generation.status) && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void mutate(async (stillSelected) => {
                            const cancelled = await api.cancelGeneration(
                              draft.id,
                              generation.id,
                            );
                            if (stillSelected()) setGeneration(cancelled);
                          }, "Generation cancellation requested.")
                        }
                      >
                        Cancel generation
                      </button>
                    )}
                    {generation.status === "succeeded" &&
                      generation.publishable &&
                      !generation.result_revision_id &&
                      draftContent === draft.content &&
                      draftTitle === draft.title && (
                        <button
                          type="button"
                          onClick={() => {
                            const selectedAtStart = selectionCounter.current;
                            void api
                              .generation(draft.id, generation.id)
                              .then((result) => {
                                if (
                                  selectedId.current === draft.id &&
                                  selectionCounter.current === selectedAtStart
                                )
                                  setGeneration(result);
                              })
                              .catch((failure) => {
                                if (expireIfUnauthorized(failure)) return;
                                if (
                                  selectedId.current === draft.id &&
                                  selectionCounter.current === selectedAtStart
                                )
                                  setError(safeError(failure));
                              });
                          }}
                        >
                          Retry publication
                        </button>
                      )}
                  </details>
                )}
              </div>
            )}
            <label className="grid gap-1">
              History
              <select
                aria-label="Revision history"
                value={revision?.id ?? ""}
                onChange={(event) => void chooseRevision(event.target.value)}
              >
                <option value="">No revision selected</option>
                {view.revisions.map((item) => (
                  <option key={item.id} value={item.id}>
                    Revision {item.number} · {item.operation}
                  </option>
                ))}
              </select>
            </label>
            {pages.revisions.hasMore && (
              <button
                type="button"
                disabled={loadingMore !== null}
                onClick={() => void loadMore("revisions")}
              >
                {loadingMore === "revisions"
                  ? "Loading revisions…"
                  : "Load older revisions"}
              </button>
            )}
            {revision ? (
              <>
                <p>
                  Selected revision {revision.number} · {revision.id} ·{" "}
                  {revision.provenance}
                </p>
                {format &&
                  activePreview?.draftId === draft.id &&
                  (activePreview.revisionId === revision.id ? (
                    <p role="status">
                      Displaying {activePreview.format.toUpperCase()} revision{" "}
                      {activePreview.revisionId}.
                    </p>
                  ) : (
                    <p role="status">
                      Displaying {activePreview.format.toUpperCase()} revision{" "}
                      {activePreview.revisionId}. Selected revision{" "}
                      {revision.id} is not displayed.
                    </p>
                  ))}
                {format ? (
                  <ComposerPreview
                    draftId={draft.id}
                    revisionId={revision.id}
                    format={format}
                    previewSha256={previewArtifact?.sha256}
                    previewUrl={artifactUrl(draft.id, revision.id, "preview")}
                    downloadUrl={artifactUrl(draft.id, revision.id, "download")}
                    onActiveRevisionChange={onActiveRevisionChange}
                    onUnauthorized={() => {
                      if (currentOwner()) auth.expire();
                    }}
                    onDownload={downloadActiveRevision}
                  />
                ) : activeTextPreview !== null ? (
                  <pre className="max-h-[40rem] overflow-auto rounded-control border border-muted p-3 whitespace-pre-wrap">
                    {activeTextPreview}
                  </pre>
                ) : (
                  <p>
                    Native preview is unavailable for this source type. Download
                    the exact revision to inspect it.
                  </p>
                )}
                <div className="flex flex-wrap gap-3">
                  {!format && (
                    <button
                      type="button"
                      className="text-accent underline"
                      onClick={() => {
                        const mediaType = revision.artifacts.find(
                          (item) => item.kind === "download",
                        )?.media_type;
                        void downloadRevision(
                          draft.id,
                          revision.id,
                          fileFormat(mediaType ?? "") ??
                            (mediaType === "application/zip"
                              ? "zip"
                              : mediaType === "text/markdown"
                                ? "md"
                                : "bin"),
                        );
                      }}
                    >
                      Download revision {revision.number}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void mutate(
                        () =>
                          api.restore(draft, revision.id, crypto.randomUUID()),
                        "A copy-forward revision was created.",
                      )
                    }
                  >
                    Restore as new revision
                  </button>
                </div>
                <details className="rounded-control border border-muted p-3">
                  <summary>Changes and source details</summary>
                  <p>Operation: {revision.operation}</p>
                  <p>Source SHA-256: {revision.source_sha256}</p>
                  <p>
                    Restored from:{" "}
                    {revision.restored_from_revision_id ?? "None"}
                  </p>
                  {activeDiff && (
                    <div
                      aria-label="Semantic changes"
                      className="mt-3 space-y-2"
                    >
                      <h3 className="font-semibold">
                        {activeDiff.scope === "approved_markdown"
                          ? "Approved Markdown changes from previous revision"
                          : "Artifact text changes from previous revision"}
                      </h3>
                      {activeDiff.status === "unavailable" && (
                        <p>
                          {activeDiff.reason ??
                            "A semantic comparison is unavailable for this file type."}
                        </p>
                      )}
                      {activeDiff.status === "unchanged" && (
                        <p>No semantic text change.</p>
                      )}
                      {activeDiff.metadata_changes.length > 0 && (
                        <div>
                          <p>Revision settings changed:</p>
                          <ul className="list-disc pl-5">
                            {activeDiff.metadata_changes.map((change) => (
                              <li key={change}>{change}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {activeDiff.changes.map((change, index) => (
                        <div
                          key={`${change.before_start}-${change.after_start}-${index}`}
                          className="border-l-2 border-accent pl-2"
                        >
                          <p>
                            {change.kind}: lines {change.before_start}–
                            {change.before_end} to {change.after_start}–
                            {change.after_end}
                          </p>
                          <del className="block whitespace-pre-wrap">
                            {change.before_text}
                          </del>
                          <ins className="block whitespace-pre-wrap">
                            {change.after_text}
                          </ins>
                        </div>
                      ))}
                    </div>
                  )}
                </details>
              </>
            ) : (
              <p>
                No preview revision yet. Capture the source to establish an
                exact artifact pair.
              </p>
            )}
          </section>
        </div>
      )}
    </AppShell>
  );
}
