/* Journal — front-end logic */
"use strict";

const $ = (s) => document.querySelector(s);
const layout = $("#layout");
const editor = $("#editor");
const pre = $("#highlight");
const previewEl = $("#preview");
const statusEl = $("#save-status");
const btnCreateToday = $("#btn-create-today");
const btnCreateTomorrow = $("#btn-create-tomorrow");
const treeEl = $("#tree");

marked.setOptions({ gfm: true, breaks: true });

let currentFile = null;
let fileHash = null; // hash of currentFile as last loaded/saved
let dirty = false;
let saveTimer = null;
let expanded = new Set(JSON.parse(localStorage.getItem("journal.expanded") || "[]"));

/* ---------- theme ---------- */

$("#btn-theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("journal_theme", next);
});

/* ---------- panel toggles ---------- */

const mqMobile = matchMedia("(max-width: 767px)");
const btnTree = $("#btn-tree");
const btnPreview = $("#btn-preview");

function syncPanelButtons() {
  btnTree.setAttribute("aria-pressed", String(
    mqMobile.matches ? layout.classList.contains("tree-open")
                     : !layout.classList.contains("no-tree")));
  btnPreview.setAttribute("aria-pressed", String(
    mqMobile.matches ? layout.classList.contains("preview-open")
                     : !layout.classList.contains("no-preview")));
}

function closeMobilePanels() {
  if (!mqMobile.matches) return;
  layout.classList.remove("tree-open", "preview-open");
  syncPanelButtons();
}

btnTree.addEventListener("click", () => {
  if (mqMobile.matches) {
    layout.classList.toggle("tree-open");
    layout.classList.remove("preview-open");
  } else {
    layout.classList.toggle("no-tree");
  }
  syncPanelButtons();
});

btnPreview.addEventListener("click", () => {
  if (mqMobile.matches) {
    layout.classList.toggle("preview-open");
    layout.classList.remove("tree-open");
    if (layout.classList.contains("preview-open")) renderPreview();
  } else {
    layout.classList.toggle("no-preview");
    if (!layout.classList.contains("no-preview")) renderPreview();
  }
  syncPanelButtons();
});

/* ---------- api ---------- */

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (r.status === 401) { location.href = "/"; throw new Error("unauthenticated"); }
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    const err = new Error(typeof d.detail === "string" ? d.detail : "Request failed");
    err.status = r.status;
    err.body = d;
    throw err;
  }
  return r.json();
}

/* ---------- save status / autosave ---------- */

function setStatus(mode) {
  const text = { saved: "Saved", dirty: "Editing…", error: "Save failed", idle: "" }[mode];
  statusEl.textContent = text;
  statusEl.className = "save-status" +
    (mode === "dirty" ? " dirty" : mode === "error" ? " error" : "");
}

async function saveNow(force = false) {
  if (!dirty || !currentFile) return;
  try {
    const d = await api("/api/file", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: currentFile, content: editor.value,
                             base_hash: force ? null : fileHash })
    });
    fileHash = d.hash;
    dirty = false;
    setStatus("saved");
  } catch (err) {
    if (!force && err.status === 409) { showConflict(err.body); return; }
    setStatus("error");
  }
}

function scheduleSave() {
  if (!currentFile) return;
  dirty = true;
  setStatus("dirty");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 1000);
}

window.addEventListener("beforeunload", () => { if (dirty) saveNow(); });
document.addEventListener("visibilitychange", () => {
  if (document.hidden && dirty) saveNow();
});

/* ---------- save conflict (file changed on another device) ---------- */

let conflictData = null;
const conflictModal = $("#conflict-modal");

function showConflict(body) {
  conflictData = body;
  conflictModal.hidden = false;
  $("#conflict-accept").focus();
}

function closeConflict() {
  conflictModal.hidden = true;
  conflictData = null;
}

$("#conflict-accept").addEventListener("click", () => {
  if (!conflictData) return;
  editor.value = conflictData.content;
  fileHash = conflictData.hash;
  renderHighlight();
  renderPreview();
  dirty = false;
  setStatus("saved");
  closeConflict();
});

$("#conflict-keep").addEventListener("click", () => {
  if (!conflictData) return;
  closeConflict();
  saveNow(true); // write this device's version over the one on disk
});

