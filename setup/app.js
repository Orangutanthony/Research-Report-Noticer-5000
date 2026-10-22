"use strict";

const $ = (id) => document.getElementById(id);
let members = [];
let invited = null;
let roles = {};
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
  $(id).textContent = text;
  $(id).className = `status ${ok ? "ok" : "bad"}`;
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
  return members.find((m) => m.id === id) || (invited && invited.id === id ? invited : { id, name: id, photo: null });
}

function personCard(who, role, onRemove) {
  const card = el("div", { className: "person" }, [
    avatar(who),
    el("div", { className: "who" }, [el("div", { className: "name", textContent: who.name }), el("div", { className: "role", textContent: role })]),
  ]);
  if (onRemove) {
    const remove = el("button", { type: "button", className: "remove", textContent: "×", ariaLabel: `Remove ${who.name}` });
    remove.addEventListener("click", onRemove);
    card.append(remove);
  }
  return card;
}

function renderPeople() {
  $("admin-card").replaceWith(Object.assign(personCard(invited, "Admin (You)"), { id: "admin-card" }));
  const cards = researchers.map((id) => personCard(person(id), "Researcher", () => {
    researchers = researchers.filter((x) => x !== id);
    saveDraft();
    renderPeople();
  }));
  $("researcher-grid").replaceChildren(...cards);
  renderMenu();
  renderExamples();
}

function renderMenu() {
  const text = $("researcher-filter").value.trim().toLowerCase();
  const open = members.filter((m) => !researchers.includes(m.id));
  const matches = open.filter((m) => m.name.toLowerCase().includes(text));
  const empty = !open.length ? "Everyone who has posted is added." : "No matches.";
  $("researcher-options").replaceChildren(...(matches.length ? matches.map((m) => {
    const option = el("div", { className: "option", role: "option" }, [avatar(m), el("span", { textContent: m.name })]);
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
  if (!researchers.length) {
    $("examples").replaceChildren(el("p", { className: "empty", textContent: "Add researchers above to upload their example reports." }));
    return;
  }
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
      el("div", { className: "example-who" }, [avatar(who), el("span", { className: "name", textContent: who.name }), el("span", { className: "count", textContent: count })]),
      zone,
    ];
    if (rows.length || busy.length) parts.push(el("ul", { className: "files" }, [...rows, ...busy]));
    if (exampleErrors[uid]) parts.push(el("p", { className: "status bad", textContent: exampleErrors[uid] }));
    return el("div", { className: "example-row" }, parts);
  }));
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

function progress() {
  if (!invited) return;
  lockSteps();
  const done = {
    channel: channelOk,
    list: listOk && Object.keys(roles).every((key) => $(`col-${key}`).value),
  };
  for (const section of document.querySelectorAll("[data-step]")) {
    section.classList.toggle("done", done[section.dataset.step]);
  }
  const count = Object.values(done).filter(Boolean).length;
  const total = Object.keys(done).length;
  $("save").disabled = count < total;
}

async function checkChannel(refresh = false) {
  // a refresh keeps the form unlocked while the active users reload
  if (!refresh) {
    channelOk = false;
    progress();
    if (!$("channel").value.trim()) return status("channel-status", "", true);
    status("channel-status", "Checking…", true);
  }
  try {
    const data = await api("channel", { link: $("channel").value, week_one_monday: $("week_one_monday").value });
    members = data.people;
    channelOk = true;
    renderPeople();
    status("channel-status", `Connected · ${members.length} active user${members.length === 1 ? "" : "s"}`, true);
  } catch (error) {
    channelOk = false;
    members = [];
    status("channel-status", error.message, false);
  }
  renderPeople();
  progress();
}

function columnSelect(key, role, current) {
  const select = el("select", { id: `col-${key}` }, [el("option", { value: "", textContent: "Choose a column" })]);
  for (const c of role.candidates) select.append(el("option", { value: c.id, textContent: c.name }));
  select.value = current || role.chosen || "";
  select.addEventListener("change", progress);
  return el("div", {}, [el("label", { htmlFor: select.id, textContent: role.label }), select]);
}

async function checkList(current = {}) {
  listOk = false;
  roles = {};
  $("columns").replaceChildren();
  progress();
  if (!$("list").value.trim()) return status("list-status", "", true);
  status("list-status", "Checking…", true);
  try {
    const data = await api("list", { link: $("list").value });
    roles = data.roles;
    $("columns").append(...Object.entries(roles).map(([key, role]) => columnSelect(key, role, current[key])));
    listOk = true;
    status("list-status", "Connected · columns matched below", true);
  } catch (error) {
    status("list-status", error.message, false);
  }
  progress();
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

function checkOnPaste(id, check) {
  let timer;
  $(id).addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => check(), 500);
  });
}

