import assert from "node:assert/strict";
import test from "node:test";

import {
  createAdministrationController,
  splitFonts,
  templateEtag,
  templateMatches,
  userMatches,
  validTemplateFile,
  validTemplatePage,
  validUsers,
  validVersions,
} from "../../src/md_converter/static/administration.js";

class FakeClassList {
  values = new Set();
  toggle(value, enabled) { enabled ? this.values.add(value) : this.values.delete(value); }
  remove(value) { this.values.delete(value); }
}

class FakeElement {
  constructor(ownerDocument, tag = "div") {
    this.ownerDocument = ownerDocument;
    this.tagName = tag.toUpperCase();
    this.listeners = new Map();
    this.children = [];
    this.dataset = {};
    this.classList = new FakeClassList();
    this.textContent = "";
    this.value = "";
    this.files = [];
    this.checked = false;
    this.hidden = false;
    this.disabled = false;
    this.name = "";
  }
  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  get firstChild() { return this.children[0] || null; }
  get elements() {
    const root = this;
    return new Proxy({}, { get(_target, name) { return descendants(root).find((child) => child.name === name); } });
  }
  reset() {
    for (const child of descendants(this)) {
      child.value = "";
      child.files = [];
    }
  }
  async dispatch(name, extra = {}) {
    const event = { preventDefault() {}, ...extra };
    for (const listener of this.listeners.get(name) || []) await listener(event);
  }
}

function descendants(element) {
  return element.children.flatMap((child) => [child, ...descendants(child)]);
}

function byText(element, text, tag = null) {
  return descendants(element).find((child) =>
    child.textContent === text && (!tag || child.tagName === tag.toUpperCase()));
}

function byClass(element, className) {
  return descendants(element).filter((child) => child.className?.split(" ").includes(className));
}

class FakeDocument {
  constructor(role = "admin") {
    this.cookie = "__Host-md_converter_csrf=csrf%20token";
    this.body = new FakeElement(this, "body");
    this.body.dataset = {
      userId: role === "admin" ? "admin-id" : "alice-id",
      userRole: role,
      preferredTemplateId: "",
      maxTemplateBytes: "1000",
    };
    this.nodes = new Map();
    for (const selector of [
      "#managed-template-list", "#administration-alert", "#template-filter",
      "#my-templates", "#create-template-form",
    ]) this.nodes.set(selector, new FakeElement(this, selector === "#create-template-form" ? "form" : "div"));
    if (role === "admin") {
      for (const selector of ["#user-list", "#user-search", "#create-user-form"])
        this.nodes.set(selector, new FakeElement(this, selector === "#create-user-form" ? "form" : "div"));
    }
    this.addCreateTemplateFields();
    if (role === "admin") this.addCreateUserFields();
  }
  addCreateTemplateFields() {
    const form = this.nodes.get("#create-template-form");
    for (const name of ["name", "description", "expected_fonts", "content"]) {
      const input = new FakeElement(this, "input");
      input.name = name;
      form.appendChild(input);
    }
  }
  addCreateUserFields() {
    const form = this.nodes.get("#create-user-form");
    for (const name of ["username", "password"]) {
      const input = new FakeElement(this, "input");
      input.name = name;
      form.appendChild(input);
    }
  }
  querySelector(selector) { return this.nodes.get(selector) || null; }
  createElement(tag) { return new FakeElement(this, tag); }
}

class FakeFormData {
  values = [];
  append(name, value) { this.values.push([name, value]); }
}

function response(status, body = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      if (body instanceof Error) throw body;
      return body;
    },
  };
}

function template(overrides = {}) {
  return {
    id: "template-1", owner_id: "admin-id", owner_username: "Admin",
    name: "Quarterly report", description: "Finance", status: "active",
    revision: 1, current_version_id: "version-2", ...overrides,
  };
}

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0));
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