/* ---------- editor highlight (overlay behind transparent textarea) ---------- */

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function highlightInline(line) {
  let s = esc(line);
  const stash = [];
  // protect inline code so other rules never touch its contents
  s = s.replace(/`[^`\n]*`/g, (m) => {
    stash.push(`<span class="md-code">${m}</span>`);
    return `\u0000${stash.length - 1}\u0000`;
  });
  s = s.replace(/\*\*[^*\n]+\*\*/g, (m) => `<span class="md-bold">${m}</span>`);
  s = s.replace(/__[^_\n]+__/g, (m) => `<span class="md-bold">${m}</span>`);
  s = s.replace(/(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)/g,
    (m) => `<span class="md-italic">${m}</span>`);
  s = s.replace(/(^|[^A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])/g,
    (m, a, b) => a + `<span class="md-italic">_${b}_</span>`);
  s = s.replace(/\[[^\]\n]*\]\([^)\n]*\)/g, (m) => `<span class="md-link">${m}</span>`);
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => stash[+i]);
}

function renderHighlight() {
  const lines = editor.value.split("\n");
  let inFence = false;
  const out = lines.map((line) => {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      return `<span class="md-fence">${esc(line)}</span>`;
    }
    if (inFence) return `<span class="md-fence">${esc(line)}</span>`;
    if (/^#{1,6}\s/.test(line)) return `<span class="md-heading">${esc(line)}</span>`;
    if (/^\s*>/.test(line))
      return `<span class="md-quote">${highlightInline(line)}</span>`;
    return highlightInline(line)
      .replace(/^(\s*(?:[-*+]\s|\d+[.)]\s))/,
        (m) => `<span class="md-marker">${m}</span>`);
  });
  let html = out.join("\n");
  if (editor.value.endsWith("\n")) html += " "; // keep trailing line height
  pre.innerHTML = html;
  pre.scrollTop = editor.scrollTop;
}

editor.addEventListener("input", () => {
  renderHighlight();
  renderPreview();
  scheduleSave();
});
editor.addEventListener("scroll", () => { pre.scrollTop = editor.scrollTop; });
editor.addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    e.preventDefault();
    editor.setRangeText("  ", editor.selectionStart, editor.selectionEnd, "end");
    renderHighlight();
    renderPreview();
    scheduleSave();
  }
});

/* ---------- toolbar ---------- */

const TOOLS = [
  { id: "h1", label: "H1", title: "Heading 1 (# )", block: true, text: "# " },
  { id: "h2", label: "H2", title: "Heading 2 (## )", block: true, text: "## " },
  { id: "h3", label: "H3", title: "Heading 3 (### )", block: true, text: "### " },
  { id: "bold", label: "B", cls: "bold", title: "Bold (**)**", wrap: ["**", "**"], ph: "text" },
  { id: "italic", label: "I", cls: "italic", title: "Italic (*)", wrap: ["*", "*"], ph: "text" },
  { id: "code", title: "Inline code (`)`", icon: '<svg viewBox="0 0 24 24"><path d="M9 8l-4 4 4 4M15 8l4 4-4 4"/></svg>', wrap: ["`", "`"], ph: "code" },
  { id: "quote", label: "\u201C", cls: "quote", title: "Quote (> )", block: true, text: "> " },
  { id: "list", title: "Bullet list (- )", icon: '<svg viewBox="0 0 24 24"><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.3" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="4.5" cy="18" r="1.3" fill="currentColor" stroke="none"/></svg>', block: true, text: "- " },
  { id: "link", title: "Link ([text](url))", icon: '<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>', link: true },
  { id: "fence", title: "Code block", icon: '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 10l2.5 2L8 14M13 14h3"/></svg>', block: true, wrap: ["```\n", "\n```"], ph: "" },
  { id: "hr", title: "Divider (---)", icon: '<svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg>', block: true, text: "---\n" },
];

const toolbarEl = $("#toolbar");
for (const t of TOOLS) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "tb-btn" + (t.cls ? " " + t.cls : "");
  b.title = t.title;
  b.setAttribute("aria-label", t.title);
  if (t.icon) b.innerHTML = t.icon; else b.textContent = t.label;
  b.addEventListener("click", () => insertTool(t));
  toolbarEl.appendChild(b);
}
// keep editor focus (and its selection) while pressing toolbar buttons
toolbarEl.addEventListener("mousedown", (e) => e.preventDefault());

// Block-level inserts (headings, quotes, ...) must start a new paragraph:
// return the separator to prepend when the cursor is mid-line.
function ensureLineStart() {
  const before = editor.value.slice(0, editor.selectionStart);
  const lineHead = before.slice(before.lastIndexOf("\n") + 1);
  return /^\s*$/.test(lineHead) ? "" : "\n\n";
}

function insertTool(t) {
  const s = editor.selectionStart;
  const e = editor.selectionEnd;
  const sel = editor.value.slice(s, e);
  const pre = t.block ? ensureLineStart() : "";
  const at = s + pre.length;
  const v = editor.value;

  if (t.link) {
    if (sel) {
      editor.value = v.slice(0, s) + `[${sel}](url)` + v.slice(e);
      const p = s + sel.length + 3;
      afterInsert(p, p + 3); // select "url"
    } else {
      editor.value = v.slice(0, s) + "[text](url)" + v.slice(e);
      afterInsert(s + 1, s + 5); // select "text"
    }
    return;
  }
  if (t.wrap) {
    const inner = sel || t.ph;
    editor.value = v.slice(0, s) + pre + t.wrap[0] + inner + t.wrap[1] + v.slice(e);
    afterInsert(at + t.wrap[0].length, at + t.wrap[0].length + inner.length);
    return;
  }
  const keep = e - s; // selection, if any, lands after the inserted marker
  editor.value = v.slice(0, s) + pre + t.text + v.slice(s);
  afterInsert(at + t.text.length, at + t.text.length + keep);
}

function afterInsert(start, end) {
  editor.setSelectionRange(start, end);
  editor.focus();
  renderHighlight();
  renderPreview();
  scheduleSave();
}

/* ---------- preview ---------- */

function renderPreview() {
  if (layout.classList.contains("no-preview")) return; // spec: no rendering while hidden
  const text = editor.value;
  if (!text.trim()) { previewEl.innerHTML = ""; return; }
  try {
    previewEl.innerHTML = marked.parse(text);
  } catch {
    previewEl.textContent = text; // never show a rendering error
  }
}

/* ---------- tree ---------- */

function persistExpanded() {
  localStorage.setItem("journal.expanded", JSON.stringify([...expanded]));
}

function markActive() {
  treeEl.querySelectorAll(".tree-row.file.active")
    .forEach((r) => r.classList.remove("active"));
  if (!currentFile) return;
  const row = treeEl.querySelector(`.tree-row.file[data-name="${CSS.escape(currentFile)}"]`);
  if (row) row.classList.add("active");
}

function renderNode(node, depth, path) {
  const wrap = document.createElement("div");
  if (node.type === "dir") {
    const key = path ? `${path}/${node.name}` : node.name;
    const open = expanded.has(key);
    const row = document.createElement("div");
    row.className = `tree-row dir-level-${Math.min(depth, 1)}` + (open ? " open" : "");
    const chev = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chev.setAttribute("class", "chev");
    chev.setAttribute("viewBox", "0 0 24 24");
    chev.innerHTML = '<path d="M9 6l6 6-6 6"/>';
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = node.name;
    row.append(chev, name);
    row.addEventListener("click", () => {
      expanded.has(key) ? expanded.delete(key) : expanded.add(key);
      persistExpanded();
      loadTree();
    });
    wrap.appendChild(row);
    if (open) {
      const kids = document.createElement("ul");
      kids.className = "tree-children";
      node.children.forEach((c) => kids.appendChild(renderNode(c, depth + 1, key)));
      wrap.appendChild(kids);
    }
  } else {
    const row = document.createElement("div");
    row.className = "tree-row file";
    row.dataset.name = node.name;
    const spacer = document.createElement("span");
    spacer.className = "chev-spacer";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = node.name.split("/").pop();
    const bin = document.createElement("button");
    bin.className = "file-bin";
    bin.title = "Delete entry";
    bin.innerHTML = '<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m5 5v6m4-6v6"/></svg>';
    bin.addEventListener("click", (e) => {
      e.stopPropagation();
      const label = node.name.split("/").pop();
      if (!confirm(`Delete ${label}?`)) return;
      api(`/api/file?name=${encodeURIComponent(node.name)}`, { method: "DELETE" })
        .then(async () => {
          if (currentFile === node.name) {
            currentFile = null;
            fileHash = null;
            editor.value = "";
            renderHighlight();
            renderPreview();
            dirty = false;
            setStatus("idle");
            localStorage.removeItem("journal.last");
          }
          await loadTree();
        })
        .catch((err) => alert(err.message));
    });
    row.append(spacer, name, bin);
    row.addEventListener("click", () => openFile(node.name));
    wrap.appendChild(row);
  }
  return wrap;
}

async function loadTree() {
  let data;
  try {
    data = await api("/api/tree");
  } catch (e) {
    if (String(e.message).includes("unauthenticated")) return;
    return;
  }
  treeEl.innerHTML = "";
  if (!data.length) {
    const empty = document.createElement("div");
    empty.className = "tree-empty";
    empty.textContent = "No entries yet. Click “Create Entry” to start one.";
    treeEl.appendChild(empty);
    return;
  }
  data.forEach((n) => treeEl.appendChild(renderNode(n, 0, "")));
  markActive();
}

/* ---------- files ---------- */

async function openFile(name) {
  const d = await api("/api/file?name=" + encodeURIComponent(name));
  currentFile = name;
  fileHash = d.hash;
  editor.value = d.content;
  renderHighlight();
  renderPreview();
  dirty = false;
  setStatus("saved");
  localStorage.setItem("journal.last", name);
  markActive();
  closeMobilePanels();
}

async function createEntry(offset) {
  const btn = offset === 1 ? btnCreateTomorrow : btnCreateToday;
  btn.disabled = true;
  try {
    const d = await api(`/api/create?offset=${offset}`, { method: "POST" });
    const parts = d.name.split("/");
    expanded.add(parts[0]);
    expanded.add(`${parts[0]}/${parts[1]}`);
    persistExpanded();
    await loadTree();
    await openFile(d.name);
  } finally {
    btn.disabled = false;
  }
}

btnCreateToday.addEventListener("click", () => createEntry(0));
btnCreateTomorrow.addEventListener("click", () => createEntry(1));

/* ---------- init ---------- */

syncPanelButtons();
setStatus("idle");
loadTree();
const last = localStorage.getItem("journal.last");
if (last) openFile(last).catch(() => {});