async function save(event) {
  event.preventDefault();
  $("save").disabled = true;
  $("save-error").hidden = true;
  const columns = Object.fromEntries(Object.keys(roles).map((key) => [key, $(`col-${key}`).value]));
  try {
    const data = await api("save", {
      channel: $("channel").value,
      list: $("list").value,
      columns,
      researchers,
      send_reminders: $("send_reminders").checked,
      deadline_time: $("deadline_time").value,
      week_one_monday: $("week_one_monday").value,
      react_enabled: $("react_enabled").checked,
      react_emoji: reactEmoji,
      classifier: $("classifier").value,
      summarizer: $("summarizer").value,
    });
    try {
      localStorage.removeItem(draftKey);
    } catch {}
    $("form").hidden = true;
    $("done-text").textContent = `Team ${data.team} is saved. The bot starts watching your channel within a minute. You can come back to this page in this browser to change it.`;
    $("done").hidden = false;
  } catch (error) {
    $("save-error").textContent = error.message;
    $("save-error").hidden = false;
    progress();
  }
}

async function start() {
  let data;
  try {
    data = await api("state");
  } catch (error) {
    $("intro").hidden = true;
    $("fatal").textContent = error.message;
    $("fatal").hidden = false;
    return;
  }
  invited = data.user;
  draftKey = `setup-draft-${data.team}`;
  const v = data.values;
  const editing = Object.keys(v).length > 0;
  $("intro").textContent = editing ? `Change the settings of team ${data.team}.` : `Set up team ${data.team}.`;
  for (const [id, names] of [["classifier", data.classifiers], ["summarizer", data.summarizers]]) {
    $(id).append(...names.map((n) => el("option", { value: n, textContent: n.replace("processors.", "") })));
  }
  const draft = loadDraft();
  $("channel").value = draft.channel || v.SLACK_CHANNEL_ID || "";
  $("list").value = draft.list || v.SLACK_LIST_ID || "";
  $("send_reminders").checked = editing ? v.SEND_REMINDERS === "true" : true;
  $("deadline_time").value = v.DEADLINE_TIME || "17:00";
  $("week_one_monday").value = v.WEEK_ONE_MONDAY || "2026-08-24";
  $("react_enabled").checked = editing ? v.REACT_ENABLED === "true" : true;
  setEmoji(v.REACT_EMOJI || "robot_face");
  $("emoji-button").addEventListener("click", () => toggleEmoji($("emoji-popover").hidden));
  $("emoji-picker").addEventListener("emoji-click", (event) => {
    setEmoji(event.detail.emoji.shortcodes[0], event.detail.unicode);
    toggleEmoji(false);
  });
  $("classifier").value = v.CLASSIFIER || data.classifiers[0] || "";
  $("summarizer").value = v.SUMMARIZER || "processors.manual_summarizer";
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
  checkOnPaste("channel", checkChannel);
  checkOnPaste("list", () => checkList());
  for (const id of ["channel", "list"]) $(id).addEventListener("input", saveDraft);
  $("week_one_monday").addEventListener("change", () => (channelOk ? checkChannel(true) : progress()));
  $("form").addEventListener("submit", save);
  $("form").hidden = false;
  researchers = draft.researchers || (v.RESEARCHERS || "").split(" ").filter(Boolean);
  exampleFiles = await api("examples");
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
  if ($("channel").value) await checkChannel();
  if ($("list").value) await checkList(Object.fromEntries(Object.keys(v).filter((k) => k.startsWith("COL_")).map((k) => [k, v[k]])));
  progress();
}

start();
