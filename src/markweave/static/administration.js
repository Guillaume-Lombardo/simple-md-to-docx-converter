import { readCookie, responseError } from "./conversion.js";

function replaceChildren(element) {
  while (element.firstChild) element.removeChild(element.firstChild);
}

function appendText(parent, tag, text, className = "") {
  const child = parent.ownerDocument.createElement(tag);
  child.textContent = text;
  if (className) child.className = className;
  parent.appendChild(child);
  return child;
}

export function splitFonts(value) {
  return value.split(",").map((font) => font.trim()).filter(Boolean);
}

function appendFonts(data, value) {
  const fonts = splitFonts(value);
  if (!fonts.length) data.append("expected_fonts", "");
  for (const font of fonts) data.append("expected_fonts", font);
}

export function templateMatches(template, query, mine, userId) {
  if (mine && template.owner_id !== userId) return false;
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [template.name, template.description, template.owner_username]
    .some((value) => String(value || "").toLocaleLowerCase().includes(needle));
}

export function userMatches(user, query) {
  return user.username.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
}

export function templateEtag(template) {
  return `"template-${template.id}-${template.revision}"`;
}

export function validTemplateFile(file, maximumBytes) {
  if (!file) return "Choose a DOCX template.";
  if (!/\.docx$/i.test(file.name)) return "Choose a file ending in .docx.";
  if (file.size < 1) return "The selected template is empty.";
  if (file.size > maximumBytes) return "The selected template exceeds the configured upload limit.";
  return null;
}

export function validTemplatePage(page) {
  return Boolean(page && Array.isArray(page.items) && Number.isInteger(page.total)
    && page.total >= 0 && page.items.every((item) => item && typeof item.id === "string"
      && typeof item.owner_id === "string" && typeof item.owner_username === "string"
      && typeof item.name === "string" && typeof item.description === "string"
      && ["active", "archived"].includes(item.status) && Number.isInteger(item.revision)));
}

export function validUsers(users) {
  return Array.isArray(users) && users.every((user) => user && typeof user.id === "string"
    && typeof user.username === "string" && ["admin", "user"].includes(user.role)
    && typeof user.active === "boolean");
}

export function validVersions(versions) {
  return Array.isArray(versions) && versions.every((version) => version
    && typeof version.id === "string" && Number.isInteger(version.number)
    && Number.isInteger(version.size) && typeof version.created_at === "string");
}

