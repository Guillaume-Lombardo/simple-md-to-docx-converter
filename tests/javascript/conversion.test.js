import assert from "node:assert/strict";
import test from "node:test";

import {
  createConversionController,
  nextPollDelay,
  readCookie,
  responseError,
  statusPresentation,
  validSource,
} from "../../src/md_converter/static/conversion.js";

class FakeClassList {
  values = new Set();
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
}

class FakeElement {
  constructor() {
    this.listeners = new Map();
    this.children = [];
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.textContent = "";
    this.value = "";
    this.files = [];
    this.classList = new FakeClassList();
    this.parts = new Map();
  }
  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }
  appendChild(child) { this.children.push(child); }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  get firstChild() { return this.children[0] || null; }
  querySelector(selector) { return this.parts.get(selector); }
  async dispatch(name, extra = {}) {
    const event = { preventDefault() {}, ...extra };
    for (const listener of this.listeners.get(name) || []) await listener(event);
  }
}

class FakeDocument {
  constructor({ withForm = true } = {}) {
    this.cookie = "__Host-md_converter_csrf=csrf%20value; other=x";
    this.body = { dataset: { maxUploadBytes: "100" } };
    this.elements = new Map();
    const selectors = [
      "#source", "#drop-zone", "#template-search", "#template-results",
      "#selected-template", "#submit-conversion", "#page-alert", "#job-status",
      "#job-progress", "#cancel-job", "#download-result",
    ];
    if (withForm) selectors.push("#conversion-form");
    for (const selector of selectors) this.elements.set(selector, new FakeElement());
    if (withForm) this.elements.get("#conversion-form").outputValue = "docx";
    const selected = this.elements.get("#selected-template");
    if (selected) {
      selected.parts.set("span", new FakeElement());
      selected.parts.set("strong", new FakeElement());
    }
    this.jobLinks = [];
    this.outputs = [new FakeElement(), new FakeElement(), new FakeElement()];
  }
  querySelector(selector) { return this.elements.get(selector) || null; }
  querySelectorAll(selector) {
    if (selector === ".job-link") return this.jobLinks;
    if (selector === 'input[name="output"]') return this.outputs;
    return [];
  }
  createElement() { return new FakeElement(); }
}

class FakeFormData {
  constructor(form) { this.form = form; this.values = []; }
  append(name, value) { this.values.push([name, value]); }
  get(name) { return name === "output" ? this.form.outputValue : null; }
}

function response(status, body, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      if (body instanceof Error) throw body;
      return body;
    },
    headers: { get: (name) => headers[name] || null },
  };
}

function job(overrides = {}) {
  return {
    id: "job-id", state: "queued", step: "queued", progress: 0,
    cancel_requested: false, error_message: null, ...overrides,
  };
}

function harness(fetchResponses = []) {
  const doc = new FakeDocument();
  const requests = [];
  const scheduled = [];
  const cancelled = [];
  const fetch = async (...args) => {
    requests.push(args);
    const next = fetchResponses.shift();
    if (next instanceof Error) throw next;
    return next;
  };
  let keyNumber = 0;
  const controller = createConversionController(doc, {
    fetch,
    setTimeout(callback, delay) { scheduled.push({ callback, delay }); return scheduled.length; },
    clearTimeout(id) { cancelled.push(id); },
    randomUUID: () => `key-${++keyNumber}`,
    FormData: FakeFormData,
  });
  return { controller, doc, requests, scheduled, cancelled };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, reject, resolve };
}

test("pure browser helpers validate cookies, files, polling, states, and safe errors", async () => {
  assert.equal(readCookie("a=1; csrf=hello%20world", "csrf"), "hello world");
  assert.equal(readCookie("a=1", "csrf"), null);
  assert.equal(validSource(null, 5), "Choose a Markdown or ZIP file.");
  assert.match(validSource({ name: "x.txt", size: 1 }, 5), /ending/);
  assert.match(validSource({ name: "x.md", size: 0 }, 5), /empty/);
  assert.match(validSource({ name: "x.ZIP", size: 6 }, 5), /limit/);
  assert.equal(validSource({ name: "x.MD", size: 5 }, 5), null);
  assert.equal(nextPollDelay(1000), 1600);
  assert.equal(nextPollDelay(9000), 10000);
  assert.deepEqual(statusPresentation(job()), { message: "Your conversion is queued.", terminal: false });
  assert.match(statusPresentation(job({ state: "running", step: "pdf", progress: 70 })).message, /PDF/);
  assert.match(statusPresentation(job({ state: "running", step: "unknown", progress: 5 })).message, /Processing/);
  assert.equal(statusPresentation(job({ state: "failed", error_message: "Safe failure" })).message, "Safe failure");
  assert.match(statusPresentation(job({ state: "failed" })).message, /failed/);
  assert.match(statusPresentation(job({ state: "cancelled" })).message, /cancelled/);
  assert.match(statusPresentation(job({ state: "expired" })).message, /expired/);
  assert.match(statusPresentation(job({ state: "succeeded" })).message, /ready/);
  assert.match(statusPresentation(job({ cancel_requested: true })).message, /Cancellation requested/);
  assert.match(await responseError(response(401, {})), /session/);
  assert.equal(await responseError(response(422, { error: { message: "Safe" } })), "Safe");
  assert.match(await responseError(response(500, new Error("invalid json"))), /could not/);
  assert.match(await responseError(response(500, {})), /could not/);
  assert.equal(createConversionController(new FakeDocument({ withForm: false })), null);
});