test("administration helpers validate safe filtering and concurrency metadata", () => {
  const item = template();
  assert.deepEqual(splitFonts(" Liberation Serif, , Carlito "), ["Liberation Serif", "Carlito"]);
  assert.deepEqual(splitFonts(""), []);
  assert.equal(templateMatches(item, "finance", false, "other"), true);
  assert.equal(templateMatches(item, "admin", false, "other"), true);
  assert.equal(templateMatches(item, "missing", false, "other"), false);
  assert.equal(templateMatches(item, "", true, "other"), false);
  assert.equal(templateMatches(item, "", true, "admin-id"), true);
  assert.equal(userMatches({ username: "Alice" }, "ALI"), true);
  assert.equal(userMatches({ username: "Alice" }, "bob"), false);
  assert.equal(templateEtag(item), '"template-template-1-1"');
  assert.match(validTemplateFile(null, 1), /Choose/);
  assert.match(validTemplateFile({ name: "x.zip", size: 1 }, 2), /ending/);
  assert.match(validTemplateFile({ name: "x.docx", size: 0 }, 2), /empty/);
  assert.match(validTemplateFile({ name: "x.DOCX", size: 3 }, 2), /limit/);
  assert.equal(validTemplateFile({ name: "x.DOCX", size: 2 }, 2), null);
  assert.equal(validTemplatePage({ items: [item], total: 1 }), true);
  assert.equal(validTemplatePage({ items: [], total: -1 }), false);
  assert.equal(validTemplatePage({ items: [{}], total: 1 }), false);
  assert.equal(validUsers([{ id: "a", username: "A", role: "user", active: true }]), true);
  assert.equal(validUsers([{ id: "a", username: "A", role: "owner", active: true }]), false);
  assert.equal(validVersions([{ id: "v", number: 1, size: 1, created_at: "now" }]), true);
  assert.equal(validVersions([{ id: "v", number: "1", size: 1, created_at: "now" }]), false);
  assert.equal(createAdministrationController({ querySelector: () => null }), null);
});

test("administrator controller exercises template and account workflows", async () => {
  const doc = new FakeDocument();
  const requests = [];
  let templates = [template(), template({
    id: "template-2", owner_id: "alice-id", owner_username: "Alice",
    name: "Alice brief", description: "", current_version_id: "version-a",
  })];
  let users = [
    { id: "admin-id", username: "Admin", role: "admin", active: true },
    { id: "alice-id", username: "Alice", role: "user", active: true },
  ];
  const fetch = async (url, options = {}) => {
    requests.push([url, options]);
    const method = options.method || "GET";
    if (url === "/api/v1/templates?limit=100&offset=0") return response(200, { items: templates, total: templates.length });
    if (url === "/api/v1/admin/users" && method === "GET") return response(200, users);
    if (url.endsWith("/versions") && method === "GET") return response(200, [
      { id: "version-2", number: 2, size: 20, created_at: "now" },
      { id: "version-1", number: 1, size: 10, created_at: "before" },
    ]);
    if (url.endsWith("/preferred")) return response(204);
    if (url.endsWith("/archive")) {
      templates[0] = { ...templates[0], status: "archived", revision: 2 };
      return response(200);
    }
    if (url === "/api/v1/templates/template-1" && method === "DELETE") {
      templates = templates.slice(1);
      return response(204);
    }
    if (url === "/api/v1/templates" && method === "POST") {
      templates.push(template({ id: "created", name: "Created", revision: 1 }));
      return response(201);
    }
    if (url.includes("/active")) {
      users[1] = { ...users[1], active: !users[1].active };
      return response(200);
    }
    if (url === "/api/v1/admin/users" && method === "POST") {
      users.push({ id: "bob", username: "Bob", role: "user", active: true });
      return response(201);
    }
    return response(method === "DELETE" ? 204 : 200);
  };
  createAdministrationController(doc, {
    fetch, confirm: () => true, FormData: FakeFormData,
  });
  await settle();
  const list = doc.querySelector("#managed-template-list");
  assert.equal(byClass(list, "management-card").length, 2);

  doc.querySelector("#template-filter").value = "Alice";
  await doc.querySelector("#template-filter").dispatch("input");
  assert.equal(byClass(list, "management-card").length, 1);
  doc.querySelector("#template-filter").value = "";
  doc.querySelector("#my-templates").checked = true;
  await doc.querySelector("#my-templates").dispatch("change");
  assert.equal(byClass(list, "management-card").length, 1);
  doc.querySelector("#my-templates").checked = false;
  await doc.querySelector("#my-templates").dispatch("change");

  await byText(list, "Make preferred", "button").dispatch("click");
  assert.ok(requests.some(([url]) => url.endsWith("/preferred")));
  await byText(list, "Clear preferred", "button").dispatch("click");
  assert.ok(requests.some(([url]) => url === "/api/v1/template-preference"));
  const details = descendants(list).find((child) => child.tagName === "DETAILS");
  const forms = descendants(details).filter((child) => child.tagName === "FORM");
  const metadata = forms[0];
  metadata.elements.name.value = "Renamed";
  await metadata.dispatch("submit");
  const replacement = forms[1];
  replacement.elements.content.files = [{ name: "replacement.docx", size: 10 }];
  replacement.elements.expected_fonts.value = "Calibri, Cambria";
  await replacement.dispatch("submit");
  await byText(details, "Load version history", "button").dispatch("click");
  await byText(details, "Restore", "button").dispatch("click");

  await byText(list, "Archive", "button").dispatch("click");
  await byText(list, "Delete", "button").dispatch("click");
  assert.equal(templates.length, 1);

  const createTemplate = doc.querySelector("#create-template-form");
  createTemplate.elements.name.value = "Created";
  createTemplate.elements.description.value = "New";
  createTemplate.elements.expected_fonts.value = "";
  createTemplate.elements.content.files = [{ name: "created.docx", size: 5 }];
  await createTemplate.dispatch("submit");
  assert.ok(requests.some(([url, options]) => url === "/api/v1/templates" && options.method === "POST"));

  const userList = doc.querySelector("#user-list");
  doc.querySelector("#user-search").value = "missing";
  await doc.querySelector("#user-search").dispatch("input");
  assert.match(userList.children[0].textContent, /No users/);
  doc.querySelector("#user-search").value = "Alice";
  await doc.querySelector("#user-search").dispatch("input");
  await byText(userList, "Deactivate", "button").dispatch("click");
  const resetForm = descendants(userList).find((child) => child.className === "inline-form");
  resetForm.elements.password.value = "new-password";
  await resetForm.dispatch("submit");
  const createUser = doc.querySelector("#create-user-form");
  createUser.elements.username.value = "Bob";
  createUser.elements.password.value = "bob-password";
  await createUser.dispatch("submit");
  assert.equal(users.length, 3);
  assert.ok(requests.every(([, options]) => !options.headers?.["X-CSRF-Token"] || options.headers["X-CSRF-Token"] === "csrf token"));
});