export function createAdministrationController(doc, dependencies = {}) {
  const list = doc.querySelector("#managed-template-list");
  if (!list) return null;
  const fetchRequest = dependencies.fetch || globalThis.fetch.bind(globalThis);
  const confirmAction = dependencies.confirm || globalThis.confirm.bind(globalThis);
  const FormDataClass = dependencies.FormData || globalThis.FormData;
  const AbortControllerClass = dependencies.AbortController || globalThis.AbortController;
  const alert = doc.querySelector("#administration-alert");
  const filter = doc.querySelector("#template-filter");
  const mine = doc.querySelector("#my-templates");
  const createTemplateForm = doc.querySelector("#create-template-form");
  const userList = doc.querySelector("#user-list");
  const userSearch = doc.querySelector("#user-search");
  const createUserForm = doc.querySelector("#create-user-form");
  const userId = doc.body.dataset.userId;
  const isAdmin = doc.body.dataset.userRole === "admin";
  const maximumBytes = Number(doc.body.dataset.maxTemplateBytes);
  let templates = [];
  let users = [];
  let preferredId = doc.body.dataset.preferredTemplateId;
  let templateLoadGeneration = 0;
  let templateLoadAbort = null;
  let userLoadGeneration = 0;
  let userLoadAbort = null;
  const versionLoads = new Map();

  function showMessage(message, failure = true) {
    alert.textContent = message;
    alert.classList.toggle("notice", !failure);
    alert.hidden = false;
  }

  function clearMessage() {
    alert.textContent = "";
    alert.hidden = true;
    alert.classList.remove("notice");
  }

  function csrfHeaders(extra = {}) {
    const token = readCookie(doc.cookie, "__Host-md_converter_csrf");
    return token ? { ...extra, "X-CSRF-Token": token } : extra;
  }

  async function request(url, options = {}, isCurrent = () => true) {
    const canPublish = () => !options.signal?.aborted && isCurrent();
    let response;
    try {
      response = await fetchRequest(url, options);
    } catch {
      if (!canPublish()) return null;
      showMessage("The request could not be completed. Check your connection and try again.");
      return null;
    }
    if (!response.ok) {
      const message = await responseError(response);
      if (canPublish()) showMessage(message);
      return null;
    }
    return response;
  }

  async function responseJson(response, validator, isCurrent = () => true) {
    let value;
    try {
      value = await response.json();
    } catch {
      if (isCurrent()) showMessage("The server returned an invalid response. Try again.");
      return null;
    }
    if (!validator(value)) {
      if (isCurrent()) showMessage("The server returned an invalid response. Try again.");
      return null;
    }
    return value;
  }

  function actionButton(parent, label, handler, className = "") {
    const button = appendText(parent, "button", label, className);
    button.type = "button";
    button.addEventListener("click", async (event) => {
      if (button.disabled) return;
      button.disabled = true;
      try {
        await handler(event);
      } finally {
        button.disabled = false;
      }
    });
    return button;
  }

  function guardedSubmit(form, handler) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (form.dataset.submitting === "true") return;
      form.dataset.submitting = "true";
      try {
        await handler(event);
      } finally {
        delete form.dataset.submitting;
      }
    });
  }

  async function mutateTemplate(template, url, options, successMessage) {
    clearMessage();
    const response = await request(url, options);
    if (!response) return null;
    showMessage(successMessage, false);
    await loadTemplates();
    return response;
  }

  function renderVersions(container, template, versions) {
    replaceChildren(container);
    if (!versions.length) {
      appendText(container, "p", "No versions are available.", "muted");
      return;
    }
    const versionList = appendText(container, "ol", "");
    for (const version of versions) {
      const item = appendText(versionList, "li", "");
      appendText(item, "span", `Version ${version.number} · ${version.size} bytes · ${version.created_at}`);
      const download = appendText(item, "a", "Download");
      download.href = `/api/v1/templates/${template.id}/versions/${version.id}/content`;
      if ((template.owner_id === userId || isAdmin) && version.id !== template.current_version_id) {
        actionButton(item, "Restore", async () => {
          await mutateTemplate(
            template,
            `/api/v1/templates/${template.id}/versions/${version.id}/restore`,
            { method: "POST", headers: csrfHeaders({ "If-Match": templateEtag(template) }) },
            `Version ${version.number} was restored as a new version.`,
          );
        });
      }
    }
  }

  async function loadVersions(container, template) {
    const previous = versionLoads.get(template.id);
    previous?.controller.abort();
    const requestAbort = new AbortControllerClass();
    const generation = (previous?.generation || 0) + 1;
    const active = { controller: requestAbort, generation };
    versionLoads.set(template.id, active);
    const isCurrent = () => !requestAbort.signal.aborted
      && versionLoads.get(template.id) === active;
    const response = await request(`/api/v1/templates/${template.id}/versions`, {
      signal: requestAbort.signal,
    }, isCurrent);
    if (!response || !isCurrent()) return;
    const versions = await responseJson(response, validVersions, isCurrent);
    if (versions && isCurrent()) renderVersions(container, template, versions);
  }

  function renderTemplate(template) {
    const item = appendText(list, "li", "", "management-card");
    const heading = appendText(item, "div", "", "management-heading");
    const title = appendText(heading, "div", "");
    appendText(title, "h3", template.name);
    appendText(title, "p", `${template.owner_username} · ${template.status}`, "muted");
    appendText(item, "p", template.description || "No description.");
    if (preferredId === template.id) appendText(item, "p", "Preferred template", "status-badge");
    const actions = appendText(item, "div", "", "actions wrap");
    const download = appendText(actions, "a", "Download current", "button secondary");
    download.href = `/api/v1/templates/${template.id}/content`;
    const isPreferred = preferredId === template.id;
    if (template.status === "active" || isPreferred) {
      actionButton(actions, isPreferred ? "Clear preferred" : "Make preferred", async () => {
        const response = await request(
          isPreferred ? "/api/v1/template-preference" : `/api/v1/templates/${template.id}/preferred`,
          { method: isPreferred ? "DELETE" : "PUT", headers: csrfHeaders() },
        );
        if (response) {
          preferredId = isPreferred ? "" : template.id;
          showMessage(
            isPreferred ? "Your preferred template was cleared." : `${template.name} is now your preferred template.`,
            false,
          );
          renderTemplates();
        }
      });
    }
    const canManage = template.owner_id === userId || isAdmin;
    if (!canManage) return;

    const details = appendText(item, "details", "");
    appendText(details, "summary", "Edit and version history");
    const metadata = appendText(details, "form", "", "stack compact-form");
    const nameLabel = appendText(metadata, "label", "Name");
    const name = appendText(nameLabel, "input", "");
    name.name = "name";
    name.required = true;
    name.value = template.name;
    const descriptionLabel = appendText(metadata, "label", "Description");
    const description = appendText(descriptionLabel, "textarea", "");
    description.name = "description";
    description.value = template.description;
    appendText(metadata, "button", "Save details").type = "submit";
    guardedSubmit(metadata, async () => {
      await mutateTemplate(template, `/api/v1/templates/${template.id}`, {
        method: "PATCH",
        headers: csrfHeaders({ "Content-Type": "application/json", "If-Match": templateEtag(template) }),
        body: JSON.stringify({ name: name.value, description: description.value }),
      }, "Template details were saved.");
    });

    const replacement = appendText(details, "form", "", "stack compact-form");
    appendText(replacement, "h4", "Replace content");
    const fileLabel = appendText(replacement, "label", "DOCX file");
    const file = appendText(fileLabel, "input", "");
    file.type = "file";
    file.name = "content";
    file.accept = ".docx";
    file.required = true;
    const fontsLabel = appendText(replacement, "label", "Expected fonts");
    const fonts = appendText(fontsLabel, "input", "");
    fonts.name = "expected_fonts";
    fonts.placeholder = "Liberation Serif, Carlito";
    appendText(replacement, "button", "Replace template").type = "submit";
    guardedSubmit(replacement, async () => {
      const invalid = validTemplateFile(file.files?.[0], maximumBytes);
      if (invalid) return showMessage(invalid);
      const data = new FormDataClass();
      data.append("content", file.files[0]);
      appendFonts(data, fonts.value);
      await mutateTemplate(template, `/api/v1/templates/${template.id}/content`, {
        method: "PUT", headers: csrfHeaders({ "If-Match": templateEtag(template) }), body: data,
      }, "Template content was replaced.");
    });

    const versions = appendText(details, "div", "", "version-list");
    actionButton(details, "Load version history", async () => {
      await loadVersions(versions, template);
    }, "secondary");
    if (template.status === "active") {
      actionButton(actions, "Archive", async () => {
        if (!confirmAction(`Archive ${template.name}?`)) return;
        await mutateTemplate(template, `/api/v1/templates/${template.id}/archive`, {
          method: "POST", headers: csrfHeaders({ "If-Match": templateEtag(template) }),
        }, "Template was archived.");
      }, "danger");
    } else {
      actionButton(actions, "Delete", async () => {
        if (!confirmAction(`Permanently delete archived template ${template.name}?`)) return;
        await mutateTemplate(template, `/api/v1/templates/${template.id}`, {
          method: "DELETE", headers: csrfHeaders({ "If-Match": templateEtag(template) }),
        }, "Template was deleted.");
      }, "danger");
    }
  }

  function renderTemplates() {
    replaceChildren(list);
    const visible = templates.filter((template) => templateMatches(template, filter.value, mine.checked, userId));
    if (!visible.length) return appendText(list, "li", "No templates match these filters.", "muted");
    for (const template of visible) renderTemplate(template);
  }

  async function loadTemplates() {
    const generation = ++templateLoadGeneration;
    templateLoadAbort?.abort();
    for (const active of versionLoads.values()) active.controller.abort();
    versionLoads.clear();
    const requestAbort = new AbortControllerClass();
    templateLoadAbort = requestAbort;
    const isCurrent = () => !requestAbort.signal.aborted
      && generation === templateLoadGeneration;
    const loaded = [];
    let offset = 0;
    while (true) {
      const response = await request(`/api/v1/templates?limit=100&offset=${offset}`, {
        signal: requestAbort.signal,
      }, isCurrent);
      if (!response || !isCurrent()) return;
      const page = await responseJson(response, validTemplatePage, isCurrent);
      if (!page || !isCurrent()) return;
      loaded.push(...page.items);
      if (loaded.length >= page.total || page.items.length === 0) break;
      offset = loaded.length;
    }
    if (isCurrent()) {
      templates = loaded;
      renderTemplates();
    }
  }

  function renderUsers() {
    if (!userList) return;
    replaceChildren(userList);
    const visible = users.filter((user) => userMatches(user, userSearch.value));
    if (!visible.length) return appendText(userList, "li", "No users match this search.", "muted");
    for (const user of visible) {
      const item = appendText(userList, "li", "", "management-card");
      appendText(item, "h3", user.username);
      appendText(item, "p", `${user.role} · ${user.active ? "active" : "inactive"}`, "muted");
      const actions = appendText(item, "div", "", "actions wrap");
      if (user.role !== "admin") {
        actionButton(actions, user.active ? "Deactivate" : "Reactivate", async () => {
          const response = await request(`/api/v1/admin/users/${user.id}/active`, {
            method: "PATCH", headers: csrfHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ active: !user.active }),
          });
          if (response) {
            showMessage(`${user.username} is now ${user.active ? "inactive" : "active"}.`, false);
            await loadUsers();
          }
        }, user.active ? "danger" : "");
      }
      const resetForm = appendText(item, "form", "", "inline-form");
      const label = appendText(resetForm, "label", "New password");
      const password = appendText(label, "input", "");
      password.type = "password";
      password.name = "password";
      password.autocomplete = "new-password";
      password.required = true;
      appendText(resetForm, "button", "Reset password").type = "submit";
      guardedSubmit(resetForm, async () => {
        const response = await request(`/api/v1/admin/users/${user.id}/password`, {
          method: "POST", headers: csrfHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ password: password.value }),
        });
        if (response) {
          resetForm.reset();
          showMessage(`Password reset completed for ${user.username}.`, false);
        }
      });
    }
  }

  async function loadUsers() {
    if (!userList) return;
    const generation = ++userLoadGeneration;
    userLoadAbort?.abort();
    const requestAbort = new AbortControllerClass();
    userLoadAbort = requestAbort;
    const isCurrent = () => !requestAbort.signal.aborted
      && generation === userLoadGeneration;
    const response = await request(
      "/api/v1/admin/users", { signal: requestAbort.signal }, isCurrent,
    );
    if (!response || !isCurrent()) return;
    const loaded = await responseJson(response, validUsers, isCurrent);
    if (loaded && isCurrent()) {
      users = loaded;
      renderUsers();
    }
  }

  filter.addEventListener("input", renderTemplates);
  mine.addEventListener("change", renderTemplates);
  userSearch?.addEventListener("input", renderUsers);
  guardedSubmit(createTemplateForm, async () => {
    clearMessage();
    const file = createTemplateForm.elements.content.files?.[0];
    const invalid = validTemplateFile(file, maximumBytes);
    if (invalid) return showMessage(invalid);
    const data = new FormDataClass();
    data.append("name", createTemplateForm.elements.name.value);
    data.append("description", createTemplateForm.elements.description.value);
    appendFonts(data, createTemplateForm.elements.expected_fonts.value);
    data.append("content", file);
    const response = await request("/api/v1/templates", { method: "POST", headers: csrfHeaders(), body: data });
    if (response) {
      createTemplateForm.reset();
      showMessage("Template was created.", false);
      await loadTemplates();
    }
  });
  if (createUserForm) guardedSubmit(createUserForm, async () => {
    const response = await request("/api/v1/admin/users", {
      method: "POST", headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        username: createUserForm.elements.username.value,
        password: createUserForm.elements.password.value,
      }),
    });
    if (response) {
      createUserForm.reset();
      showMessage("Account was created.", false);
      await loadUsers();
    }
  });

  void loadTemplates();
  void loadUsers();
  return { loadTemplates, loadUsers, renderTemplates, renderUsers };
}

if (typeof document !== "undefined") createAdministrationController(document);