test("template search renders empty, failure, and safe selectable results", async () => {
  const found = { id: "template", current_version_id: "version", name: "Clean", description: "" };
  const h = harness([
    response(200, { items: [] }),
    response(503, { error: { message: "Storage unavailable." } }),
    response(200, { items: [found] }),
    response(200, { items: [] }),
    new Error("offline"),
  ]);
  await h.controller.searchTemplates();
  assert.match(h.doc.querySelector("#template-results").children[0].textContent, /No active/);
  await h.controller.searchTemplates();
  assert.equal(h.doc.querySelector("#page-alert").textContent, "Storage unavailable.");
  h.doc.querySelector("#template-search").value = "Clean";
  await h.controller.searchTemplates();
  const button = h.doc.querySelector("#template-results").children[0].children[0];
  assert.match(button.textContent, /No description/);
  await button.dispatch("click");
  assert.equal(h.doc.querySelector("#selected-template").dataset.versionId, "version");
  assert.equal(h.doc.querySelector("#submit-conversion").disabled, false);
  await h.doc.querySelector("#template-search").dispatch("input");
  await h.doc.querySelector("#template-search").dispatch("input");
  assert.equal(h.scheduled.at(-1).delay, 250);
  assert.ok(h.cancelled.length > 0);
  await h.scheduled.at(-1).callback();
  await h.controller.searchTemplates();
  assert.match(h.doc.querySelector("#page-alert").textContent, /could not be loaded/);
});

test("submission renews acknowledged keys and reuses only ambiguous requests", async () => {
  const h = harness([
    new Error("network"),
    response(202, job({ id: "job-1" }), { "Retry-After": "2" }),
    response(200, job({ id: "job-1", state: "succeeded", step: "complete", progress: 100 })),
    response(202, job({ id: "job-2" }), { "Retry-After": "1" }),
    response(200, job({ id: "job-2", state: "succeeded", step: "complete", progress: 100 })),
    new Error("network"),
    response(202, job({ id: "job-3", output: "pdf" })),
    response(422, { error: { message: "Confirmed rejection." } }),
  ]);
  const source = h.doc.querySelector("#source");
  const selected = h.doc.querySelector("#selected-template");
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  assert.match(h.doc.querySelector("#page-alert").textContent, /Choose/);
  source.files = [{ name: "bad.txt", size: 1 }];
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  source.files = [{ name: "source.md", size: 10 }];
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  assert.match(h.doc.querySelector("#page-alert").textContent, /active template/);
  selected.dataset.templateId = "template";
  selected.dataset.versionId = "version";
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  assert.match(h.doc.querySelector("#page-alert").textContent, /same request key/);
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  assert.equal(h.scheduled.at(-1).delay, 2000);
  await h.scheduled.at(-1).callback();
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  await h.scheduled.at(-1).callback();
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  assert.match(h.doc.querySelector("#page-alert").textContent, /same request key/);
  h.doc.querySelector("#conversion-form").outputValue = "pdf";
  await h.doc.outputs[1].dispatch("change");
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  assert.equal(h.doc.querySelector("#page-alert").textContent, "Confirmed rejection.");
  const keys = h.requests.map(([, options]) => options?.headers?.["Idempotency-Key"]).filter(Boolean);
  assert.deepEqual(keys, ["key-1", "key-1", "key-2", "key-3", "key-4", "key-5"]);
  await source.dispatch("change");
});

