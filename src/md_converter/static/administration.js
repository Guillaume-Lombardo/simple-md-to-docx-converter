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

export function createAdministrationController(doc, dependencies = {}) {
  const list = doc.querySelector("#managed-template-list");
  if (!list) return null;
  const fetchRequest = dependencies.fetch || globalThis.fetch.bind(globalThis);
  const confirmAction = dependencies.confirm || globalThis.confirm.bind(globalThis);
  const FormDataClass = dependencies.FormData || globalThis.FormData;
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

  async function request(url, options = {}) {
    let response;
    try {
      response = await fetchRequest(url, options);
    } catch {
      showMessage("The request could not be completed. Check your connection and try again.");
      return null;
    }
    if (!response.ok) {
      showMessage(await responseError(response));
      return null;
    }
    return response;
  }

  function actionButton(parent, label, handler, className = "") {
    const button = appendText(parent, "button", label, className);
    button.type = "button";
    button.addEventListener("click", handler);
    return button;
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
    if (template.status === "active") {
      const isPreferred = preferredId === template.id;
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
    metadata.addEventListener("submit", async (event) => {
      event.preventDefault();
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
    replacement.addEventListener("submit", async (event) => {
      event.preventDefault();
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
      const response = await request(`/api/v1/templates/${template.id}/versions`);
      if (response) renderVersions(versions, template, await response.json());
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
    const loaded = [];
    let offset = 0;
    while (true) {
      const response = await request(`/api/v1/templates?limit=100&offset=${offset}`);
      if (!response) return;
      const page = await response.json();
      loaded.push(...page.items);
      if (loaded.length >= page.total || page.items.length === 0) break;
      offset = loaded.length;
    }
    templates = loaded;
    renderTemplates();
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
      resetForm.addEventListener("submit", async (event) => {
        event.preventDefault();
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
    const response = await request("/api/v1/admin/users");
    if (!response) return;
    users = await response.json();
    renderUsers();
  }

  filter.addEventListener("input", renderTemplates);
  mine.addEventListener("change", renderTemplates);
  userSearch?.addEventListener("input", renderUsers);
  createTemplateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
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
  createUserForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
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
