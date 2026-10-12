"use strict";
const $ = (id) => document.getElementById(id);
const LIMIT = 1048576;
const state = {
  corpus: [],
  targets: [],
  result: null,
  model: null,
  candidate: -1,
  target: null,
  document: null,
  selected: null,
  workspaces: new Map(),
  busy: false,
  serial: 0,
};
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
function el(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}
function size(n) {
  return Number(n).toLocaleString() + " B";
}
function json(x) {
  return JSON.stringify(x, null, 2);
}
function bytes(base64) {
  const s = atob(base64);
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}
function textBytes(value) {
  for (let i = 0; i < value.length; i++) {
    const n = value.charCodeAt(i);
    if (n >= 0xd800 && n <= 0xdbff) {
      const next = value.charCodeAt(++i);
      if (!(next >= 0xdc00 && next <= 0xdfff))
        throw Error(
          "UTF-8 text contains an incomplete Unicode character. Use a complete character or Hex mode.",
        );
    } else if (n >= 0xdc00 && n <= 0xdfff)
      throw Error(
        "UTF-8 text contains an incomplete Unicode character. Use a complete character or Hex mode.",
      );
  }
  return encoder.encode(value);
}
function b64(data) {
  let s = "";
  for (let i = 0; i < data.length; i += 8192)
    s += String.fromCharCode(...data.subarray(i, i + 8192));
  return btoa(s);
}
function hex(data) {
  return Array.from(data, (x) => x.toString(16).padStart(2, "0")).join("");
}
function unhex(value) {
  const s = value.replace(/\s/g, "");
  if (s.length % 2 || !/^[0-9a-f]*$/i.test(s))
    throw Error(
      "Use complete hex byte pairs, for example 72 69 76 65 72 00. Spaces and line breaks are allowed.",
    );
  if (s.length > 2 * LIMIT) throw Error("The payload exceeds 1 MiB.");
  return Uint8Array.from(s.match(/../g) || [], (x) => parseInt(x, 16));
}
function prettyHex(value) {
  return (value.match(/.{1,32}/g) || [""])
    .map((line) => (line.match(/../g) || []).join(" "))
    .join("\n");
}
function tagText(value) {
  return String.fromCharCode(...unhex(value));
}
function message(value, kind = "") {
  $("status").textContent = value;
  $("status").className = "status" + (kind ? " " + kind : "");
}
async function request(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  let result;
  try {
    result = await response.json();
  } catch {
    throw Error(
      "The local server did not return JSON. Refresh after checking that Reclay is running.",
    );
  }
  if (!response.ok)
    throw Error(
      result.error || result.message || "The operation could not be completed.",
    );
  return result;
}
function controls() {
  $("infer").disabled = state.busy || state.corpus.length < 2;
  $("corpus-files").disabled = state.busy;
  $("target-upload").disabled = state.busy;
  $("target-file").disabled = state.busy;
  for (const button of document.querySelectorAll(
    "[data-example], .candidate, .remove-file",
  ))
    button.disabled = state.busy;
  $("preview").disabled =
    state.busy || !state.selected || !!state.selected.children;
  $("payload-editor").disabled = state.busy;
  $("discard-edit").disabled =
    state.busy || !state.selected || !!state.selected.children;
  $("mode-hex").disabled = state.busy;
  $("mode-text").disabled = state.busy;
  $("download-codec").disabled = state.busy || !state.model;
  $("download-schema").disabled = state.busy || !state.model;
  $("download-file").disabled =
    state.busy || !state.document || !workspace()?.preview;
}
async function operation(label, action) {
  if (state.busy) return;
  state.busy = true;
  controls();
  message(label, "busy");
  try {
    await action();
  } catch (error) {
    message(error.message || String(error), "error");
  } finally {
    state.busy = false;
    controls();
  }
}
function file(name, data) {
  const raw = bytes(data);
  if (!raw.length || raw.length > LIMIT)
    throw Error("Every file must contain between 1 byte and 1 MiB.");
  return { id: ++state.serial, name, data, size: raw.length };
}
async function readFile(nativeFile) {
  if (!nativeFile.size || nativeFile.size > LIMIT)
    throw Error(
      nativeFile.name + ": files must contain between 1 byte and 1 MiB.",
    );
  return file(
    nativeFile.name,
    b64(new Uint8Array(await nativeFile.arrayBuffer())),
  );
}
function invalidate() {
  state.result = null;
  state.model = null;
  state.candidate = -1;
  state.document = null;
  state.selected = null;
  $("candidates").replaceChildren();
  $("candidate-empty").hidden = false;
  $("candidate-status").hidden = true;
  $("model-details").hidden = true;
  $("target-controls").hidden = true;
  $("document-area").hidden = true;
  $("parse-empty").hidden = false;
  $("evidence-summary").hidden = true;
  $("experiment-details").hidden = true;
  renderEditor();
}
function renderCorpus() {
  $("corpus-count").textContent = state.corpus.length + " files";
  $("corpus-list").replaceChildren();
  for (const sample of state.corpus) {
    const item = el("li", "file-item");
    const copy = el("div", "file-copy");
    copy.append(
      el("strong", "", sample.name),
      el("small", "", size(sample.size)),
    );
    const remove = el("button", "remove-file", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove " + sample.name);
    remove.addEventListener("click", () => {
      state.corpus = state.corpus.filter((x) => x.id !== sample.id);
      invalidate();
      renderCorpus();
      controls();
      message("Evidence changed. Infer again to evaluate the new collection.");
    });
    item.append(el("span", "file-dot"), copy, remove);
    $("corpus-list").append(item);
  }
  controls();
}
async function addCorpus(files) {
  await operation("Reading your evidence collection…", async () => {
    if (state.corpus.length + files.length > 8)
      throw Error(
        "The collection can contain at most 8 files. Remove an existing file first.",
      );
    const incoming = await Promise.all(Array.from(files, readFile));
    if (
      [...state.corpus, ...incoming].reduce((n, x) => n + x.size, 0) >
      4 * LIMIT
    )
      throw Error(
        "The evidence collection exceeds 4 MiB. Use fewer or smaller files.",
      );
    state.corpus.push(...incoming);
    invalidate();
    renderCorpus();
    message(
      state.corpus.length +
        " files ready. At least two different files are needed for inference.",
    );
  });
}
$("corpus-files").addEventListener("change", (event) => {
  const files = Array.from(event.target.files);
  event.target.value = "";
  if (files.length) addCorpus(files);
});
const dropzone = $("dropzone");
for (const name of ["dragenter", "dragover"])
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    if (!state.busy) dropzone.classList.add("drag-over");
  });