test("polling progresses, exposes downloads, reports errors, and cancels", async () => {
  const h = harness([
    response(200, job({ state: "running", step: "validating", progress: 20 })),
    response(200, job({ state: "succeeded", step: "complete", progress: 100 })),
    response(404, { error: { message: "The conversion was not found." } }),
    response(200, job({ state: "running", step: "docx", progress: 50 })),
    response(200, job({ state: "cancelled" })),
    response(200, job({ state: "running", step: "docx", progress: 50 })),
    response(503, { error: { message: "Cancellation unavailable." } }),
    response(503, { error: { message: "Status unavailable." } }),
    response(200, job({ state: "succeeded", step: "complete", progress: 100 })),
    new Error("offline"),
    response(200, job({ state: "running", step: "pdf", progress: 75 })),
    response(200, job({ state: "succeeded", step: "complete", progress: 100 })),
    new Error("offline"),
  ]);
  h.doc.querySelector("#selected-template").dataset.versionId = "version";
  await h.controller.pollJob("job-id");
  assert.equal(h.scheduled.at(-1).delay, 1600);
  await h.controller.pollJob("job-id");
  assert.equal(h.doc.querySelector("#download-result").href, "/api/v1/conversions/job-id/result");
  assert.equal(h.doc.querySelector("#download-result").hidden, false);
  await h.controller.pollJob("missing");
  assert.match(h.doc.querySelector("#page-alert").textContent, /not found/);
  await h.controller.pollJob("job-id");
  await h.controller.cancelJob();
  assert.equal(h.doc.querySelector("#cancel-job").hidden, true);
  await h.controller.pollJob("job-id");
  await h.controller.cancelJob();
  assert.equal(h.doc.querySelector("#page-alert").textContent, "Cancellation unavailable.");
  await h.controller.pollJob("job-id");
  assert.equal(h.doc.querySelector("#page-alert").textContent, "Status unavailable.");
  await h.scheduled.at(-1).callback();
  await h.controller.pollJob("job-id");
  assert.match(h.doc.querySelector("#page-alert").textContent, /Polling will continue/);
  await h.scheduled.at(-1).callback();
  await h.scheduled.at(-1).callback();
  await h.controller.cancelJob();
  assert.match(h.doc.querySelector("#page-alert").textContent, /could not be requested/);
});

test("accepted jobs are immediately cancellable and requested cancellation keeps polling", async () => {
  const immediate = harness([
    response(202, job({ id: "accepted" }), { "Retry-After": "1" }),
    response(200, job({ id: "accepted", state: "cancelled" })),
  ]);
  immediate.doc.querySelector("#source").files = [{ name: "source.md", size: 10 }];
  immediate.doc.querySelector("#selected-template").dataset.templateId = "template";
  immediate.doc.querySelector("#selected-template").dataset.versionId = "version";
  await immediate.doc.querySelector("#conversion-form").dispatch("submit");
  await immediate.controller.cancelJob();
  assert.equal(immediate.requests[1][0], "/api/v1/conversions/accepted");

  const requested = harness([
    response(200, job({ id: "requested" })),
    response(200, job({ id: "requested", cancel_requested: true })),
    response(200, job({ id: "requested", state: "running", step: "docx", progress: 50, cancel_requested: true })),
    response(200, job({ id: "requested", state: "cancelled" })),
  ]);
  await requested.controller.pollJob("requested");
  await requested.controller.cancelJob();
  assert.match(requested.doc.querySelector("#job-status").textContent, /Cancellation requested/);
  assert.equal(requested.doc.querySelector("#cancel-job").hidden, true);
  await requested.scheduled.at(-1).callback();
  assert.match(requested.doc.querySelector("#job-status").textContent, /Cancellation requested/);
  await requested.scheduled.at(-1).callback();
  assert.match(requested.doc.querySelector("#job-status").textContent, /cancelled/);
});

test("late template searches cannot replace newer results", async () => {
  const first = deferred();
  const second = deferred();
  const doc = new FakeDocument();
  const requests = [];
  const controller = createConversionController(doc, {
    fetch(url, options) {
      requests.push({ options, url });
      return url.includes("name=First") ? first.promise : second.promise;
    },
    setTimeout() { return 1; }, clearTimeout() {}, randomUUID: () => "key",
    FormData: FakeFormData,
  });
  doc.querySelector("#template-search").value = "First";
  const firstSearch = controller.searchTemplates();
  doc.querySelector("#template-search").value = "Second";
  const secondSearch = controller.searchTemplates();
  second.resolve(response(200, { items: [{ id: "second", current_version_id: "v2", name: "Second", description: "Newest" }] }));
  await secondSearch;
  first.resolve(response(200, { items: [{ id: "first", current_version_id: "v1", name: "First", description: "Stale" }] }));
  await firstSearch;
  const rendered = doc.querySelector("#template-results").children[0].children[0].textContent;
  assert.match(rendered, /Second/);
  assert.doesNotMatch(rendered, /First/);
  assert.equal(requests[0].options.signal.aborted, true);
});

