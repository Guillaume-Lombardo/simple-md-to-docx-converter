const TERMINAL_STATES = new Set(["succeeded", "failed", "cancelled", "expired"]);
const STEP_LABELS = {
  queued: "Waiting for a worker",
  validating: "Validating the package",
  rendering: "Rendering diagrams and images",
  docx: "Creating the DOCX file",
  pdf: "Creating the PDF file",
  publishing: "Publishing the result",
  complete: "Complete",
};

export function readCookie(cookieText, name) {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of cookieText.split(";")) {
    const value = part.trim();
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
  }
  return null;
}

export function validSource(file, maximumBytes) {
  if (!file) return "Choose a Markdown or ZIP file.";
  if (!/\.(md|zip)$/i.test(file.name)) return "Choose a file ending in .md or .zip.";
  if (file.size < 1) return "The selected file is empty.";
  if (file.size > maximumBytes) return "The selected file exceeds the configured upload limit.";
  return null;
}

export function nextPollDelay(currentMilliseconds) {
  return Math.min(Math.ceil(currentMilliseconds * 1.6), 10000);
}

export function statusPresentation(job) {
  const state = job.state;
  const step = STEP_LABELS[job.step] || "Processing";
  if (state === "failed") return { message: job.error_message || "The conversion failed.", terminal: true };
  if (state === "cancelled") return { message: "The conversion was cancelled.", terminal: true };
  if (state === "expired") return { message: "This conversion has expired and its files are no longer available.", terminal: true };
  if (state === "succeeded") return { message: "Your conversion is ready to download.", terminal: true };
  if (state === "queued") return { message: "Your conversion is queued.", terminal: false };
  return { message: `${step} (${job.progress}%).`, terminal: false };
}

export async function responseError(response) {
  if (response.status === 401) return "Your session has expired. Sign in again.";
  try {
    const body = await response.json();
    if (body && body.error && typeof body.error.message === "string") return body.error.message;
  } catch {
    // A stable generic error is safer than reflecting an unexpected response body.
  }
  return "The request could not be completed. Try again.";
}

function replaceChildren(element) {
  while (element.firstChild) element.removeChild(element.firstChild);
}