for (const name of ["dragleave", "drop"])
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("drag-over");
  });
dropzone.addEventListener("drop", (event) => {
  if (!state.busy && event.dataTransfer.files.length)
    addCorpus(Array.from(event.dataTransfer.files));
});
for (const button of document.querySelectorAll("[data-example]"))
  button.addEventListener("click", () =>
    operation("Loading independent example files…", async () => {
      const data = await request(
        "/api/examples?family=" + encodeURIComponent(button.dataset.example),
      );
      const samples = data.samples.map((x) => file(x.name, x.data));
      if (
        samples.length > 8 ||
        samples.reduce((n, x) => n + x.size, 0) > 4 * LIMIT
      )
        throw Error("The example corpus exceeds the collection limits.");
      state.corpus = samples;
      state.targets = [];
      state.target = null;
      invalidate();
      $("example-description").textContent = data.description || "";
      document
        .querySelectorAll("[data-example]")
        .forEach((x) => x.classList.toggle("active", x === button));
      renderCorpus();
      message(
        "Example evidence loaded. Infer structure to see the surviving explanations.",
      );
    }),
  );
function modelSummary(model) {
  const g = model.grammar;
  return (
    g.tagWidth +
    "-byte tags · " +
    g.lengthWidth +
    "-byte " +
    g.endian +
    "-endian lengths · " +
    g.alignment +
    "-byte alignment" +
    (g.checksum ? " · CRC32 " + g.checksum.scope : " · no checksum inferred")
  );
}
function renderCandidates() {
  $("candidate-empty").hidden = true;
  $("candidates").replaceChildren();
  const models = state.result.models || [];
  models.forEach((model, i) => {
    const button = el("button", "candidate");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.candidate === i));
    const heading = el("div", "candidate-title");
    heading.append(
      el(
        "span",
        "",
        "Candidate " + (i + 1) + " · blocks at byte " + model.start,
      ),
      el(
        "span",
        "candidate-select",
        state.candidate === i ? "Selected" : "Choose →",
      ),
    );
    button.append(
      heading,
      el("small", "", modelSummary(model)),
      el(
        "small",
        "",
        model.prefix.growthSafe
          ? "Resize supported by this model"
          : "Opaque changing header · size-preserving edits only",
      ),
    );
    button.addEventListener("click", () =>
      operation("Parsing with candidate " + (i + 1) + "…", async () => {
        state.model = model;
        state.candidate = i;
        state.selected = null;
        renderCandidates();
        $("model-json").textContent = json(model);
        $("model-details").hidden = false;
        $("target-controls").hidden = false;
        renderTargets();
        if (!state.target) state.target = state.corpus[0] || state.targets[0];
        if (state.target) {
          $("target-file").value = String(state.target.id);
          await parseTarget();
        }
      }),
    );
    $("candidates").append(button);
  });
  const truncated = !!state.result.truncated;
  const unique = state.result.status === "unique" && !truncated;
  const reasons = state.result.truncationReasons || [];
  const reasonText = reasons.length
    ? " Bound reported: " +
      reasons
        .map((x) => (typeof x === "string" ? x : JSON.stringify(x)))
        .join("; ") +
      "."
    : "";
  $("candidate-status").hidden = false;
  $("candidate-status").classList.toggle("incomplete", truncated);
  $("candidate-status").textContent = truncated
    ? "Incomplete search. " +
      models.length +
      " candidates survived the explored hypotheses, but other explanations may exist. No uniqueness claim is established." +
      reasonText +
      " Inspect Experiments & limits before choosing."
    : unique
      ? "One candidate matches this evidence within the supported hypothesis family. Choose it explicitly to inspect a file."
      : models.length
        ? models.length +
          " candidates fit the collection. The observations do not identify one unique grammar. Choose a candidate to inspect its explanation."
        : "No supported tagged-block grammar fits this collection. Try related files with different sizes, or inspect the suggested experiments.";
  $("evidence-summary").hidden = false;
  $("evidence-summary").replaceChildren(
    el(
      "strong",
      "",
      (truncated ? "Incomplete search · " : "") +
        models.length +
        (models.length === 1
          ? " surviving explanation"
          : " surviving explanations"),
    ),
    el(
      "p",
      "small-copy",
      state.corpus.length +
        " observed files. Matching this corpus is evidence, not a guarantee for every file." +
        (truncated
          ? " Search bounds were reached; more hypotheses may survive." +
            reasonText
          : ""),
    ),
  );
  $("experiments").textContent = json({
    experiments: state.result.experiments || [],
    limits: state.result.limits || {},
    truncated,
    truncationReasons: reasons,
  });
  $("experiment-details").hidden = false;
}
$("infer").addEventListener("click", () =>
  operation(
    "Testing structural hypotheses against every observation…",
    async () => {
      const result = await request("/api/infer", {
        samples: state.corpus.map(({ name, data }) => ({ name, data })),
      });
      state.result = result;
      state.model = null;
      state.candidate = -1;
      state.document = null;
      state.selected = null;
      $("model-details").hidden = true;
      $("target-controls").hidden = true;
      $("document-area").hidden = true;
      $("parse-empty").hidden = false;
      renderCandidates();
      renderEditor();
      message(
        result.truncated
          ? "Incomplete search: a search bound was reached. Surviving candidates do not establish uniqueness. Inspect Experiments & limits for the reported reasons."
          : (result.models || []).length
            ? "Inference finished. Choose an interpretation explicitly before editing."
            : "No supported structure matched. The experiments panel shows what evidence would help.",
      );
    },
  ),
);
function renderTargets() {
  const select = $("target-file");
  select.replaceChildren();
  for (const sample of [...state.corpus, ...state.targets]) {
    const option = el("option", "", sample.name + " · " + size(sample.size));
    option.value = String(sample.id);
    select.append(option);
  }
  if (state.target) select.value = String(state.target.id);
}
function workspace() {
  if (!state.model || !state.target) return null;
  const key = state.model.id + ":" + state.target.id;
  if (!state.workspaces.has(key))
    state.workspaces.set(key, {
      drafts: new Map(),
      edits: new Map(),
      preview: null,
      selected: null,
    });
  return state.workspaces.get(key);
}
async function parseTarget() {
  state.document = null;
  state.selected = null;
  $("document-area").hidden = true;
  $("parse-empty").hidden = false;
  $("target-summary").textContent =
    "Original: " + size(state.target.size) + " · awaiting parse";
  renderEditor();
  const ws = workspace();
  const data = ws.preview ? ws.preview.data : state.target.data;
  state.document = await request("/api/parse", { model: state.model, data });
  $("target-summary").textContent =
    "Original: " +
    size(state.target.size) +
    (ws.preview
      ? " · displaying edited preview"
      : " · displaying original bytes");
  renderDocument();
  if (ws.selected) {
    const node = allNodes().find(
      (x) => pathKey(x.path) === pathKey(ws.selected),
    );
    if (node) selectNode(node);
  }
  renderChanges();
  message(
    "Parsed " +
      state.target.name +
      " with candidate " +
      (state.candidate + 1) +
      ". Select a block to inspect its raw payload.",
  );
}
$("target-file").addEventListener("change", () =>
  operation("Opening this working file…", async () => {
    state.target = [...state.corpus, ...state.targets].find(
      (x) => String(x.id) === $("target-file").value,
    );
    await parseTarget();
  }),
);
$("target-upload").addEventListener("change", (event) => {
  const incoming = event.target.files[0];
  event.target.value = "";
  if (!incoming) return;
  operation("Parsing your fresh working file…", async () => {
    const sample = await readFile(incoming);
    state.targets.push(sample);
    state.target = sample;
    renderTargets();
    await parseTarget();
  });
});
function pathKey(path) {
  return path.join("/");
}
function allNodes() {
  if (!state.document) return [];
  const result = [];
  const nest = state.model.nesting || [];
  function walk(nodes, grammar, offset, parentPath) {
    for (let i = 0; i < nodes.length; i++) {
      const chunk = nodes[i];
      const header = grammar.tagWidth + grammar.lengthWidth;
      const payloadLength = chunk.payloadHex.length / 2;
      const checksumBytes = grammar.checksum ? 4 : 0;
      const paddingLength = chunk.paddingHex.length / 2;
      const node = {
        ...chunk,
        path: [...parentPath, i],
        grammar,
        offset,
        payloadOffset: offset + header,
        payloadLength,
        headerLength: header,
        checksumBytes,
        paddingLength,
        length: header + payloadLength + checksumBytes + paddingLength,
      };
      result.push(node);
      if (chunk.children) {
        const rule = nest.find(
          (x) => x.tagHex.toLowerCase() === chunk.tagHex.toLowerCase(),
        );
        if (rule)
          walk(
            chunk.children,
            rule.grammar,
            node.payloadOffset + (chunk.containerPrefixHex || "").length / 2,
            node.path,
          );
      }
      offset += node.length;
    }
  }
  walk(state.document.chunks, state.model.grammar, state.model.start, []);
  return result;
}
function renderDocument() {
  const nodes = allNodes();
  const fileSize = state.document.evidence.size;
  $("document-area").hidden = false;
  $("parse-empty").hidden = true;
  $("document-size").textContent = size(fileSize);
  $("chunk-tree").replaceChildren();
  $("byte-map").replaceChildren();
  $("map-legend").replaceChildren();
  nodes.forEach((node, i) => {
    const selected =
      state.selected && pathKey(node.path) === pathKey(state.selected.path);
    const button = el(
      "button",
      "chunk-row depth-" + Math.min(node.path.length - 1, 8),
    );
    button.type = "button";
    button.setAttribute("aria-pressed", String(!!selected));
    button.setAttribute(
      "aria-label",
      "Inspect block " + tagText(node.tagHex) + " at " + pathKey(node.path),
    );
    const title = el("div", "node-title");
    const tag = el("div", "node-tag");
    if (node.path.length > 1) tag.append(el("span", "node-branch", "↳"));
    tag.append(el("span", "", tagText(node.tagHex)));
    title.append(
      tag,
      el(
        "div",
        "node-location",
        "byte " +
          node.offset +
          " → " +
          (node.offset + node.length) +
          (node.children
            ? " · " + node.children.length + " nested blocks"
            : ""),
      ),
    );
    button.append(title, el("span", "node-length", size(node.payloadLength)));
    button.addEventListener("click", () => selectNode(node));
    $("chunk-tree").append(button);
    if (node.path.length === 1) {
      const color = i % 6;
      const weight = Math.max(
        1,
        Math.min(100, Math.round((100 * node.length) / fileSize)),
      );
      const bar = el(
        "button",
        "map-block color-" + color + " weight-" + weight,
      );
      bar.type = "button";
      bar.title =
        tagText(node.tagHex) +
        " · bytes " +
        node.offset +
        "–" +
        (node.offset + node.length) +
        " · " +
        size(node.length);
      bar.setAttribute("aria-label", bar.title);
      bar.setAttribute("aria-pressed", String(!!selected));
      bar.addEventListener("click", () => selectNode(node));
      $("byte-map").append(bar);
      const legend = el("span", "legend-item");
      legend.append(
        el("i", "legend-swatch color-" + color),
        el("span", "", tagText(node.tagHex)),
      );
      $("map-legend").append(legend);
    }
  });
  $("document-facts").replaceChildren(
    el("span", "fact", nodes.length + " parsed blocks"),
    el("span", "fact", "prefix " + state.model.start + " B"),
    el(
      "span",
      "fact",
      state.model.grammar.checksum
        ? "CRC32 validated by parser"
        : "No checksum in selected grammar",
    ),
  );
}
function selectNode(node) {
  state.selected = node;
  workspace().selected = node.path;
  renderDocument();
  renderEditor();
  controls();
}
function draftFor(node) {
  const ws = workspace();
  const key = pathKey(node.path);
  if (!ws.drafts.has(key))
    ws.drafts.set(key, { mode: "hex", value: prettyHex(node.payloadHex) });
  return ws.drafts.get(key);
}
function draftBytes(draft) {
  if (draft.mode === "hex") return unhex(draft.value);
  const body = textBytes(draft.value);
  if (!draft.trailingZero) return body;
  const result = new Uint8Array(body.length + 1);
  result.set(body);
  return result;
}
function renderEditor() {
  $("editor-empty").hidden = !!state.selected;
  $("editor-body").hidden = !state.selected;
  $("editor-path").textContent = state.selected
    ? state.selected.path.join(" / ")
    : "no selection";
  if (!state.selected) {
    renderChanges();
    return;
  }
  const node = state.selected;
  const draft = draftFor(node);
  $("selected-tag").textContent = tagText(node.tagHex);
  $("selected-range").textContent = "byte " + node.payloadOffset;
  $("selected-facts").replaceChildren();
  const facts = [
    ["Header", size(node.headerLength)],
    ["Payload", size(node.payloadLength)],
    ["Alignment padding", size(node.paddingLength)],
  ];
  if (node.grammar.checksum) {
    const raw = bytes(
      workspace().preview ? workspace().preview.data : state.target.data,
    );
    const crc = raw.slice(
      node.payloadOffset + node.payloadLength,
      node.payloadOffset + node.payloadLength + 4,
    );
    facts.push(
      ["Stored CRC32", hex(crc) + " (" + node.grammar.checksum.endian + ")"],
      ["CRC32 scope", node.grammar.checksum.scope],
    );
  }
  for (const [name, value] of facts)
    $("selected-facts").append(el("div", "", name + ": " + value));
  if (node.children)
    $("selected-facts").append(
      el(
        "div",
        "",
        "Container payload is read-only. Choose a nested leaf block to edit; its container length is rebuilt automatically.",
      ),
    );
  $("payload-editor").readOnly = !!node.children;
  $("payload-editor").value = draft.value;
  $("mode-hex").setAttribute("aria-pressed", String(draft.mode === "hex"));
  $("mode-text").setAttribute("aria-pressed", String(draft.mode === "text"));
  $("payload-label").textContent =
    draft.mode === "hex" ? "PAYLOAD BYTES" : "PAYLOAD AS UTF-8";
  $("editor-help").textContent =
    draft.mode === "hex"
      ? "Hex pairs edit exact payload bytes, including 00 terminators. Structural lengths and inferred checksums are rebuilt."
      : draft.trailingZero
        ? "The original trailing 00 byte is preserved after your UTF-8 text. Use Hex to inspect or remove it."
        : "UTF-8 edits raw text bytes. File-specific content rules are your responsibility; use Hex to inspect exact bytes.";
  refreshDraft();
  renderChanges();
}
function refreshDraft() {
  if (!state.selected) return;
  if (state.selected.children) {
    $("payload-size").textContent = size(state.selected.payloadLength);
    $("draft-status").textContent =
      "Container inspection only. Select a nested leaf block to edit its bytes.";
    return;
  }
  const draft = draftFor(state.selected);
  try {
    const raw = draftBytes(draft);
    $("payload-size").textContent = size(raw.length);
    $("draft-status").textContent =
      (hex(raw) === state.selected.payloadHex.toLowerCase()
        ? "Payload matches the displayed file."
        : "Unbuilt draft. Build the file to see repaired structure.") +
      (!state.model.prefix.growthSafe
        ? " This candidate permits size-preserving edits only."
        : "");
  } catch (error) {
    $("payload-size").textContent = "invalid hex";
    $("draft-status").textContent = error.message;
  }
}
$("payload-editor").addEventListener("input", () => {
  if (state.selected) {
    draftFor(state.selected).value = $("payload-editor").value;
    refreshDraft();
  }
});
function mode(next) {
  if (!state.selected) return;
  const draft = draftFor(state.selected);
  if (draft.mode === next) return;
  try {
    const raw = draftBytes(draft);
    let textBytes = raw;
    const trailingZero =
      next === "text" && raw.length > 0 && raw[raw.length - 1] === 0;
    if (trailingZero) textBytes = raw.subarray(0, raw.length - 1);
    if (next === "text" && textBytes.includes(0))
      throw Error(
        "This payload contains interior 00 bytes. Keep Hex mode to preserve them exactly.",
      );
    const value =
      next === "hex" ? prettyHex(hex(raw)) : decoder.decode(textBytes);
    if (next === "text" && hex(encoder.encode(value)) !== hex(textBytes))
      throw Error("Text conversion would change these bytes. Keep Hex mode.");
    draft.mode = next;
    draft.value = value;
    draft.trailingZero = trailingZero;
    renderEditor();
  } catch (error) {
    message(
      next === "text"
        ? "This payload cannot be edited as plain UTF-8 text. " + error.message
        : error.message,
      "error",
    );
  }
}
$("mode-hex").addEventListener("click", () => mode("hex"));
$("mode-text").addEventListener("click", () => mode("text"));
$("preview").addEventListener("click", () =>
  operation(
    "Rebuilding payloads, lengths and inferred checksums…",
    async () => {
      if (!state.selected || state.selected.children)
        throw Error(
          "Choose a leaf block to edit. Container payloads are read-only.",
        );
      const ws = workspace();
      const key = pathKey(state.selected.path);
      const payloadHex = hex(draftBytes(draftFor(state.selected)));
      const edits = new Map(ws.edits);
      edits.set(key, { path: state.selected.path, payloadHex });
      const result = await request("/api/edit", {
        model: state.model,
        data: state.target.data,
        edits: Array.from(edits.values()),
      });
      ws.edits = edits;
      ws.preview = result;
      state.document = result.document;
      const selected = allNodes().find((x) => pathKey(x.path) === key);
      if (selected) state.selected = selected;
      renderDocument();
      renderEditor();
      $("target-summary").textContent =
        "Original: " + size(state.target.size) + " · displaying edited preview";
      message(
        "Edited file built and parsed again. Its updated byte locations, lengths and checksums are shown. The original is unchanged.",
      );
    },
  ),
);
$("discard-edit").addEventListener("click", () => {
  if (!state.selected) return;
  const draft = draftFor(state.selected);
  draft.mode = "hex";
  draft.value = prettyHex(state.selected.payloadHex);
  renderEditor();
  message(
    "Draft reset to the displayed file. The current built preview is unchanged.",
  );
});
function differenceRanges(before, after) {
  const ranges = [];
  let start = null;
  const end = Math.max(before.length, after.length);
  for (let i = 0; i <= end; i++) {
    const differs = i < end && before[i] !== after[i];
    if (differs && start === null) start = i;
    if (!differs && start !== null) {
      ranges.push([start, i]);
      start = null;
    }
  }
  return ranges;
}
function renderChanges() {
  const ws = workspace();
  const result = state.document ? ws?.preview : null;
  $("changes").hidden = !result;
  if (!result) return;
  const c = result.changes;
  $("change-stats").replaceChildren();
  for (const [name, value] of [
    ["Original", c.originalBytes],
    ["Edited", c.editedBytes],
    ["Different offsets", c.changedBytes],
  ]) {
    const stat = el("div", "stat");
    stat.append(
      el("strong", "", Number(value).toLocaleString()),
      el("span", "", name + " bytes"),
    );
    $("change-stats").append(stat);
  }
  const before = bytes(state.target.data),
    after = bytes(result.data);
  const ranges = differenceRanges(before, after);
  const lines = ranges
    .slice(0, 12)
    .map(
      ([a, b]) =>
        "bytes " + a + "–" + (b - 1) + " · " + (b - a) + " different offsets",
    );
  if (ranges.length > 12)
    lines.push("+" + (ranges.length - 12) + " further ranges");
  if (!ranges.length) lines.push("Byte-for-byte identical to the original.");
  $("change-ranges").replaceChildren(...lines.map((x) => el("div", "", x)));
  $("change-json").textContent = json({
    ...c,
    differenceRanges: ranges,
    interpretation:
      "Ranges compare the same byte positions. Shifted content counts as different; this is not a minimal edit distance.",
  });
}
function download(name, data, type) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const link = el("a");
  link.href = url;
  link.download = name.replace(/[\\/]/g, "_");
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
$("download-file").addEventListener("click", () => {
  const result = workspace()?.preview;
  if (result) {
    const name = state.target.name;
    const dot = name.lastIndexOf(".");
    const edited =
      dot > 0
        ? name.slice(0, dot) + ".edited" + name.slice(dot)
        : name + ".edited";
    download(edited, bytes(result.data), "application/octet-stream");
    message(
      "Downloaded the built preview as " +
        edited +
        ". Your original remains unchanged.",
    );
  }
});
$("download-schema").addEventListener("click", () => {
  if (state.model) {
    download(
      "reclay-schema.json",
      json(state.model) + "\n",
      "application/json",
    );
    message("Downloaded the exact selected grammar and its corpus evidence.");
  }
});
$("download-codec").addEventListener("click", () =>
  operation("Exporting this selected grammar…", async () => {
    const result = await request("/api/export", { model: state.model });
    download(result.filename || "codec.py", result.source, "text/x-python");
    message(
      "Downloaded the self-contained Python codec for this selected grammar.",
    );
  }),
);
request("/api/health")
  .then((result) => {
    $("runtime").textContent = "Local · v" + result.version;
  })
  .catch(() => {
    $("runtime").textContent = "Server unavailable";
    message(
      "Reclay is not reachable. Start the local server and refresh this page.",
      "error",
    );
  });
renderCorpus();
