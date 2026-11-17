// Controller checks only; engine/HTTP behavior is covered by the Python suite.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function harness() {
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {value: '', disabled: false, hidden: false,
        checked: false, textContent: '', classList: {toggle() {}}, replaceChildren() {}});
      return elements.get(id);
    },
    addEventListener() {},
  };
  const context = vm.createContext({document, window: {}, TextEncoder, TextDecoder, Uint8Array,
    console, btoa: s => Buffer.from(s, 'binary').toString('base64')});
  vm.runInContext(fs.readFileSync(require.resolve('../reclay/web/relocation.js'), 'utf8'), context);
  vm.runInContext(`setWorking({name:'working.bin',bytes:new Uint8Array([9,8,65,66,67,0,7]),start:2,end:6});
    documentInfo={payloadHex:'41424300'}; $('payload').disabled=false; $('restore-payload').disabled=false;
    $('encoding').value='text'; $('payload').value='unaccepted long draft'; $('terminator').checked=true;
    result={bytes:new Uint8Array([1])}; $('result').hidden=false; $('download').disabled=false;`,context);
  return {context, el: id => document.getElementById(id), run: s=>vm.runInContext(s,context)};
}
test('restore gets the exact marked bytes, clears stale output and reports the byte delta', () => {
  const h = harness();
  h.el('restore-payload').onclick();
  assert.equal(h.el('payload').value, '41424300');
  assert.equal(h.el('encoding').value, 'hex');
  assert.equal(h.el('terminator').checked, false);
  assert.equal(h.el('terminator').disabled, true);
  assert.equal(h.el('result').hidden, true);
  assert.equal(h.el('download').disabled, true);
  assert.equal(h.el('payload-size').textContent, '4 → 4 span bytes · 0 before alignment');
  assert.equal(h.run('result'), null);
  h.el('payload').value='4142'; h.el('payload').oninput();
  assert.equal(h.el('payload-size').textContent, '4 → 2 span bytes · -2 before alignment');
  h.el('payload').value='414243444500'; h.el('payload').oninput();
  assert.equal(h.el('payload-size').textContent, '4 → 6 span bytes · +2 before alignment');
});
test('changing the marked span disables recovery until a fresh inspection', () => {
  const h = harness();
  h.el('start').value='1'; h.el('start').oninput();
  assert.equal(h.el('restore-payload').disabled, true);
  h.el('restore-payload').onclick();
  assert.equal(h.el('payload').value, 'unaccepted long draft');
  assert.equal(h.run('documentInfo'), null);
});

test('refused imported model preserves the active model, draft and preview', async () => {
  const h = harness();
  const bytes = new TextEncoder().encode('{"id":"bad","fields":[]}');
  h.el('model-upload').files = [{size: bytes.length, async arrayBuffer() { return bytes.buffer; }}];
  h.run("model={id:'accepted-model'}; api=async()=>{throw Error('Unsupported model');}");
  await h.el('model-upload').onchange();
  assert.equal(h.run('model.id'), 'accepted-model');
  assert.equal(h.el('payload').value, 'unaccepted long draft');
  assert.equal(h.el('payload').disabled, false);
  assert.equal(h.el('download').disabled, false);
  assert.equal(h.run('result.bytes[0]'), 1);
  assert.equal(h.el('status').textContent, 'Unsupported model');
});

test('a completed import cannot replace a workspace changed while validation waits', async () => {
  const h = harness();
  const bytes = new TextEncoder().encode('{"id":"incoming-model","fields":[]}');
  h.el('model-upload').files = [{size: bytes.length, async arrayBuffer() { return bytes.buffer; }}];
  h.run("model={id:'accepted-model'}; api=()=>new Promise(resolve=>{globalThis.finishValidation=resolve});");
  const pending = h.el('model-upload').onchange();
  await new Promise(resolve => setImmediate(resolve));
  h.run('invalidatePreview()');
  h.context.finishValidation({source:'validated'});
  await pending;
  assert.equal(h.run('model.id'), 'accepted-model');
  assert.equal(h.el('payload').value, 'unaccepted long draft');
  assert.notEqual(h.run('documentInfo'), null);
});