export function createConversionController(doc, dependencies = {}) {
  const fetchRequest = dependencies.fetch || globalThis.fetch.bind(globalThis);
  const schedule = dependencies.setTimeout || globalThis.setTimeout.bind(globalThis);
  const cancelSchedule = dependencies.clearTimeout || globalThis.clearTimeout.bind(globalThis);
  const newKey = dependencies.randomUUID || (() => globalThis.crypto.randomUUID());
  const FormDataClass = dependencies.FormData || globalThis.FormData;
  const form = doc.querySelector("#conversion-form");
  if (!form) return null;
  const source = doc.querySelector("#source");
  const dropZone = doc.querySelector("#drop-zone");
  const search = doc.querySelector("#template-search");
  const results = doc.querySelector("#template-results");
  const selected = doc.querySelector("#selected-template");
  const submit = doc.querySelector("#submit-conversion");
  const alert = doc.querySelector("#page-alert");
  const status = doc.querySelector("#job-status");
  const progress = doc.querySelector("#job-progress");
  const cancel = doc.querySelector("#cancel-job");
  const download = doc.querySelector("#download-result");
  const maximumBytes = Number(doc.body.dataset.maxUploadBytes);
  let pollTimer = null;
  let searchTimer = null;
  let currentJobId = null;
  let currentDelay = 1000;
  let submissionKey = null;

  function showError(message) {
    alert.textContent = message;
    alert.hidden = false;
  }

  function clearError() {
    alert.textContent = "";
    alert.hidden = true;
  }

  function csrfHeaders() {
    const token = readCookie(doc.cookie, "__Host-md_converter_csrf");
    return token ? { "X-CSRF-Token": token } : {};
  }

  function chooseTemplate(template, label = "Selected template") {
    selected.dataset.templateId = template.id;
    selected.dataset.versionId = template.current_version_id || "";
    selected.querySelector("span").textContent = label;
    selected.querySelector("strong").textContent = template.name;
    submit.disabled = !template.current_version_id;
    replaceChildren(results);
    submissionKey = null;
  }

  async function searchTemplates() {
    const query = search.value.trim();
    const parameters = new URLSearchParams({ status: "active", limit: "20" });
    if (query) parameters.set("name", query);
    let response;
    try {
      response = await fetchRequest(`/api/v1/templates?${parameters}`);
    } catch {
      showError("Templates could not be loaded. Try the search again.");
      return;
    }
    if (!response.ok) {
      showError(await responseError(response));
      return;
    }
    const page = await response.json();
    replaceChildren(results);
    if (page.items.length === 0) {
      const item = doc.createElement("li");
      item.textContent = "No active templates match your search.";
      results.appendChild(item);
      return;
    }
    for (const template of page.items) {
      const item = doc.createElement("li");
      const button = doc.createElement("button");
      button.type = "button";
      button.textContent = `${template.name} — ${template.description || "No description"}`;
      button.addEventListener("click", () => chooseTemplate(template));
      item.appendChild(button);
      results.appendChild(item);
    }
  }

  function renderJob(job) {
    const view = statusPresentation(job);
    status.textContent = view.message;
    progress.value = job.progress;
    progress.textContent = `${job.progress}%`;
    progress.hidden = false;
    const cancellable = !TERMINAL_STATES.has(job.state) && !job.cancel_requested;
    cancel.hidden = !cancellable;
    cancel.disabled = !cancellable;
    download.hidden = job.state !== "succeeded";
    if (job.state === "succeeded") download.href = `/api/v1/conversions/${job.id}/result`;
    return view.terminal;
  }

  async function pollJob(jobId) {
    if (pollTimer !== null) cancelSchedule(pollTimer);
    currentJobId = jobId;
    let response;
    try {
      response = await fetchRequest(`/api/v1/conversions/${jobId}`);
    } catch {
      showError("Status is temporarily unavailable. Polling will continue.");
      currentDelay = nextPollDelay(currentDelay);
      pollTimer = schedule(() => pollJob(jobId), currentDelay);
      return;
    }
    if (!response.ok) {
      showError(await responseError(response));
      if (response.status !== 401 && response.status !== 404) {
        currentDelay = nextPollDelay(currentDelay);
        pollTimer = schedule(() => pollJob(jobId), currentDelay);
      }
      return;
    }
    clearError();
    const job = await response.json();
    if (!renderJob(job)) {
      currentDelay = nextPollDelay(currentDelay);
      pollTimer = schedule(() => pollJob(jobId), currentDelay);
    } else {
      pollTimer = null;
      submit.disabled = !selected.dataset.versionId;
    }
  }

  async function submitConversion(event) {
    event.preventDefault();
    clearError();
    const file = source.files && source.files[0];
    const invalid = validSource(file, maximumBytes);
    if (invalid) {
      showError(invalid);
      return;
    }
    if (!selected.dataset.versionId) {
      showError("Choose an active template before starting the conversion.");
      return;
    }
    submissionKey ||= newKey();
    const data = new FormDataClass();
    data.append("source", file);
    data.append("template_id", selected.dataset.templateId);
    data.append("template_version_id", selected.dataset.versionId);
    data.append("output", new FormDataClass(form).get("output"));
    submit.disabled = true;
    status.textContent = "Submitting your conversion…";
    let response;
    try {
      response = await fetchRequest("/api/v1/conversions", {
        method: "POST",
        headers: { ...csrfHeaders(), "Idempotency-Key": submissionKey },
        body: data,
      });
    } catch {
      submit.disabled = false;
      showError("The conversion could not be submitted. Retrying will reuse the same request key.");
      return;
    }
    if (!response.ok) {
      submit.disabled = false;
      showError(await responseError(response));
      return;
    }
    const job = await response.json();
    const retryAfter = Number(response.headers.get("Retry-After"));
    currentDelay = Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter * 1000 : 1000;
    renderJob(job);
    pollTimer = schedule(() => pollJob(job.id), currentDelay);
  }

  async function cancelJob() {
    if (!currentJobId) return;
    cancel.disabled = true;
    let response;
    try {
      response = await fetchRequest(`/api/v1/conversions/${currentJobId}`, {
        method: "DELETE",
        headers: csrfHeaders(),
      });
    } catch {
      cancel.disabled = false;
      showError("Cancellation could not be requested. Try again.");
      return;
    }
    if (!response.ok) {
      cancel.disabled = false;
      showError(await responseError(response));
      return;
    }
    clearError();
    renderJob(await response.json());
    if (pollTimer !== null) cancelSchedule(pollTimer);
    pollTimer = null;
  }

  form.addEventListener("submit", submitConversion);
  cancel.addEventListener("click", cancelJob);
  search.addEventListener("input", () => {
    if (searchTimer !== null) cancelSchedule(searchTimer);
    searchTimer = schedule(searchTemplates, 250);
  });
  source.addEventListener("change", () => { submissionKey = null; });
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove("dragging");
    });
  }
  dropZone.addEventListener("drop", (event) => {
    const files = event.dataTransfer && event.dataTransfer.files;
    if (files && files.length === 1) {
      source.files = files;
      submissionKey = null;
    } else showError("Drop exactly one Markdown or ZIP file.");
  });
  for (const button of doc.querySelectorAll(".job-link")) {
    button.addEventListener("click", () => {
      currentDelay = 1000;
      pollJob(button.dataset.jobId);
    });
  }

  return { cancelJob, chooseTemplate, pollJob, searchTemplates, submitConversion };
}

/* node:coverage ignore next 3 */
if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => createConversionController(document));
}