test("late job and cancellation responses cannot overwrite a newer active job", async () => {
  const pollA = deferred();
  const pollB = deferred();
  const cancelA = deferred();
  const pollCancelA = deferred();
  const doc = new FakeDocument();
  const scheduled = [];
  const fetch = (url, options = {}) => {
    if (url.endsWith("/job-a")) return pollA.promise;
    if (url.endsWith("/job-b")) return pollB.promise;
    if (url.endsWith("/cancel-a") && options.method === "DELETE") return cancelA.promise;
    if (url.endsWith("/cancel-a")) return pollCancelA.promise;
    if (url.endsWith("/new-b")) return Promise.resolve(response(200, job({ id: "new-b", state: "running", step: "pdf", progress: 75 })));
    throw new Error(`unexpected request ${url}`);
  };
  const controller = createConversionController(doc, {
    fetch,
    setTimeout(callback, delay) { scheduled.push({ callback, delay }); return scheduled.length; },
    clearTimeout() {}, randomUUID: () => "key", FormData: FakeFormData,
  });

  const firstPoll = controller.pollJob("job-a");
  const secondPoll = controller.pollJob("job-b");
  pollB.resolve(response(200, job({ id: "job-b", state: "running", step: "validating", progress: 20 })));
  await secondPoll;
  pollA.resolve(response(200, job({ id: "job-a", state: "succeeded", step: "complete", progress: 100 })));
  await firstPoll;
  assert.match(doc.querySelector("#job-status").textContent, /Validating/);
  assert.equal(doc.querySelector("#download-result").hidden, true);

  const oldPoll = controller.pollJob("cancel-a");
  pollCancelA.resolve(response(200, job({ id: "cancel-a", state: "running", step: "docx", progress: 50 })));
  await oldPoll;
  const oldCancel = controller.cancelJob();
  await controller.pollJob("new-b");
  const activeTimer = scheduled.at(-1);
  cancelA.resolve(response(200, job({ id: "cancel-a", state: "cancelled" })));
  await oldCancel;
  assert.match(doc.querySelector("#job-status").textContent, /PDF/);
  assert.equal(scheduled.at(-1), activeTimer);
});

test("drag and recent-job interactions are accessible", async () => {
  const doc = new FakeDocument();
  const recent = new FakeElement();
  recent.dataset.jobId = "recent";
  doc.jobLinks.push(recent);
  const scheduled = [];
  createConversionController(doc, {
    fetch: async () => response(200, job({ id: "recent", state: "succeeded", progress: 100 })),
    setTimeout(callback, delay) { scheduled.push({ callback, delay }); return 1; },
    clearTimeout() {}, randomUUID: () => "key", FormData: FakeFormData,
  });
  const drop = doc.querySelector("#drop-zone");
  await drop.dispatch("dragenter");
  assert.ok(drop.classList.values.has("dragging"));
  await drop.dispatch("dragleave");
  assert.equal(drop.classList.values.has("dragging"), false);
  await drop.dispatch("drop", { dataTransfer: { files: [{ name: "a.md", size: 1 }] } });
  assert.equal(doc.querySelector("#source").files.length, 1);
  await drop.dispatch("drop", { dataTransfer: { files: [] } });
  assert.match(doc.querySelector("#page-alert").textContent, /exactly one/);
  await drop.dispatch("drop", { dataTransfer: null });
  await recent.dispatch("click");
  assert.match(doc.querySelector("#job-status").textContent, /ready/);
});

test("missing CSRF and cancellation targets take safe empty paths", async () => {
  const h = harness([response(202, job({ id: "without-csrf" }))]);
  h.doc.cookie = "";
  await h.controller.cancelJob();
  h.doc.querySelector("#source").files = [{ name: "source.md", size: 1 }];
  h.doc.querySelector("#selected-template").dataset.templateId = "template";
  h.doc.querySelector("#selected-template").dataset.versionId = "version";
  await h.doc.querySelector("#conversion-form").dispatch("submit");
  assert.equal("X-CSRF-Token" in h.requests[0][1].headers, false);
});
