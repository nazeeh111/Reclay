// Component/controller tests only. Python tests exercise the real dispatcher and HTTP API.
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const fixture=JSON.parse(fs.readFileSync(process.argv[2] || require('node:path').join(__dirname,'fixtures/holdout-ui.json'), 'utf8'));
function harness() {
  const elements=new Map(), downloads=[];
  class Element {
    constructor(){this.hidden=false;this.disabled=false;this.textContent='';this.value='';this.children=[];this.handlers={};this.dataset={};this.classList={toggle(){},add(){},remove(){}};}
    addEventListener(type,fn){this.handlers[type]=fn;}
    setAttribute(){}
    append(...xs){this.children.push(...xs)}
    replaceChildren(...xs){this.children=xs}
    click(){downloads.push({name:this.download, url:this.href})}
    remove(){}
  }
  const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement(){return new Element()},querySelectorAll(){return []},addEventListener(){},body:new Element()};
  const calls=[];let pending=null;let fail=false;
  const transport={enabled:true,async request(path,body){calls.push({path,body});
    if(path==='/api/ensemble-identity')return {sha256:fixture.digest};
    if(path==='/api/validate-hypotheses'){
      if(fail)throw Error('holdout-work-bound');
      if(pending)return await pending;
      assert.equal(body.expectedSha256,fixture.digest);return fixture.result;
    }
    throw Error('unexpected '+path);
  }};
  let savedBlob;
  const urls={createObjectURL(blob){savedBlob=blob;return 'blob:test'},revokeObjectURL(){}};
  const c=vm.createContext({document,window:{ReclayTransport:transport},Blob,URL:urls,setTimeout(fn){fn()},TextEncoder,TextDecoder,Uint8Array,Map,Promise,Error,JSON,String,Number,Array,console,atob:s=>Buffer.from(s,'base64').toString('binary'),btoa:s=>Buffer.from(s,'binary').toString('base64')});
  vm.runInContext(fs.readFileSync(process.env.RECLAY_APP_JS || require.resolve('../reclay/web/app.js'),'utf8'),c);
  const run=s=>vm.runInContext(s,c);run('state.result = '+JSON.stringify(fixture.source)+'; renderCandidates();');
  return {c,run,el:id=>document.getElementById(id),calls,downloads,getBlob:()=>savedBlob,setPending(p){pending=p},setFail(x){fail=x}};
}
async function select(h, file){h.el('holdout-files').handlers.change({target:{files:[file],value:'x'}});await new Promise(r=>setImmediate(r));}
const goodFile={name:'hold.bin',size:2,async arrayBuffer(){return Uint8Array.from([1,2]).buffer}};
test('actual controller applies all outcomes and requires renewed explicit model choice',async()=>{
  const h=harness();await select(h,goodFile);
  assert.match(h.el('holdout-status').textContent,/not yet checked/);
  await h.el('validate-holdouts').handlers.click();
  assert.equal(h.run('state.result.models.length'),1);
  assert.equal(h.run('state.model'),null);
  assert.equal(h.el('holdout-details').hidden,false);
  assert.match(h.el('holdout-outcomes').textContent,/refused/);
  assert.match(h.el('candidate-status').textContent,/does not establish uniqueness/);
  assert.equal(h.calls[1].body.samples[0].data,'AQI=');
  h.el('download-validation').handlers.click();
  assert.equal(h.downloads[0].name,'reclay-validation.json');
  const saved=JSON.parse(await h.getBlob().text());
  assert.equal(saved.validation.sourceEnsembleSha256,fixture.digest);
  assert.equal(saved.validation.outcomes.length,3);
});
test('changed source while request is pending never applies the stale result',async()=>{
  const h=harness();await select(h,goodFile);let resolve;
  h.setPending(new Promise(r=>resolve=r));const waiting=h.el('validate-holdouts').handlers.click();
  await new Promise(r=>setImmediate(r));h.run('state.validationGeneration++; state.result = {models:[]};');
  resolve(fixture.result);await waiting;
  assert.equal(h.run('state.result.models.length'),0);
  assert.match(h.el('status').textContent,/changed during validation/);
});
test('file read and work-budget errors preserve the previous ensemble',async()=>{
  const h=harness();await select(h,{name:'bad',size:2,async arrayBuffer(){throw Error('read failed')}});
  assert.equal(h.run('state.holdouts.length'),0);assert.match(h.el('status').textContent,/read failed/);
  await select(h,goodFile);h.setFail(true);await h.el('validate-holdouts').handlers.click();
  assert.equal(h.run('state.result.models.length'),3);assert.equal(h.run('state.holdouts.length'),1);
  assert.match(h.el('holdout-status').textContent,/incomplete or refused/);
});

test('model selection changed during a pending validation is rejected',async()=>{
  const h=harness();await select(h,goodFile);let resolve;
  h.setPending(new Promise(r=>resolve=r));const waiting=h.el('validate-holdouts').handlers.click();
  await new Promise(r=>setImmediate(r));h.run('state.model = state.result.models[0]');
  resolve(fixture.result);await waiting;
  assert.equal(h.run('state.result.models.length'),3);
  assert.match(h.el('status').textContent,/changed during validation/);
});