test("regular controller hides mutations and reports stable failures and empty states", async () => {
  const doc = new FakeDocument("user");
  const replies = [response(200, { items: [], total: 0 }), new Error("offline")];
  createAdministrationController(doc, {
    fetch: async () => {
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return reply;
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  assert.match(doc.querySelector("#managed-template-list").children[0].textContent, /No templates/);
  const form = doc.querySelector("#create-template-form");
  await form.dispatch("submit");
  assert.match(doc.querySelector("#administration-alert").textContent, /Choose/);
  form.elements.content.files = [{ name: "valid.docx", size: 1 }];
  await form.dispatch("submit");
  assert.match(doc.querySelector("#administration-alert").textContent, /connection/);
});

test("administration controller handles rejected requests, empty history, and declined archive", async () => {
  const rejected = new FakeDocument();
  createAdministrationController(rejected, {
    fetch: async () => response(503, { error: { message: "Storage unavailable." } }),
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  assert.equal(rejected.querySelector("#administration-alert").textContent, "Storage unavailable.");

  const doc = new FakeDocument();
  doc.cookie = "";
  const requests = [];
  createAdministrationController(doc, {
    fetch: async (url, options = {}) => {
      requests.push([url, options]);
      if (url === "/api/v1/templates?limit=100&offset=0") return response(200, { items: [template()], total: 1 });
      if (url === "/api/v1/admin/users") return response(200, []);
      if (url.endsWith("/versions")) return response(200, []);
      return response(200);
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  const list = doc.querySelector("#managed-template-list");
  const details = descendants(list).find((child) => child.tagName === "DETAILS");
  await byText(details, "Load version history", "button").dispatch("click");
  assert.match(byText(details, "No versions are available.").textContent, /No versions/);
  const count = requests.length;
  await byText(list, "Archive", "button").dispatch("click");
  assert.equal(requests.length, count);
  await byText(list, "Make preferred", "button").dispatch("click");
  assert.equal("X-CSRF-Token" in requests.at(-1)[1].headers, false);
});

test("regular users can view foreign templates without owner controls", async () => {
  const doc = new FakeDocument("user");
  createAdministrationController(doc, {
    fetch: async () => response(200, { items: [template()], total: 1 }),
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  const list = doc.querySelector("#managed-template-list");
  assert.equal(byText(list, "Edit and version history"), undefined);
  assert.equal(byText(list, "Archive", "button"), undefined);
  assert.ok(byText(list, "Download current"));
});

test("template loading follows every server page", async () => {
  const doc = new FakeDocument("user");
  const urls = [];
  createAdministrationController(doc, {
    fetch: async (url) => {
      urls.push(url);
      if (url.endsWith("offset=0")) return response(200, { items: [template()], total: 2 });
      return response(200, { items: [template({ id: "template-2", name: "Second" })], total: 2 });
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  assert.deepEqual(urls, [
    "/api/v1/templates?limit=100&offset=0",
    "/api/v1/templates?limit=100&offset=1",
  ]);
  assert.equal(byClass(doc.querySelector("#managed-template-list"), "management-card").length, 2);
});

test("late template loads are aborted and cannot overwrite newer state", async () => {
  const doc = new FakeDocument("user");
  const first = deferred();
  const second = deferred();
  const signals = [];
  let call = 0;
  const controller = createAdministrationController(doc, {
    fetch: (_url, options) => {
      signals.push(options.signal);
      call += 1;
      return call === 1 ? first.promise : second.promise;
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  const newer = controller.loadTemplates();
  assert.equal(signals[0].aborted, true);
  second.resolve(response(200, { items: [template({ name: "Newest" })], total: 1 }));
  await newer;
  first.resolve(response(200, { items: [template({ name: "Stale" })], total: 1 }));
  await settle();
  const text = doc.querySelector("#managed-template-list").children[0]
    .children[0].children[0].children[0].textContent;
  assert.equal(text, "Newest");
});

test("late user and version loads cannot overwrite newer administration state", async () => {
  const userDoc = new FakeDocument();
  const firstUsers = deferred();
  const secondUsers = deferred();
  const userSignals = [];
  let userCalls = 0;
  const userController = createAdministrationController(userDoc, {
    fetch: (url, options = {}) => {
      if (url.startsWith("/api/v1/templates")) {
        return Promise.resolve(response(200, { items: [], total: 0 }));
      }
      userSignals.push(options.signal);
      userCalls += 1;
      return userCalls === 1 ? firstUsers.promise : secondUsers.promise;
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  const newerUsers = userController.loadUsers();
  assert.equal(userSignals[0].aborted, true);
  secondUsers.resolve(response(200, [{ id: "new", username: "Newest", role: "user", active: true }]));
  await newerUsers;
  firstUsers.resolve(response(200, [{ id: "old", username: "Stale", role: "user", active: true }]));
  await settle();
  assert.match(userDoc.querySelector("#user-list").children[0].children[0].textContent, /Newest/);

  const versionDoc = new FakeDocument("user");
  const versions = deferred();
  let versionSignal;
  const versionController = createAdministrationController(versionDoc, {
    fetch: (url, options = {}) => {
      if (url.endsWith("/versions")) {
        versionSignal = options.signal;
        return versions.promise;
      }
      return Promise.resolve(response(200, { items: [template({ owner_id: "alice-id" })], total: 1 }));
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  const details = descendants(versionDoc.querySelector("#managed-template-list"))
    .find((child) => child.tagName === "DETAILS");
  const loadButton = byText(details, "Load version history", "button");
  const loading = loadButton.listeners.get("click")[0]({});
  await versionController.loadTemplates();
  assert.equal(versionSignal.aborted, true);
  versions.resolve(response(200, [{ id: "stale", number: 9, size: 1, created_at: "later" }]));
  await loading;
  assert.equal(byText(versionDoc.querySelector("#managed-template-list"), "Version 9 · 1 bytes · later"), undefined);
});

test("superseded body parsing cannot publish template, user, or version errors", async () => {
  const doc = new FakeDocument();
  const templateBody = deferred();
  const userBody = deferred();
  let templateCalls = 0;
  let userCalls = 0;
  let templateParsing = false;
  let userParsing = false;
  const controller = createAdministrationController(doc, {
    fetch: async (url) => {
      if (url.startsWith("/api/v1/templates")) {
        templateCalls += 1;
        if (templateCalls === 1) return {
          ok: false,
          status: 503,
          json() { templateParsing = true; return templateBody.promise; },
        };
        return response(200, { items: [], total: 0 });
      }
      userCalls += 1;
      if (userCalls === 1) return {
        ok: true,
        status: 200,
        json() { userParsing = true; return userBody.promise; },
      };
      return response(200, []);
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  assert.equal(templateParsing, true);
  assert.equal(userParsing, true);
  await Promise.all([controller.loadTemplates(), controller.loadUsers()]);
  templateBody.resolve({ error: { message: "Stale template failure" } });
  userBody.resolve({ invalid: "stale users" });
  await settle();
  assert.equal(doc.querySelector("#administration-alert").textContent, "");

  const versionDoc = new FakeDocument("user");
  const versionBody = deferred();
  let versionParsing = false;
  const versionController = createAdministrationController(versionDoc, {
    fetch: async (url) => {
      if (url.endsWith("/versions")) return {
        ok: false,
        status: 503,
        json() { versionParsing = true; return versionBody.promise; },
      };
      return response(200, { items: [template({ owner_id: "alice-id" })], total: 1 });
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  const details = descendants(versionDoc.querySelector("#managed-template-list"))
    .find((child) => child.tagName === "DETAILS");
  const loading = byText(details, "Load version history", "button")
    .listeners.get("click")[0]({});
  await settle();
  assert.equal(versionParsing, true);
  await versionController.loadTemplates();
  versionBody.resolve({ error: { message: "Stale version failure" } });
  await loading;
  assert.equal(versionDoc.querySelector("#administration-alert").textContent, "");
});

test("malformed success bodies are rejected without replacing current state", async () => {
  for (const badBody of [{ items: [], total: "invalid" }, new Error("truncated")]) {
    const doc = new FakeDocument("user");
    createAdministrationController(doc, {
      fetch: async () => response(200, badBody),
      confirm: () => false,
      FormData: FakeFormData,
    });
    await settle();
    assert.match(doc.querySelector("#administration-alert").textContent, /invalid response/);
    assert.equal(doc.querySelector("#managed-template-list").children.length, 0);
  }
});

test("guarded forms suppress concurrent duplicate submissions", async () => {
  const doc = new FakeDocument("user");
  const mutation = deferred();
  let posts = 0;
  createAdministrationController(doc, {
    fetch: async (url, options = {}) => {
      if ((options.method || "GET") === "POST") {
        posts += 1;
        return mutation.promise;
      }
      return response(200, { items: [], total: 0 });
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  const form = doc.querySelector("#create-template-form");
  form.elements.name.value = "Only once";
  form.elements.description.value = "Concurrent click";
  form.elements.content.files = [{ name: "valid.docx", size: 1 }];
  const listener = form.listeners.get("submit")[0];
  const event = { preventDefault() {} };
  const first = listener(event);
  const duplicate = listener(event);
  assert.equal(posts, 1);
  mutation.resolve(response(201));
  await Promise.all([first, duplicate]);
  assert.equal(posts, 1);
});

test("password reset suppresses concurrent duplicate mutations", async () => {
  const doc = new FakeDocument();
  const mutation = deferred();
  let resets = 0;
  createAdministrationController(doc, {
    fetch: async (url, options = {}) => {
      if (url.endsWith("/password")) {
        resets += 1;
        return mutation.promise;
      }
      if (url === "/api/v1/admin/users") {
        return response(200, [{ id: "alice", username: "Alice", role: "user", active: true }]);
      }
      return response(200, { items: [], total: 0 });
    },
    confirm: () => false,
    FormData: FakeFormData,
  });
  await settle();
  const resetForm = descendants(doc.querySelector("#user-list"))
    .find((child) => child.className === "inline-form");
  resetForm.elements.password.value = "new-password";
  const listener = resetForm.listeners.get("submit")[0];
  const event = { preventDefault() {} };
  const first = listener(event);
  const duplicate = listener(event);
  assert.equal(resets, 1);
  mutation.resolve(response(200));
  await Promise.all([first, duplicate]);
  assert.equal(resets, 1);
  assert.equal(
    doc.querySelector("#administration-alert").textContent,
    "Password reset completed for Alice.",
  );
});
