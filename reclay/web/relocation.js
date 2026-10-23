"use strict";
const $ = (id) => document.getElementById(id);
const MAX = 1048576;
let samples = [],
  working = null,
  model = null,
  result = null,
  documentInfo = null,
  revision = 0;
const encoder = new TextEncoder();
function tell(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}
function bytesTo64(bytes) {
  let text = "";
  for (let p = 0; p < bytes.length; p += 16384)
    text += String.fromCharCode(...bytes.subarray(p, p + 16384));
  return btoa(text);
}
function from64(text) {
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
}
function hex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
function unhex(text) {
  const clean = text.replace(/\s/g, "");
  if (clean.length > 2 * MAX || clean.length % 2 || !/^[0-9a-f]*$/i.test(clean))
    throw Error("Use complete hexadecimal byte pairs, up to 1 MiB.");
  return Uint8Array.from(clean.match(/../g) || [], (s) => parseInt(s, 16));
}
async function fileBytes(file, limit = MAX) {
  if (file.size > limit || !file.size)
    throw Error("Input exceeds its allowed size or is empty.");
  const data = new Uint8Array(await file.arrayBuffer());
  if (data.length > limit) throw Error("Input exceeds its allowed size.");
  return data;
}
async function api(path, body) {
  if (window.ReclayTransport?.enabled)
    return window.ReclayTransport.request(path, body);
  const response = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const answer = await response.json();
  if (!response.ok) throw Error(answer.error || "Operation failed.");
  return answer;
}
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function invalidatePreview() {
  revision++;
  result = null;
  $("result").hidden = true;
  $("download").disabled = true;
}
function invalidateDocument() {
  invalidatePreview();
  documentInfo = null;
  $("payload").disabled = true;
  $("restore-payload").disabled = true;
  $("preview").disabled = true;
  $("terminator").disabled = true;
  $("payload-size").textContent = "";
  $("guard").textContent =
    "Inspect the current file and marked span before editing.";
}
function invalidateModel() {
  invalidateDocument();
  model = null;
  $("models").replaceChildren();
  $("save-model").disabled = true;
  $("export").disabled = true;
  $("graph").replaceChildren(
    node("p", "Learned addresses appear after selecting a model."),
  );
  $("model-status").textContent = "Observations changed. Learn again.";
}
function coordinates() {
  const start = Number($("start").value),
    end = Number($("end").value);
  if (
    !working ||
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    start < 0 ||
    end <= start ||
    end > working.bytes.length
  )
    throw Error("Mark a nonempty span inside the working file.");
  return { start, end };
}
function body() {
  if (!model) throw Error("Choose a model first.");
  return { model, data: bytesTo64(working.bytes), ...coordinates() };
}
function renderSamples() {
  $("files").replaceChildren();
  $("count").textContent = samples.length + " files";
  $("infer").disabled = samples.length < 2;
  samples.forEach((s, index) => {
    const row = node("div", undefined, "observation");
    const remove = node("button", "×", "remove");
    remove.setAttribute("aria-label", "Remove " + s.name);
    remove.onclick = () => {
      samples.splice(index, 1);
      invalidateModel();
      renderSamples();
    };
    row.append(
      remove,
      node("div", s.name + " · " + s.bytes.length + " bytes", "file-title"),
    );
    const controls = node("div", undefined, "spans");
    ["start", "end"].forEach((key) => {
      const label = node("label", key === "start" ? "Start" : "End");
      const input = node("input");
      input.type = "number";
      input.min = 0;
      input.max = s.bytes.length;
      input.step = 1;
      input.value = s[key];
      input.setAttribute("aria-label", s.name + " " + key);
      input.oninput = () => {
        s[key] = Number(input.value);
        invalidateModel();
      };
      label.append(input);
      controls.append(label);
    });
    row.append(controls);
    $("files").append(row);
  });
}
function setWorking(s) {
  invalidateDocument();
  working = s;
  $("working-name").textContent = s.name;
  $("start").value = s.start;
  $("end").value = s.end;
  $("inspect").disabled = !model;
}
function renderGraph(doc) {
  $("graph").replaceChildren();
  const fields = doc.fields || [];
  if (!fields.length)
    $("graph").append(node("p", "No observed dependent fields."));
  fields.forEach((f) => {
    const n = node("div", undefined, "edge");
    const site = f.site === undefined ? f.offset : f.site;
    n.append(
      node("span", "@" + site),
      node("span", "→", "arrow"),
      node(
        "strong",
        f.kind === "spanLength" ? "marked span length" : "address " + f.value,
      ),
      node("div", f.width + " bytes · " + f.endian + " · stored " + f.value),
    );
    $("graph").append(n);
  });
}
async function inspect() {
  invalidateDocument();
  const rev = revision;
  const request = body();
  tell("Checking the file envelope and every learned field…");
  const doc = await api("/api/relocation/inspect", request);
  if (rev !== revision) return;
  documentInfo = doc;
  $("encoding").value = "hex";
  const exampleReplacement =
    working.exampleReplacement &&
    request.start === working.start &&
    request.end === working.end
      ? working.exampleReplacement
      : null;
  $("payload").value = exampleReplacement
    ? hex(exampleReplacement)
    : doc.payloadHex || hex(working.bytes.subarray(request.start, request.end));
  $("terminator").checked = false;
  $("payload").disabled = false;
  $("restore-payload").disabled = false;
  $("preview").disabled = false;
  renderGraph(doc);
  $("guard").textContent =
    "Guard matched. " +
    (doc.fields || []).length +
    " observed relationships; all other envelope bytes preserved. This does not prove unobserved dependencies.";
  updatePayloadSize();
  tell(
    exampleReplacement
      ? "The working file fits. A shorter independently produced title is ready; preview its pointer rewrites."
      : "The working file fits this interpretation. Draft an edit to preview its byte rewrites.",
  );
}
function selectModel(m) {
  invalidateDocument();
  model = m;
  $("save-model").disabled = false;
  $("export").disabled = false;
  $("inspect").disabled = !working;
  for (const button of $("models").children)
    button.setAttribute("aria-pressed", String(button.dataset.model === m.id));
  $("evidence").textContent = JSON.stringify(m, null, 2);
  if (working) inspect().catch((e) => tell(e.message, true));
}
function renderModels(answer) {
  $("models").replaceChildren();
  const models = answer.models || [];
  $("model-status").textContent =
    models.length +
    " surviving model" +
    (models.length === 1 ? "" : "s") +
    ". " +
    (answer.truncated
      ? "Search limit reached; alternatives may be missing."
      : "Selection remains an assumption within the declared search family.");
  $("evidence").textContent = JSON.stringify(answer, null, 2);
  models.forEach((m, i) => {
    const b = node("button", undefined, "candidate");
    b.dataset.model = m.id;
    b.setAttribute("aria-pressed", "false");
    b.append(
      node("strong", "Interpretation " + (i + 1)),
      node(
        "span",
        m.id.slice(0, 12) +
          " · " +
          (m.fields || []).length +
          " relationships · alignment " +
          m.alignment,
      ),
    );
    b.onclick = () => selectModel(m);
    $("models").append(b);
  });
  if (models.length === 1 && !answer.truncated) selectModel(models[0]);
}
function payload() {
  if (!documentInfo) throw Error("Inspect the current file before editing.");
  let out =
    $("encoding").value === "hex"
      ? unhex($("payload").value)
      : encoder.encode($("payload").value);
  if ($("encoding").value === "text" && $("terminator").checked) {
    const padded = new Uint8Array(out.length + 1);
    padded.set(out);
    out = padded;
  }
  if (out.length > MAX) throw Error("Replacement exceeds 1 MiB.");
  return out;
}
function updatePayloadSize() {
  try {
    const size = payload().length;
    const originalSize = coordinates().end - coordinates().start;
    const delta = size - originalSize;
    $("payload-size").textContent = `${originalSize} → ${size} span bytes · ${delta > 0 ? "+" : ""}${delta} before alignment`;
  } catch (e) {
    $("payload-size").textContent = e.message;
  }
}
function download(name, bytes, type = "application/octet-stream") {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const link = node("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function safe(task) {
  return () =>
    Promise.resolve()
      .then(task)
      .catch((e) => tell(e.message, true));
}
$("observations").onchange = safe(async () => {
  const files = Array.from($("observations").files);
  if (samples.length + files.length > 8) throw Error("At most 8 observations.");
  const incoming = [];
  for (const f of files)
    incoming.push({
      name: f.name,
      bytes: await fileBytes(f),
      start: 0,
      end: f.size,
    });
  if (
    [...samples, ...incoming].reduce((n, s) => n + s.bytes.length, 0) >
    4 * MAX
  )
    throw Error("Corpus exceeds 4 MiB.");
  invalidateModel();
  samples.push(...incoming);
  renderSamples();
  $("observations").value = "";
  tell("Mark the same changing span in every observation.");
});
$("working").onchange = safe(async () => {
  const f = $("working").files[0];
  if (!f) return;
  setWorking({
    name: f.name,
    bytes: await fileBytes(f),
    start: 0,
    end: f.size,
  });
  $("working").value = "";
  tell("Mark the working span and inspect it.");
});
$("example").onclick = safe(async () => {
  invalidateModel();
  const rev = revision;
  const answer = await api("/api/relocation-examples");
  if (rev !== revision) return;
  samples = answer.samples.map((s) => ({ ...s, bytes: from64(s.data) }));
  renderSamples();
  setWorking({
    ...answer.working,
    bytes: from64(answer.working.data),
    exampleReplacement: from64(answer.replacement.data),
  });
  $("description").textContent = answer.description;
  tell(
    "Loaded independent TIFF observations and a withheld working file. Learn their relationships.",
  );
});
$("infer").onclick = safe(async () => {
  invalidateModel();
  const rev = revision;
  $("infer").disabled = true;
  try {
    tell("Searching integer interpretations, alignment and envelope coverage…");
    const answer = await api("/api/relocation/infer", {
      samples: samples.map((s) => ({
        data: bytesTo64(s.bytes),
        start: s.start,
        end: s.end,
      })),
    });
    if (rev !== revision) return;
    renderModels(answer);
    if (!(answer.models || []).length)
      tell("No supported model explains every changing envelope byte.", true);
    else
      tell(
        "Inference finished. Inspect the selected interpretation before rebuilding.",
      );
  } finally {
    $("infer").disabled = samples.length < 2;
  }
});
$("inspect").onclick = safe(inspect);
$("restore-payload").onclick = () => {
  if (!documentInfo) return;
  const { start, end } = coordinates();
  invalidatePreview();
  $("encoding").value = "hex";
  $("payload").value = hex(working.bytes.subarray(start, end));
  $("terminator").checked = false;
  $("terminator").disabled = true;
  updatePayloadSize();
  tell("Current span restored from the working file. Preview again to check the result; the original file is untouched.");
};
["start", "end"].forEach((id) => ($(id).oninput = invalidateDocument));
$("payload").oninput = () => {
  invalidatePreview();
  updatePayloadSize();
};
$("terminator").onchange = () => {
  invalidatePreview();
  updatePayloadSize();
};
$("encoding").onchange = () => {
  try {
    const bytes =
      $("encoding").value === "text"
        ? unhex($("payload").value)
        : (() => {
            let out = encoder.encode($("payload").value);
            if ($("terminator").checked) {
              const b = new Uint8Array(out.length + 1);
              b.set(out);
              out = b;
            }
            return out;
          })();
    if ($("encoding").value === "text") {
      const nul = bytes.length > 0 && bytes[bytes.length - 1] === 0;
      $("payload").value = new TextDecoder("utf-8", { fatal: true }).decode(
        nul ? bytes.subarray(0, -1) : bytes,
      );
      $("terminator").checked = nul;
      $("terminator").disabled = false;
    } else {
      $("payload").value = hex(bytes);
      $("terminator").disabled = true;
      $("terminator").checked = false;
    }
    invalidatePreview();
    updatePayloadSize();
  } catch (e) {
    $("encoding").value = "hex";
    tell("This span is not valid UTF-8. Keep hexadecimal editing.", true);
  }
};
$("preview").onclick = safe(async () => {
  const replacement = bytesTo64(payload());
  invalidatePreview();
  const rev = revision;
  const request = { ...body(), replacement };
  tell("Rebuilding a separate file and checking the result…");
  const answer = await api("/api/relocation/resize", request);
  if (rev !== revision) return;
  result = { bytes: from64(answer.data), changes: answer.changes };
  $("result").hidden = false;
  $("download").disabled = false;
  const changes = answer.changes;
  $("delta").textContent =
    working.bytes.length + " → " + result.bytes.length + " bytes";
  $("rewrites").replaceChildren();
  for (const c of changes.changes || [])
    $("rewrites").append(
      node(
        "div",
        c.kind +
          " @" +
          c.site +
          " → @" +
          c.newSite +
          " : " +
          c.before +
          " → " +
          c.after,
        "rewrite",
      ),
    );
  if (!(changes.changes || []).length)
    $("rewrites").append(node("p", "No integer rewrites required."));
  tell(
    "Preview ready. Original untouched; verify the download in an independent reader.",
  );
});
$("download").onclick = () => {
  if (result)
    download(working.name.replace(/(\.[^.]*)?$/, "-edited$1"), result.bytes);
};
$("save-model").onclick = () => {
  if (model)
    download(
      "relocation-model.json",
      JSON.stringify(model, null, 2) + "\n",
      "application/json",
    );
};
$("export").onclick = safe(async () => {
  if (!model) return;
  const rev = revision;
  const answer = await api("/api/relocation/export", { model });
  if (rev !== revision) return;
  download(answer.filename, answer.source, "text/x-python");
  tell(
    "Standalone editor downloaded. Run its --help for bounded inspect and rebuild commands.",
  );
});
$("model-upload").onchange = safe(async () => {
  const f = $("model-upload").files[0];
  if (!f) return;
  const obj = JSON.parse(new TextDecoder().decode(await fileBytes(f, 3 * MAX)));
  invalidateModel();
  renderModels({ models: [obj], status: "imported" });
  $("model-upload").value = "";
  tell(
    "Imported model is checked by the server before inspecting, rebuilding or exporting.",
  );
});

document.addEventListener("reclay-runtime-reset", (event) => {
  invalidatePreview();
  tell(event.detail, true);
});
