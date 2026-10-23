"use strict";

const $ = (id) => document.getElementById(id);
let members = [];
let invited = null;
let workspace = null;
let exampleClassifiers = new Set();
let classifierInfo = [];
let summarizerInfo = [];
let linkerInfo = [];
let githubMembers = [];
let githubHandles = {};
let githubTimer;
let githubError = "";
let rulesClassifiers = new Set();
let rules = [];
let rulesError = "";
let mode = "server";
let appStatus = null;
// where the team runs now, the server or the app, which saving here may change
let runningOn = null;
// where the server says the team is set to run, whether it is stopped, and how its app is doing
let botStatus = null;
let statusTimer;
let drawnDiagram = "";
let appKey = "";
let channelOk = false;
let listOk = false;

function el(tag, props = {}, children = []) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function api(action, body) {
  const response = await fetch(`/setup/api/${action}`, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({ error: "unexpected reply from the server" }));
  if (!response.ok) throw new Error(data.error || "something went wrong");
  return data;
}

function status(id, text, ok) {
  // ok null is a neutral state, waiting rather than good or bad
  $(id).textContent = text;
  $(id).className = ok === null ? "status" : `status ${ok ? "ok" : "bad"}`;
}

let researchers = [];
let reactEmoji = "robot_face";
let exampleFiles = {};
const uploading = {};
const exampleErrors = {};
const EXAMPLE_BYTES = 100 * 1024 * 1024;

function avatar(person) {
  if (person.photo) return el("img", { src: person.photo, alt: "", className: "avatar" });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = '<circle cx="12" cy="8.5" r="4"/><path d="M4 20.5c1.5-4 4.5-6 8-6s6.5 2 8 6"/>';
  return el("span", { className: "avatar placeholder" }, [svg]);
}

function person(id) {
  return members.find((m) => m.id === id) || (invited && invited.id === id ? invited : { id, name: null, photo: null });
}

function label(who) {
  return who.name || `Unknown (${who.id})`;
}

function nameNode(who, className) {
  if (who.name) return el("span", { className, textContent: who.name });
  const parts = ["Unknown ("];
  if (workspace) {
    const link = el("a", {
      href: `${workspace}team/${who.id}`, target: "_blank", rel: "noopener", className: "profile-link",
      textContent: who.id, tabIndex: 0,
    });
    link.dataset.tip = "Open their Slack profile to see who this is";
    link.addEventListener("click", (event) => event.stopPropagation());
    parts.push(link);
  } else {
    parts.push(who.id);
  }
  parts.push(")");
  return el("span", { className }, parts);
}

function personCard(who, role, onRemove) {
  const card = el("div", { className: "person" }, [
    avatar(who),
    el("div", { className: "who" }, [el("div", { className: "name" }, [nameNode(who)]), el("div", { className: "role", textContent: role })]),
  ]);
  if (onRemove) {
    const remove = el("button", { type: "button", className: "remove", textContent: "×", ariaLabel: `Remove ${label(who)}` });
    remove.addEventListener("click", onRemove);
    card.append(remove);
  }
  return card;
}

function renderPeople() {
  if (invited) $("admin-card").replaceWith(Object.assign(personCard(invited, "Admin (You)"), { id: "admin-card" }));
  const cards = researchers.map((id) => personCard(person(id), "Researcher", () => {
    researchers = researchers.filter((x) => x !== id);
    saveDraft();
    renderPeople();
  }));
  $("researcher-grid").replaceChildren(...cards);
  renderMenu();
  renderExamples();
  renderGithub();
  progress();
}

function renderMenu() {
  const text = $("researcher-filter").value.trim().toLowerCase();
  const open = members.filter((m) => !researchers.includes(m.id));
  const matches = open.filter((m) => label(m).toLowerCase().includes(text));
  const empty = !open.length ? "Everyone who has posted is added." : "No matches.";
  $("researcher-options").replaceChildren(...(matches.length ? matches.map((m) => {
    const option = el("div", { className: "option", role: "option" }, [avatar(m), nameNode(m)]);
    option.addEventListener("click", () => {
      researchers = [...researchers, m.id];
      saveDraft();
      $("researcher-filter").value = "";
      renderPeople();
      $("researcher-filter").focus();
    });
    return option;
  }) : [el("p", { className: "empty", textContent: empty })]));
}

function toggleMenu(open) {
  $("researcher-menu").hidden = !open;
  $("add-researcher").setAttribute("aria-expanded", String(open));
  if (open) {
    renderMenu();
    $("researcher-filter").focus();
  }
}

function size(bytes) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderExamples() {
  $("examples-block").hidden = !researchers.length || !exampleClassifiers.has($("classifier").value);
  if ($("examples-block").hidden) return;
  $("examples").replaceChildren(...researchers.map((uid) => {
    const who = person(uid);
    const files = exampleFiles[uid] || [];
    const pending = uploading[uid] || [];
    const count = files.length ? `${files.length} example${files.length === 1 ? "" : "s"}` : "No examples yet";
    const input = el("input", { type: "file", multiple: true, accept: ".pdf,.docx", hidden: true });
    input.addEventListener("change", () => upload(uid, input.files));
    const zone = el("label", { className: "dropzone" }, [input, "Drop past reports here or ", el("span", { className: "browse", textContent: "browse" })]);
    zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      zone.classList.remove("over");
      upload(uid, event.dataTransfer.files);
    });
    const rows = files.map((f) => {
      const remove = el("button", { type: "button", className: "remove", textContent: "×", ariaLabel: `Remove ${f.name}` });
      remove.addEventListener("click", () => removeExample(uid, f.name));
      return el("li", {}, [el("span", { className: "file-name", textContent: f.name }), el("span", { className: "file-size", textContent: size(f.size) }), remove]);
    });
    const busy = pending.map((name) => el("li", { className: "pending" }, [el("span", { className: "file-name", textContent: name }), el("span", { className: "file-size", textContent: "Uploading…" })]));
    const parts = [
      el("div", { className: "example-who" }, [avatar(who), nameNode(who, "name"), el("span", { className: "count", textContent: count })]),
      zone,
    ];
    if (rows.length || busy.length) parts.push(el("ul", { className: "files" }, [...rows, ...busy]));
    if (exampleErrors[uid]) parts.push(el("p", { className: "status bad", textContent: exampleErrors[uid] }));
    return el("div", { className: "example-row" }, parts);
  }));
}

function renderRules() {
  $("rules-block").hidden = !rulesClassifiers.has($("classifier").value);
  if ($("rules-block").hidden) return;
  const input = el("input", { type: "file", accept: ".txt,text/plain", hidden: true });
  input.addEventListener("change", () => uploadRules(input.files[0]));
  const zone = el("label", { className: "dropzone" }, [input, `Drop ${rules.length ? "a new" : "your"} rules file here or `, el("span", { className: "browse", textContent: "browse" })]);
  zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("over");
    uploadRules(event.dataTransfer.files[0]);
  });
  const parts = [zone];
  if (rules.length) parts.push(el("ul", { className: "files" }, rules.map((r) => el("li", {}, [el("code", { className: "file-name", textContent: r })]))));
  if (rulesError) parts.push(el("p", { className: "status bad", textContent: rulesError }));
  $("rules").replaceChildren(...parts);
}

function ago(seconds) {
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

function showTab(local) {
  try {
    localStorage.setItem(`${draftKey}-mode`, local ? "local" : "server");
  } catch {}
  $("modes").hidden = true;
  $("tabs").hidden = false;
  $("tab-server").setAttribute("aria-selected", String(!local));
  $("tab-local").setAttribute("aria-selected", String(local));
  $("local-panel").hidden = !local;
  $("form").hidden = local;
}

function chosenMode(running) {
  if (running) return running;
  try {
    return localStorage.getItem(`${draftKey}-mode`);
  } catch {
    return null;
  }
}

function copyBox(text, label) {
  const box = el("button", { type: "button", className: "copybox", ariaLabel: label }, [
    el("code", { textContent: text }),
    $("copy-invite").querySelector(".icon-copy").cloneNode(true),
    $("copy-invite").querySelector(".icon-done").cloneNode(true),
    el("span", { className: "copied", role: "status", textContent: "Copied!" }),
  ]);
  box.addEventListener("click", async () => {
    await navigator.clipboard.writeText(text);
    box.classList.add("done");
    clearTimeout(box.timer);
    box.timer = setTimeout(() => box.classList.remove("done"), 1500);
  });
  return box;
}

function renderApp() {
  const connect = el("button", { type: "button", className: appStatus ? "secondary" : "", textContent: "Connect" });
  connect.addEventListener("click", connectApp);
  const parts = [];
  if (appKey) {
    const box = copyBox(appKey, "Copy app key");
    parts.push(
      el("p", { className: "muted hint", textContent: "The app should open with its own setup page. If it didn't, start the app and paste this key. It is only shown once." }),
      el("div", { className: "key-row" }, [box]),
    );
  } else if (appStatus) {
    const seen = appStatus.seen ? `Connected · app last seen ${ago(Date.now() / 1000 - appStatus.seen)}` : "Not connected yet";
    parts.push(el("div", { className: "key-row" }, [el("span", { className: appStatus.seen ? "status ok" : "status", textContent: seen }), connect]));
  } else {
    parts.push(el("div", { className: "key-row" }, [connect]));
  }
  $("app-key").replaceChildren(...parts);
}

async function connectApp() {
  if (appStatus && !confirm("The app connected now will stop working until it is connected again. Continue?")) return;
  try {
    appKey = (await api("app-key", {})).key;
    appStatus = { seen: null };
  } catch (error) {
    $("app-key").replaceChildren(el("p", { className: "status bad", textContent: error.message }));
    return;
  }
  renderApp();
  location.href = `report-bot-app://connect?key=${encodeURIComponent(appKey)}&server=${encodeURIComponent(location.origin)}`;
}

function lock(app) {
  const locked = Boolean(app && app.window_open);
  $("lock").hidden = !locked;
  document.querySelector("main").inert = locked;
}

async function watchApp() {
  try {
    appStatus = (await api("app-status")).app;
  } catch {
    return;
  }
  lock(appStatus);
  if (!appKey) renderApp();
  progress();
}

const STAGES = {
  report: { title: "Classifier", select: "classifier", provider: "llm_provider", model: "llm_model", other: "llm_model_other", providerBlock: "provider-block", modelBlock: "model-block", installBlock: "install-block",
    note: "provider-note", definitionBlock: "definition-block", info: () => classifierInfo, wanted: "", loadedFor: null },
  summary: { title: "Summarizer", select: "summarizer", provider: "summary_provider", model: "summary_model", other: "summary_model_other", providerBlock: "summary-provider-block", modelBlock: "summary-model-block", installBlock: "summary-install-block",
    note: "summary-provider-note", definitionBlock: "summary-definition-block", info: () => summarizerInfo, wanted: "", loadedFor: null },
  links: { title: "Link to Work", select: "linker", provider: "links_provider", model: "links_model", other: "links_model_other", providerBlock: "links-provider-block", modelBlock: "links-model-block", installBlock: "links-install-block",
    note: "links-provider-note", definitionBlock: "links-definition-block", off: () => !assigned.COL_LINK, info: () => linkerInfo, wanted: "", loadedFor: null },
};
const DEFINITIONS = {
  report: { title: "Classifier prompt", text: "Describe what counts as a weekly report. The LLM judges every posted file against this, alongside each researcher's example reports." },
  links: { title: "Work links prompt", text: "Describe when a pull request or link counts as the work a report describes. The LLM follows this for every report." },
  summary: { title: "Summarizer prompt", text: "Describe how to pick the status and write the summary and blockers. The LLM follows this for every report it summarizes." },
};
const INSTALL = {
  claude: { command: "irm https://claude.ai/install.ps1 | iex", run: "claude", signIn: " and sign in with your Claude account in the browser. It needs a paid Claude plan." },
  codex: { command: "npm install -g @openai/codex", run: "codex login", signIn: " and sign in with your ChatGPT account in the browser.", needs: "Node.js" },
  antigravity: { command: "irm https://antigravity.google/cli/install.ps1 | iex", run: "agy", signIn: ", choose Google OAuth, sign in in the browser and paste back the code it gives you." },
};
const OTHER_MODEL = "__other__";
// filled in only on a computer, where the clis that will run the team are this computer's own
let installed = null;
let providerNotes = {};

// with no summarizer the week comes from a pattern, an llm summarizer will find it as it summarizes
const MANUAL_SUMMARIZER = "processors.manual_summarizer";
const LLM_SUMMARIZER = "processors.llm_summarizer";
const LLM_LINKS = "processors.llm_links";

function chosen(stage) {
  return stage.info().find((m) => m.name === $(stage.select).value) || {};
}

async function loadModels(stage, provider) {
  // the list comes from the clis on the machine that will run the team, so it matches that login
  stage.loadedFor = provider;
  stage.loading = true;
  $(stage.model).replaceChildren(el("option", { value: stage.wanted, textContent: "Loading…" }));
  let found = [];
  let accountDefault = false;
  try {
    ({ models: found, account_default: accountDefault } = await api(`models?provider=${encodeURIComponent(provider)}`));
  } catch {}
  if (stage.loadedFor !== provider) return;
  const options = accountDefault ? [el("option", { value: "", textContent: "Account default" })] : [];
  options.push(...found.map((m) => el("option", { value: m.id, textContent: m.name })));
  // the cli takes any model its login allows, not only the ones it lists
  options.push(el("option", { value: OTHER_MODEL, textContent: "Other model…" }));
  $(stage.model).replaceChildren(...options);
  const values = options.map((o) => o.value);
  const listed = values.includes(stage.wanted);
  const other = Boolean(stage.wanted) && !listed;
  $(stage.model).value = other ? OTHER_MODEL : listed ? stage.wanted : values[0] || "";
  $(stage.other).value = other ? stage.wanted : "";
  stage.loading = false;
  renderOtherModel(stage);
  progress();
}

function renderOtherModel(stage) {
  $(stage.other).hidden = $(stage.model).value !== OTHER_MODEL;
}

function modelValue(stage) {
  return $(stage.model).value === OTHER_MODEL ? $(stage.other).value.trim() : $(stage.model).value;
}

function renderReminder() {
  const on = $("send_reminders").checked;
  $("reminder_text").disabled = !on;
  $("reminder-block").classList.toggle("off", !on);
}

let localModelsTimer;

function suggestLocalModels() {
  clearTimeout(localModelsTimer);
  localModelsTimer = setTimeout(async () => {
    const url = $("local_llm_url").value.trim();
    if (!url) return;
    try {
      const found = (await api(`models?provider=local&url=${encodeURIComponent(url)}&key=${encodeURIComponent($("local_llm_key").value)}`)).models;
      $("local-models").replaceChildren(...found.map((m) => el("option", { value: m.id })));
    } catch {}
  }, 500);
}

function renderStage(stage) {
  if (stage.off && stage.off()) return null;
  const info = chosen(stage);
  const providers = info.providers;
  $(stage.providerBlock).hidden = !providers;
  $(stage.select).closest(".field").classList.toggle("wide", !providers);
  if (stage.definitionBlock) $(stage.definitionBlock).hidden = !info.describes;
  if (providers && $(stage.provider).options.length !== Object.keys(providers).length) {
    $(stage.provider).replaceChildren(...Object.entries(providers).map(([value, text]) => el("option", { value, textContent: text })));
  }
  if (providers) {
    for (const option of $(stage.provider).options) {
      option.textContent = providers[option.value] + (installed && installed[option.value] === false ? " (not installed)" : "");
    }
  }
  const provider = providers ? $(stage.provider).value : null;
  const missing = Boolean(provider && installed && installed[provider] === false);
  $(stage.installBlock).hidden = !missing;
  if (missing) renderInstall(stage, provider, providers[provider]);
  $(stage.modelBlock).hidden = !provider || provider === "local" || missing;
  $(stage.note).textContent = providerNotes[provider] || "";
  $(stage.note).hidden = !providerNotes[provider];
  $(stage.providerBlock).classList.toggle("wide", missing);
  if (provider && provider !== "local" && !missing && stage.loadedFor !== provider) loadModels(stage, provider);
  return provider;
}

function renderInstall(stage, provider, name) {
  const guide = INSTALL[provider];
  const block = $(stage.installBlock);
  block.querySelector(".panel-title").textContent = `${name} isn't installed on this computer`;
  const steps = [];
  if (guide.needs) steps.push(el("li", {}, [document.createTextNode("Install "), el("a", { href: "https://nodejs.org/en/download", target: "_blank", rel: "noopener", textContent: guide.needs }), document.createTextNode(" if you don't have it.")]));
  steps.push(
    el("li", {}, [document.createTextNode("Open PowerShell and run:"), el("div", {}, [copyBox(guide.command, `Copy the install command for ${name}`)])]),
    el("li", {}, [document.createTextNode("Open a new PowerShell window, run "), el("code", { textContent: guide.run }), document.createTextNode(guide.signIn)]),
  );
  block.querySelector(".install-steps").replaceChildren(...steps);
}

async function checkInstalled(stage) {
  const block = $(stage.installBlock);
  const button = block.querySelector(".check-installed");
  const status = block.querySelector(".install-status");
  button.disabled = true;
  status.textContent = "Checking…";
  try {
    installed = (await api("installed")).installed;
    for (const each of Object.values(STAGES)) each.loadedFor = null;
    renderLocalModel();
    status.textContent = installed[$(stage.provider).value] === false ? "Still not found. Finish the steps above, then check again." : "";
  } catch (error) {
    status.textContent = error.message;
  }
  button.disabled = false;
  progress();
}

function renderLocalModel() {
  // a local model's settings only matter on a computer, and are shared by every step that uses it
  // link to work only matters once the list has a column for it
  $("links-card").hidden = !assigned.COL_LINK;
  const used = Object.values(STAGES).map(renderStage);
  $("week-pattern-block").hidden = $("summarizer").value !== MANUAL_SUMMARIZER;
  $("local-llm-block").hidden = !used.includes("local");
  // shown in the first card that uses it
  const first = Object.values(STAGES)[used.indexOf("local")];
  const anchor = first && $(first.definitionBlock);
  if (anchor && anchor.previousElementSibling !== $("local-llm-block")) anchor.before($("local-llm-block"));
  renderGithub();
  progress();
}

function renderGithub() {
  const show = Boolean(chosen(STAGES.links).github);
  $("github-block").hidden = !show;
  if (!show) return;
  const logins = githubMembers.map((m) => m.login);
  const hasRepo = Boolean($("github_repo").value.trim());
  $("github-people").replaceChildren(...researchers.map((uid) => {
    const who = person(uid);
    const current = githubHandles[uid] || "";
    const select = el("select", { id: `github-${uid}` }, [el("option", { value: "", textContent: "Not on GitHub" })]);
    for (const login of current && !logins.includes(current) ? [...logins, current] : logins) select.append(el("option", { value: login, textContent: login }));
    select.value = current;
    select.disabled = !hasRepo;
    select.setAttribute("aria-label", `GitHub username for ${label(who)}`);
    select.addEventListener("change", () => {
      if (select.value) githubHandles[uid] = select.value;
      else delete githubHandles[uid];
    });
    return el("div", { className: "github-row" }, [el("div", { className: "github-who" }, [avatar(who), nameNode(who, "name")]), select]);
  }));
}

function loadGithub() {
  clearTimeout(githubTimer);
  githubTimer = setTimeout(async () => {
    const repo = $("github_repo").value.trim();
    githubMembers = [];
    githubError = "";
    if (!repo) {
      status("github-status", "", true);
      progress();
      return renderGithub();
    }
    status("github-status", "Checking…", true);
    try {
      githubMembers = (await api(`github?repo=${encodeURIComponent(repo)}`)).members;
      status("github-status", githubMembers.length
        ? `${githubMembers.length} ${githubMembers.length === 1 ? "person has" : "people have"} opened pull requests or committed here. Pick each researcher's username below.`
        : "Nobody has opened a pull request or committed here yet.", githubMembers.length > 0);
    } catch (error) {
      githubError = error.message;
      status("github-status", error.message, false);
    }
    renderGithub();
    progress();
  }, 500);
}

async function openDefinition(which) {
  $("definition-dialog").dataset.which = which;
  $("definition-title").textContent = DEFINITIONS[which].title;
  $("definition-intro").textContent = DEFINITIONS[which].text;
  $("definition-status").textContent = "";
  $("definition-text").value = "Loading…";
  $("definition-dialog").showModal();
  try {
    const found = await api(`definition?which=${which}`);
    $("definition-text").value = found.text;
    $("definition-text").dataset.default = found.default;
  } catch (error) {
    status("definition-status", error.message, false);
  }
}

async function saveDefinition(event) {
  event.preventDefault();
  const which = $("definition-dialog").dataset.which;
  try {
    await api("definition", { which, text: $("definition-text").value });
    $("definition-dialog").close();
  } catch (error) {
    status("definition-status", error.message, false);
  }
}

async function uploadRules(file) {
  if (!file) return;
  rulesError = "";
  try {
    const response = await fetch("/setup/api/rules", { method: "POST", body: file });
    const data = await response.json().catch(() => ({ error: "unexpected reply from the server" }));
    if (!response.ok) throw new Error(data.error || "upload failed");
    rules = data.rules;
  } catch (error) {
    rulesError = `${file.name}: ${error.message}`;
  }
  renderRules();
}

async function upload(uid, fileList) {
  delete exampleErrors[uid];
  for (const file of fileList) {
    if (file.size > EXAMPLE_BYTES) {
      exampleErrors[uid] = `${file.name} is larger than 100 MB`;
      continue;
    }
    uploading[uid] = [...(uploading[uid] || []), file.name];
    renderExamples();
    try {
      const response = await fetch(`/setup/api/examples/${uid}?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      const data = await response.json().catch(() => ({ error: "unexpected reply from the server" }));
      if (!response.ok) throw new Error(data.error || "upload failed");
      exampleFiles = data;
    } catch (error) {
      exampleErrors[uid] = `${file.name}: ${error.message}`;
    }
    uploading[uid] = uploading[uid].filter((name) => name !== file.name);
  }
  renderExamples();
}

async function removeExample(uid, name) {
  const response = await fetch(`/setup/api/examples/${uid}/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (response.ok) exampleFiles = await response.json();
  renderExamples();
}

async function setEmoji(name, glyph) {
  reactEmoji = name;
  $("emoji-name").textContent = `:${name}:`;
  if (!glyph) {
    await customElements.whenDefined("emoji-picker");
    const found = await $("emoji-picker").database.getEmojiByShortcode(name);
    glyph = found ? found.unicode : "";
  }
  $("emoji-glyph").textContent = glyph;
}

function toggleEmoji(open) {
  $("emoji-popover").hidden = !open;
  $("emoji-button").setAttribute("aria-expanded", String(open));
}

function lockSteps() {
  for (const section of document.querySelectorAll(".needs-channel")) {
    section.classList.toggle("locked", !channelOk);
    section.setAttribute("aria-disabled", String(!channelOk));
    for (const child of section.children) {
      if (child.tagName !== "H2") child.inert = !channelOk;
    }
  }
  if (!channelOk) {
    toggleMenu(false);
    toggleEmoji(false);
  }
}

function stageBlockers(stage) {
  if (stage.off && stage.off()) return [];
  const providers = chosen(stage).providers;
  if (!providers) return [];
  const provider = $(stage.provider).value;
  const name = `${stage.title}: ${providers[provider] || "the LLM"}`;
  if (installed && installed[provider] === false) return [`${name} isn't installed on this computer.`];
  if (provider === "local") {
    return $("local_llm_url").value.trim() && $("local_llm_model").value.trim() ? [] : [`${name} needs the local model server's address and model.`];
  }
  if (stage.loading) return [`${name} is still loading its models.`];
  const model = $(stage.model).value;
  if (model === OTHER_MODEL && !$(stage.other).value.trim()) return [`${name} needs a model name.`];
  return [];
}

function blockers() {
  const found = [];
  if (!channelOk) found.push("Invite the bot to your channel (step 1).");
  if (!listOk) found.push("Pick your Progress Tracker (step 2).");
  found.push(...schemaProblems().map((p) => `Progress Tracker: ${p}`));
  if (!researchers.length) found.push("Add at least one researcher (step 3).");
  if (rulesClassifiers.has($("classifier").value) && !rules.length) found.push("Classifier: upload your regex rules file.");
  for (const stage of Object.values(STAGES)) found.push(...stageBlockers(stage));
  if (assigned.COL_LINK && chosen(STAGES.links).github) {
    if (!$("github_repo").value.trim()) found.push("Link to Work: enter a GitHub repository, or set Link to Work to None.");
    else if (githubError) found.push(`Link to Work: ${githubError}`);
  }
  return found;
}

function applyStatus(found) {
  botStatus = found;
  runningOn = found.location === "computer" ? "local" : found.location;
  if (mode !== "local") {
    appStatus = found.app;
    $("local-moving").hidden = runningOn !== "server";
  }
}

async function refreshStatus() {
  const before = botStatus && `${botStatus.location} ${botStatus.paused}`;
  try {
    applyStatus(await api("status"));
  } catch {
    return;
  }
  if ($("dashboard").hidden) return;
  renderDashboard();
  if (before !== `${botStatus.location} ${botStatus.paused}`) restartedStats();
}

function restartedStats() {
  // uptime comes from when the bot itself started, which takes a few seconds after a start or stop
  loadStats();
  setTimeout(loadStats, 8000);
}

const DIAGRAM_ICONS = {
  slack: '<rect x="2" y="2" width="28" height="28" rx="7"/><path d="M12 8v16M20 8v16M8 12h16M8 20h16"/>',
  server: '<rect x="4" y="4" width="24" height="10" rx="2.5"/><rect x="4" y="18" width="24" height="10" rx="2.5"/><path d="M9 9h.01M9 23h.01M14 9h9M14 23h9"/>',
  computer: '<rect x="3" y="5" width="26" height="17" rx="2.5"/><path d="M12 27h8M16 22v5"/>',
};
// a round head with ears and an antenna, like the monkey
// the same shapes twice, outlined underneath and filled on top, so the outline only shows around the outside
const BOT_SHAPE = '<line x1="0" y1="-12" x2="0" y2="-19.5"/><circle cx="0" cy="-21" r="2.8"/>'
  + '<circle cx="-13" cy="0" r="3.4"/><circle cx="13" cy="0" r="3.4"/><circle r="13"/>';
const BOT = `<g class="outline">${BOT_SHAPE}</g><g class="body">${BOT_SHAPE}</g>`
  + '<path class="eyes" d="M-4.8 -2h.01M4.8 -2h.01"/><path class="face" d="M-5 3.5q5 3.4 10 0"/>';

function diagram(nodes, links, vertical) {
  // nodes run left to right, or top to bottom on a narrow screen, and a host that runs the bot carries a bot badge
  const width = 168, height = 104, gap = vertical ? 56 : 92, pad = 28;
  const at = (i) => (vertical ? { x: pad, y: pad + i * (height + gap) } : { x: pad + i * (width + gap), y: pad });
  const parts = links.map((link, i) => {
    const a = at(i), b = at(i + 1);
    const [x1, y1, x2, y2] = vertical
      ? [a.x + width / 2, a.y + height + 4, b.x + width / 2, b.y - 4]
      : [a.x + width + 4, a.y + height / 2, b.x - 4, b.y + height / 2];
    const label = !link.label ? "" : vertical
      ? `<text class="dia-label" x="${x1 + 10}" y="${(y1 + y2) / 2 + 4}" style="text-anchor: start">${link.label}</text>`
      : `<text class="dia-label" x="${(x1 + x2) / 2}" y="${y1 - 10}">${link.label}</text>`;
    return `<line class="dia-link ${link.tone}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>${label}`;
  });
  nodes.forEach((node, i) => {
    const { x, y } = at(i);
    const bot = node.bot ? `<g class="dia-bot ${node.bot}" transform="translate(${x + width - 6} ${y + 6})">${BOT}</g>` : "";
    parts.push(`<g class="dia-node ${node.tone || ""}"><rect x="${x}" y="${y}" width="${width}" height="${height}" rx="12"/>`
      + `<g class="icon ${node.dim ? "dim" : ""}" transform="translate(${x + width / 2 - 16} ${y + 14})">${DIAGRAM_ICONS[node.icon]}</g>`
      + `<text class="dia-title" x="${x + width / 2}" y="${y + 68}" text-anchor="middle">${node.title}</text>`
      + `<text class="dia-sub" x="${x + width / 2}" y="${y + 86}" text-anchor="middle">${node.sub}</text></g>${bot}`);
  });
  const last = at(nodes.length - 1);
  // the room above is for the bot's antenna, the bottom needs less
  const w = last.x + width + pad + 8, h = last.y + height + 12;
  // drawn at its own size so every layout keeps the same text size, shrinking only when it has to
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" style="max-width: 100%" xmlns="http://www.w3.org/2000/svg">${parts.join("")}</svg>`;
}

function renderDashboard() {
  const here = mode === "local";
  const app = botStatus.app;
  const onServer = botStatus.location === "server";
  const appOnline = here || Boolean(app && app.running);
  const seen = app && app.seen ? ago(Date.now() / 1000 - app.seen) : null;
  const tone = botStatus.paused ? "off" : !onServer && !appOnline ? "warn" : "on";
  $("dash-pill").textContent = { on: "Running", off: "Stopped", warn: "Offline" }[tone];
  $("dash-pill").className = `dash-pill ${tone}`;
  const detail = here ? "" : appOnline ? `Local app online ${seen}` : seen ? `Local app last online ${seen}` : "";
  $("dash-detail").hidden = !detail;
  $("dash-detail").textContent = detail;
  toast(botStatus.notes);
  // the boxes never change, the bot badge and the borders and lines show what is happening
  const running = tone === "on";
  const host = { bot: tone, tone: tone === "off" ? "" : tone };
  const linked = running ? "link" : "";
  const nodes = [
    { icon: "slack", title: "Slack", sub: "Your channel and List", tone: linked },
    { icon: "server", title: "Our Server", sub: "www.haagbot.lol", ...(onServer ? host : { tone: linked }) },
    // the computer only lights up when it is the one running the bot
    { icon: "computer", title: "Your Computer", sub: "Local App", ...(onServer ? {} : host), dim: !appOnline },
  ];
  const links = [
    { tone: tone === "on" ? "on" : tone === "warn" && onServer ? "warn" : "" },
    { tone: !appOnline || onServer || tone === "off" ? "" : tone, label: onServer ? "" : "Slack calls" },
  ];
  const drawn = diagram(nodes, links, $("dash-diagram").clientWidth < 520);
  // redrawn only when something changed, so the pulse is not restarted by every refresh
  if (drawnDiagram !== drawn) {
    $("dash-diagram").innerHTML = drawn;
    drawnDiagram = drawn;
  }
  const where = onServer ? "Our Server (www.haagbot.lol)" : "Your Computer (Local App)";
  $("dash-diagram").setAttribute("aria-label", `${$("dash-pill").textContent} on ${where}.`);
  $("dash-stop").hidden = botStatus.paused;
  $("dash-start-server").hidden = !botStatus.paused;
  $("dash-start-computer").hidden = !botStatus.paused || !here;
}

const toasted = new Set();

function toast(notes) {
  // each note shows once as a pop-up, and is cleared on the server once the last one goes
  for (const note of notes || []) {
    if (toasted.has(note)) continue;
    toasted.add(note);
    const close = el("button", { type: "button", textContent: "×", ariaLabel: "Dismiss" });
    const item = el("div", { className: "toast" }, [el("span", { textContent: note }), close]);
    const leave = () => {
      if (item.classList.contains("leaving")) return;
      item.classList.add("leaving");
      setTimeout(() => item.remove(), 300);
      if (!$("toasts").querySelector(".toast:not(.leaving)")) api("bot", { action: "dismiss" }).catch(() => {});
    };
    close.addEventListener("click", leave);
    setTimeout(leave, 12000);
    $("toasts").append(item);
  }
}

let stats = null;
let statsAt = 0;

function day(iso, options) {
  return new Date(iso).toLocaleDateString(undefined, options || { month: "short", day: "numeric" });
}

function span(seconds) {
  // a duration as its two largest units
  const units = [["d", 86400], ["h", 3600], ["m", 60], ["s", 1]];
  const parts = [];
  for (const [unit, size] of units) {
    if (seconds >= size || parts.length) parts.push(`${Math.floor(seconds / size)}${unit}`);
    seconds %= size;
    if (parts.length === 2) break;
  }
  return parts.join(" ") || "0s";
}

async function loadStats() {
  try {
    stats = (await api("data?kind=summary")).data;
    statsAt = Date.now();
    $("stats-note").hidden = true;
    $("stats-body").hidden = false;
  } catch (error) {
    stats = null;
    $("stats-note").textContent = error.message;
    $("stats-note").hidden = false;
    $("stats-body").hidden = true;
    return;
  }
  const monday = new Date(`${stats.monday}T00:00:00`);
  const friday = new Date(monday.getTime() + 4 * 86400000);
  $("stat-week").textContent = `Week ${stats.week}`;
  $("stat-week-dates").textContent = `${day(monday)} – ${day(friday)}`;
  $("stat-due-at").textContent = new Date(stats.due).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  $("stat-filed").textContent = `${stats.filed_this_week} / ${stats.researchers}`;
  const latest = stats.latest;
  $("stats-latest").replaceChildren(...(latest ? logLine(latest) : [document.createTextNode("Nothing yet.")]));
  tick();
}

function tick() {
  // the countdown and uptime count every second between refreshes
  if (!stats) return;
  const now = Date.now() / 1000;
  $("stat-due").textContent = span(Math.max(0, new Date(stats.due).getTime() / 1000 - now));
  const running = stats.started && botStatus && !botStatus.paused;
  $("stat-uptime").textContent = running ? span(now - stats.started) : "—";
  const since = new Date(stats.started * 1000);
  const today = since.toDateString() === new Date().toDateString();
  $("stat-uptime-since").textContent = !running ? "not running"
    : `since ${today ? since.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : day(since)}`;
}

function logLine(entry) {
  const when = new Date(entry.at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const about = [entry.who, entry.week ? `week ${entry.week}` : ""].filter(Boolean).join(", ");
  return [el("span", { className: "when", textContent: when }), document.createTextNode(about ? `${entry.what}: ${about}` : entry.what)];
}

async function showLog() {
  rememberView("dashboard");
  showOnly("log-view");
  $("log-list").replaceChildren();
  try {
    const entries = (await api("data?kind=log")).data;
    $("log-note").hidden = entries.length > 0;
    $("log-note").textContent = "Nothing has happened yet.";
    $("log-list").replaceChildren(...entries.map((entry) => el("li", {}, logLine(entry))));
  } catch (error) {
    $("log-note").textContent = error.message;
    $("log-note").hidden = false;
  }
}

let dbTables = [];

async function showDatabase() {
  rememberView("dashboard");
  showOnly("db-view");
  try {
    dbTables = (await api("data?kind=db")).data;
    $("db-note").hidden = true;
  } catch (error) {
    dbTables = [];
    $("db-note").textContent = error.message;
    $("db-note").hidden = false;
  }
  $("db-band").hidden = !dbTables.length;
  $("db-tabs").replaceChildren(...dbTables.map((table, i) => {
    const tab = el("button", { type: "button", className: "secondary", role: "tab", textContent: `${table.name} (${table.rows.length})` });
    tab.addEventListener("click", () => showTable(i));
    return tab;
  }));
  const first = dbTables.findIndex((t) => t.name === "reports");
  if (dbTables.length) showTable(first < 0 ? 0 : first);
}

function showTable(index) {
  const table = dbTables[index];
  [...$("db-tabs").children].forEach((tab, i) => tab.setAttribute("aria-selected", String(i === index)));
  $("db-band").style.setProperty("--cols", table.columns.length);
  $("db-head").replaceChildren(...table.columns.map((c) => el("th", { scope: "col", textContent: c, title: c })));
  $("db-rows").replaceChildren(...(table.rows.length
    ? table.rows.map((row) => el("tr", {}, row.map((value) => {
      const text = value === null ? "" : String(value);
      return el("td", { textContent: text, title: text });
    })))
    : [el("tr", {}, [el("td", { colSpan: table.columns.length, textContent: "Empty" })])]));
  fitBand($("db-band"));
}

async function control(action, where) {
  $("dash-error").hidden = true;
  try {
    applyStatus(await api("bot", { action, where }));
  } catch (error) {
    $("dash-error").textContent = error.message;
    $("dash-error").hidden = false;
  }
  renderDashboard();
  if (action !== "dismiss") restartedStats();
}

function rememberView(view) {
  try {
    sessionStorage.setItem("view", view);
  } catch {}
}

function rememberedView() {
  try {
    return sessionStorage.getItem("view");
  } catch {
    return null;
  }
}

const VIEWS = ["modes", "tabs", "local-panel", "form", "settings-bar", "dashboard", "log-view", "db-view"];

function showOnly(...ids) {
  for (const id of VIEWS) $(id).hidden = !ids.includes(id);
  document.body.classList.toggle("wide", ids.some((id) => ["dashboard", "log-view", "db-view"].includes(id)));
}

function showDashboard() {
  rememberView("dashboard");
  showOnly("dashboard");
  loadStats();
  $("title-text").textContent = "Weekly Report Bot";
  renderDashboard();
  clearInterval(statusTimer);
  statusTimer = setInterval(refreshStatus, 5000);
}

function showSettings() {
  rememberView("settings");
  clearInterval(statusTimer);
  showOnly();
  $("settings-bar").hidden = !botStatus.location;
  $("title-text").textContent = "Configure Weekly Report Bot";
  if (mode === "local") {
    $("form").hidden = false;
  } else if (botStatus.location) {
    // settings opened from the website start on the website's own tab
    showTab(false);
  } else {
    const chosen = chosenMode(runningOn);
    if (chosen) showTab(chosen === "local");
    else $("modes").hidden = false;
  }
  progress();
}

function runningElsewhere() {
  // the bot is running in the other place, so it has to stop there before it can start here
  if (!botStatus || botStatus.paused) return false;
  return mode === "local" ? botStatus.location === "server" : botStatus.location === "computer";
}

function runningHere() {
  if (!botStatus || botStatus.paused) return false;
  return mode === "local" ? botStatus.location === "computer" : botStatus.location === "server";
}

function renderMoveNote() {
  const elsewhere = runningElsewhere();
  const text = elsewhere && (mode === "local" ? "This bot is running on our server (www.haagbot.lol) right now." : "This bot is running in the local app right now.");
  $("move-note").hidden = !text;
  $("move-note").textContent = text || "";
  $("save").textContent = elsewhere ? (mode === "local" ? "Stop it on www.haagbot.lol" : "Stop it in the local app")
    : runningHere() ? "Save changes" : "Save and start";
}

function progress() {
  if (!invited) return;
  renderMoveNote();
  lockSteps();
  const done = {
    channel: channelOk,
    list: listOk && !schemaProblems().length,
  };
  for (const section of document.querySelectorAll("[data-step]")) {
    section.classList.toggle("done", done[section.dataset.step]);
  }
  // stopping the bot elsewhere needs nothing filled in
  const found = runningElsewhere() ? [] : blockers();
  $("save").setAttribute("aria-disabled", String(found.length > 0));
  $("save-wrap").classList.toggle("blocked", found.length > 0);
  $("save-blockers").replaceChildren(...found.map((text) => el("li", { textContent: text })));
}

let invitedChannels = [];
let channelLists = [];
let listsTimer;

function channelLabel(c) {
  if (c.name) return `#${c.name}`;
  const when = new Date(c.invited * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  return `Private channel, invited ${when}`;
}

function renderChannelPicker() {
  // a dropdown only when the person invited the bot to more than one channel
  const many = invitedChannels.length > 1;
  $("channel-name").hidden = many;
  $("channel-select").hidden = !many;
  if (many) {
    $("channel-select").replaceChildren(...invitedChannels.map((c) => el("option", { value: c.id, textContent: channelLabel(c) })));
    $("channel-select").value = $("channel").value;
  }
}

function useChannel(id) {
  $("channel").value = id;
  saveDraft();
  renderChannelPicker();
  checkChannel();
}

async function watchForInvite() {
  // the server hears the bot being invited, so channels appear here for whoever invited it
  $("channel-select").addEventListener("change", () => {
    // researchers belong to the channel they were picked from, so a different channel starts with none
    researchers = [];
    $("list").value = "";
    checkList();
    useChannel($("channel-select").value);
  });
  while (true) {
    try {
      const found = (await api("joined")).channels;
      const changed = found.map((c) => c.id + c.name).join() !== invitedChannels.map((c) => c.id + c.name).join();
      invitedChannels = found;
      if (!$("channel").value.trim() && found.length) useChannel(found[0].id);
      else if (changed) renderChannelPicker();
    } catch {}
    await new Promise((r) => setTimeout(r, 4000));
  }
}

function showChannel(name, connected) {
  // the name is only known once a file has been shared in the channel, otherwise a placeholder stands in
  $("channel-name").textContent = name ? `#${name}` : connected ? "Private channel" : "Not invited yet";
  $("channel-name").classList.toggle("placeholder", !name);
}

async function checkChannel(refresh = false) {
  // a refresh keeps the form unlocked while the active users reload
  if (!refresh) {
    channelOk = false;
    progress();
    if (!$("channel").value.trim()) {
      showChannel(null, false);
      return status("channel-status", "Waiting for you to invite the bot…", null);
    }
    status("channel-status", "Checking…", true);
  }
  try {
    const data = await api("channel", { link: $("channel").value, week_one_monday: $("week_one_monday").value });
    members = data.people;
    channelOk = true;
    renderPeople();
    showChannel(data.name, true);
    status("channel-status", `Connected · ${members.length} active user${members.length === 1 ? "" : "s"}`, true);
    if (!refresh) loadLists();
  } catch (error) {
    channelOk = false;
    members = [];
    status("channel-status", error.message, false);
  }
  renderPeople();
  progress();
}

async function loadLists() {
  clearTimeout(listsTimer);
  const channel = $("channel").value.trim();
  if (!channel) return;
  try {
    channelLists = (await api(`lists?channel=${encodeURIComponent(channel)}`)).lists;
  } catch (error) {
    channelLists = [];
    status("list-status", error.message, false);
  }
  if (channel !== $("channel").value.trim()) return;
  if (!$("list").value && channelLists.length === 1) chooseList(channelLists[0].id);
  renderLists();
  // a list shared after the page opened appears without a reload
  if (!channelLists.length) listsTimer = setTimeout(loadLists, 5000);
}

function renderLists() {
  const current = $("list").value;
  const options = [el("option", { value: "", textContent: channelLists.length ? "Choose a List" : "No Lists shared in this channel yet" })];
  for (const list of channelLists) options.push(el("option", { value: list.id, textContent: list.title }));
  if (current && !channelLists.some((list) => list.id === current)) options.push(el("option", { value: current, textContent: "Current Progress Tracker" }));
  $("list-select").replaceChildren(...options);
  $("list-select").value = current;
  $("list-select").disabled = options.length === 1;
}

function chooseList(id) {
  $("list").value = id;
  saveDraft();
  renderLists();
  checkList();
}

const TYPE_NAMES = { text: "Text", user: "Person", select: "Select", checkbox: "Checkbox", date: "Date" };
let listPreview = null;
let assigned = {};

function schemaProblems() {
  if (!listPreview) return [];
  const problems = [];
  for (const [key, role] of Object.entries(listPreview.roles)) {
    if (!role.optional && !assigned[key]) {
      problems.push(`Add a column for ${role.label}, e.g. a ${TYPE_NAMES[role.types[0]]} column named “${role.label}”.`);
    }
  }
  const status = listPreview.columns.find((c) => c.id === assigned.COL_STATUS);
  if (status && status.type === "select" && !status.options.includes("No Report")) {
    problems.push(`Add a “No Report” option to the ${status.name} column. The bot writes it when a researcher misses the deadline.`);
  }
  return problems;
}

function renderSchema() {
  $("schema").hidden = !listPreview;
  if (!listPreview) return;
  const columns = listPreview.columns;
  const roleOf = Object.fromEntries(Object.entries(assigned).map(([key, id]) => [id, key]));
  $("schema-head").replaceChildren(...columns.map((c) => el("th", { scope: "col", className: roleOf[c.id] ? "used" : "" }, [
    el("span", { textContent: c.name }), el("span", { className: "col-type", textContent: TYPE_NAMES[c.type] || c.type }),
  ])));
  $("schema-pickers").replaceChildren(...columns.map((c) => {
    const select = el("select", { ariaLabel: `What the bot uses the ${c.name} column for` }, [el("option", { value: "", textContent: "Not used" })]);
    for (const [key, role] of Object.entries(listPreview.roles)) {
      if (role.types.includes(c.type)) select.append(el("option", { value: key, textContent: role.label }));
    }
    select.value = roleOf[c.id] || "";
    select.addEventListener("change", () => {
      // a role sits in one column at a time, so picking it here frees the column it was in
      for (const [key, id] of Object.entries(assigned)) if (id === c.id || key === select.value) delete assigned[key];
      if (select.value) assigned[select.value] = c.id;
      renderSchema();
      renderLocalModel();
    });
    return el("td", { className: roleOf[c.id] ? "used" : "" }, [select]);
  }));
  $("schema-rows").replaceChildren(...(listPreview.rows.length
    ? listPreview.rows.map((row) => el("tr", {}, row.map((value, i) => el("td", { className: roleOf[columns[i].id] ? "used" : "", textContent: value.split("\n")[0], title: value }))))
    : [el("tr", {}, [el("td", { colSpan: columns.length, textContent: "No rows yet" })])]));
  $("schema-band").style.setProperty("--cols", columns.length);
  fitBand();
  const problems = schemaProblems();
  $("schema-guide").hidden = !problems.length;
  $("schema-problems").replaceChildren(...problems.map((p) => el("li", { textContent: p })));
}

function fitBand(band = $("schema-band")) {
  // the card shows behind the table only where the table reaches past it
  const card = band.closest("section");
  const style = getComputedStyle(card);
  band.classList.toggle("wide", band.offsetWidth > card.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
}

async function checkList(current = {}) {
  listOk = false;
  listPreview = null;
  renderSchema();
  progress();
  if (!$("list").value.trim()) return status("list-status", "", true);
  status("list-status", "Checking…", true);
  try {
    listPreview = await api("list", { link: $("list").value });
    const ids = new Set(listPreview.columns.map((c) => c.id));
    const saved = Object.fromEntries(Object.entries(current).filter(([key, id]) => listPreview.roles[key] && ids.has(id)));
    assigned = Object.keys(saved).length ? saved : { ...listPreview.chosen };
    listOk = true;
    status("list-status", "Connected", true);
  } catch (error) {
    status("list-status", error.message, false);
  }
  renderSchema();
  renderLocalModel();
}

let draftKey = "setup-draft";

function loadDraft() {
  try {
    return JSON.parse(localStorage.getItem(draftKey)) || {};
  } catch {
    return {};
  }
}

function saveDraft() {
  try {
    localStorage.setItem(draftKey, JSON.stringify({ channel: $("channel").value, list: $("list").value, researchers }));
  } catch {}
}

async function save(event) {
  event.preventDefault();
  if (runningElsewhere()) {
    $("save-error").hidden = true;
    try {
      applyStatus(await api("bot", { action: "stop" }));
    } catch (error) {
      $("save-error").textContent = error.message;
      $("save-error").hidden = false;
    }
    return progress();
  }
  if (blockers().length) return;
  $("save").disabled = true;
  $("save-error").hidden = true;
  try {
    await api("save", {
      channel: $("channel").value,
      list: $("list").value,
      columns: assigned,
      researchers,
      send_reminders: $("send_reminders").checked,
      reminder_text: $("reminder_text").value,
      deadline_time: $("deadline_time").value,
      week_one_monday: $("week_one_monday").value,
      react_enabled: $("react_enabled").checked,
      react_emoji: reactEmoji,
      classifier: $("classifier").value,
      summarizer: $("summarizer").value,
      llm_provider: $("llm_provider").value,
      llm_model: modelValue(STAGES.report),
      summary_provider: $("summary_provider").value,
      summary_model: modelValue(STAGES.summary),
      linker: $("linker").value,
      links_provider: $("links_provider").value,
      links_model: modelValue(STAGES.links),
      github_repo: $("github_repo").value,
      github_handles: Object.fromEntries(researchers.filter((uid) => githubHandles[uid]).map((uid) => [uid, githubHandles[uid]])),
      week_pattern: $("week_pattern").value,
      local_llm_url: $("local_llm_url").value,
      local_llm_model: $("local_llm_model").value,
      local_llm_key: $("local_llm_key").value,
      local_llm_temperature: $("local_llm_temperature").value,
      local_llm_max_tokens: $("local_llm_max_tokens").value,
      local_llm_timeout: $("local_llm_timeout").value,
    });
    try {
      localStorage.removeItem(draftKey);
    } catch {}
    $("save").disabled = false;
    applyStatus(await api("status"));
    showDashboard();
  } catch (error) {
    $("save-error").textContent = error.message;
    $("save-error").hidden = false;
    $("save").disabled = false;
    progress();
  }
}

function wire() {
  // everything that needs no data from the bot, so the form is usable the moment the page appears
  $("send_reminders").checked = true;
  $("deadline_time").value = "17:00";
  $("week_one_monday").value = "2026-08-24";
  $("react_enabled").checked = true;
  setEmoji("robot_face");
  $("emoji-button").addEventListener("click", () => toggleEmoji($("emoji-popover").hidden));
  $("emoji-picker").addEventListener("emoji-click", (event) => {
    setEmoji(event.detail.emoji.shortcodes[0], event.detail.unicode);
    toggleEmoji(false);
  });
  $("classifier").addEventListener("change", () => {
    renderExamples();
    renderRules();
    renderLocalModel();
  });
  for (const kind of ["input", "change"]) $("form").addEventListener(kind, progress);
  $("summarizer").addEventListener("change", renderLocalModel);
  $("linker").addEventListener("change", renderLocalModel);
  $("github_repo").addEventListener("input", loadGithub);

  $("send_reminders").addEventListener("change", renderReminder);
  for (const stage of Object.values(STAGES)) {
    $(stage.installBlock).querySelector(".check-installed").addEventListener("click", () => checkInstalled(stage));
    $(stage.provider).addEventListener("change", () => {
      stage.wanted = "";
      renderLocalModel();
    });
    $(stage.model).addEventListener("change", () => {
      renderOtherModel(stage);
      if ($(stage.model).value === OTHER_MODEL) $(stage.other).focus();
      else stage.wanted = $(stage.model).value;
    });
    $(stage.other).addEventListener("input", () => (stage.wanted = $(stage.other).value.trim()));
  }
  for (const id of ["local_llm_url", "local_llm_key"]) $(id).addEventListener("input", suggestLocalModels);
  $("edit-definition").addEventListener("click", () => openDefinition("report"));
  $("edit-summary-definition").addEventListener("click", () => openDefinition("summary"));
  $("edit-links-definition").addEventListener("click", () => openDefinition("links"));
  $("definition-form").addEventListener("submit", saveDefinition);
  $("definition-cancel").addEventListener("click", () => $("definition-dialog").close());
  $("definition-reset").addEventListener("click", () => {
    $("definition-text").value = $("definition-text").dataset.default || $("definition-text").value;
  });
  $("copy-invite").addEventListener("click", async () => {
    const box = $("copy-invite");
    try {
      await navigator.clipboard.writeText($("invite-command").textContent);
    } catch {
      getSelection().selectAllChildren($("invite-command"));
      return;
    }
    box.classList.add("done");
    clearTimeout(box.timer);
    box.timer = setTimeout(() => box.classList.remove("done"), 1500);
  });
  $("list-select").addEventListener("change", () => chooseList($("list-select").value));
  $("schema-again").addEventListener("click", () => checkList({ ...assigned }));
  window.addEventListener("resize", () => {
    if (listPreview) fitBand();
    if (!$("db-view").hidden) fitBand($("db-band"));
  });
  $("week_one_monday").addEventListener("change", () => (channelOk ? checkChannel(true) : progress()));
  $("form").addEventListener("submit", save);
  $("dash-settings").addEventListener("click", showSettings);
  $("to-dashboard").addEventListener("click", showDashboard);
  $("log-back").addEventListener("click", showDashboard);
  $("db-back").addEventListener("click", showDashboard);
  $("open-log").addEventListener("click", showLog);
  $("open-db").addEventListener("click", showDatabase);
  setInterval(() => !$("dashboard").hidden && tick(), 1000);
  // the stats refresh every half minute while the dashboard shows
  setInterval(() => !$("dashboard").hidden && Date.now() - statsAt > 30000 && loadStats(), 5000);
  window.addEventListener("resize", () => !$("dashboard").hidden && renderDashboard());
  $("dash-stop").addEventListener("click", () => control("stop"));
  $("dash-start-computer").addEventListener("click", () => control("start", "computer"));
  $("dash-start-server").addEventListener("click", () => control("start", "server"));
  $("add-researcher").addEventListener("click", () => toggleMenu($("researcher-menu").hidden));
  $("researcher-filter").addEventListener("input", renderMenu);
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".adder")) toggleMenu(false);
    if (!event.target.closest(".emoji-field")) toggleEmoji(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      toggleMenu(false);
      toggleEmoji(false);
    }
  });
  renderPeople();
  progress();
  // the app on a computer has no choice to make, the server page waits for its first answer to know
  if (location.hostname === "bot.localhost" || location.protocol === "bot:") $("form").hidden = false;
}

async function start() {
  wire();
  let data;
  try {
    data = await api("state");
  } catch (error) {
    $("form").hidden = true;
    $("fatal").textContent = error.message;
    $("fatal").hidden = false;
    return;
  }
  invited = data.user;
  workspace = data.workspace;
  draftKey = `setup-draft-${data.team}`;
  const v = data.values;
  const editing = Object.keys(v).length > 0;
  for (const [id, found] of [["classifier", data.classifiers], ["summarizer", data.summarizers], ["linker", data.linkers]]) {
    $(id).append(...found.map((m) => el("option", { value: m.name, textContent: m.description })));
  }
  classifierInfo = data.classifiers;
  summarizerInfo = data.summarizers;
  linkerInfo = data.linkers;
  providerNotes = data.provider_notes;
  exampleClassifiers = new Set(data.classifiers.filter((m) => m.examples).map((m) => m.name));
  rulesClassifiers = new Set(data.classifiers.filter((m) => m.rules).map((m) => m.name));
  mode = data.mode;
  appStatus = data.app;
  applyStatus(data.status);
  rules = data.rules;
  // saved settings fill in only what the person has not already started typing
  const fill = (id, value) => {
    if (!$(id).value) $(id).value = value;
  };
  const draft = loadDraft();
  $("classifier").value = v.CLASSIFIER || (data.classifiers[0] && data.classifiers[0].name) || "";
  $("summarizer").value = v.SUMMARIZER || LLM_SUMMARIZER;
  $("linker").value = v.LINKER || LLM_LINKS;
  githubHandles = Object.fromEntries((v.GITHUB_HANDLES || "").split(",").filter(Boolean).map((pair) => pair.split("=")));
  if (v.GITHUB_REPO) fill("github_repo", `https://github.com/${v.GITHUB_REPO}`);
  STAGES.links.wanted = v.LINKS_MODEL || "";
  fill("week_pattern", v.WEEK_PATTERN || data.week_pattern);
  STAGES.report.wanted = v.LLM_MODEL || "";
  STAGES.summary.wanted = v.SUMMARY_MODEL || "";
  renderLocalModel();
  if (v.LLM_PROVIDER) $("llm_provider").value = v.LLM_PROVIDER;
  if (v.SUMMARY_PROVIDER) $("summary_provider").value = v.SUMMARY_PROVIDER;
  if (v.LINKS_PROVIDER) $("links_provider").value = v.LINKS_PROVIDER;
  loadGithub();
  for (const key of ["URL", "MODEL", "KEY", "TEMPERATURE", "MAX_TOKENS", "TIMEOUT"]) fill(`local_llm_${key.toLowerCase()}`, v[`LOCAL_LLM_${key}`] || "");
  fill("reminder_text", data.reminder_text);
  installed = data.installed || null;
  renderLocalModel();
  suggestLocalModels();
  fill("channel", draft.channel || v.SLACK_CHANNEL_ID || "");
  fill("list", draft.list || v.SLACK_LIST_ID || "");
  if (editing) {
    $("send_reminders").checked = v.SEND_REMINDERS === "true";
    $("reminder_text").value = v.REMINDER_TEXT || data.reminder_text;
    $("deadline_time").value = v.DEADLINE_TIME || "17:00";
    $("week_one_monday").value = v.WEEK_ONE_MONDAY || "2026-08-24";
    $("react_enabled").checked = v.REACT_ENABLED === "true";
    setEmoji(v.REACT_EMOJI || "robot_face");
  }
  $("classifier").value = v.CLASSIFIER || (data.classifiers[0] && data.classifiers[0].name) || "";
  $("summarizer").value = v.SUMMARIZER || LLM_SUMMARIZER;
  $("linker").value = v.LINKER || LLM_LINKS;
  renderRules();
  renderLocalModel();
  renderReminder();
  if (mode !== "local") {
    $("tab-server").addEventListener("click", () => showTab(false));
    $("tab-local").addEventListener("click", () => showTab(true));
    $("choose-server").addEventListener("click", () => showTab(false));
    $("choose-local").addEventListener("click", () => showTab(true));
    renderApp();
    lock(appStatus);
    setInterval(watchApp, 4000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") watchApp();
    });
  }
  // a team that is already set up opens on its dashboard
  // the app opens on its settings when it was just connected from the website
  if (location.hash === "#configure") {
    rememberView("settings");
    history.replaceState(null, "", location.pathname + location.search);
  }
  if (botStatus.location && rememberedView() !== "settings") showDashboard();
  else showSettings();
  if (!researchers.length) researchers = draft.researchers || (v.RESEARCHERS || "").split(" ").filter(Boolean);
  exampleFiles = await api("examples");
  renderPeople();
  if ($("channel").value && !channelOk) await checkChannel();
  watchForInvite();
  if ($("list").value && !listOk) await checkList(Object.fromEntries(Object.keys(v).filter((k) => k.startsWith("COL_")).map((k) => [k, v[k]])));
  progress();
}

start();
