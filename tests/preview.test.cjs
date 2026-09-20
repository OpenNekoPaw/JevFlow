const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(request) {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, {hidden: false, textContent: '', events: {},
      classList: {add() {}, remove() {}}, addEventListener(type, fn) {this.events[type] = fn;}});
    return elements.get(id);
  };
  const context = {document: {getElementById: el, addEventListener() {}},
    JevFlowViewer: {Viewer: class {setData(data) {this.data = data;} draw() {}}}, setInterval() {return 1;}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('jevflow/web/preview.js', 'utf8'), context);
  return {panel: new context.JevFlowPreview.Panel({request}), el};
}
const data = name => ({name, revision: '1', flowHash: name, graph: {nodes: {}}});
const tick = () => new Promise(setImmediate);
const file = name => ({name, size: 1, text: async () => name});
test('late upload responses cannot overwrite the latest selected file', async () => {
  const replies = [];
  const {panel, el} = setup(() => new Promise(resolve => replies.push(resolve)));
  const a = panel.open(file('old.yaml')); await tick();
  const b = panel.open(file('new.yaml')); await tick();
  replies[1](data('new')); await b;
  replies[0](data('old')); await a;
  assert.equal(panel.data.name, 'new');
  assert.equal(el('config-source').textContent, '本地文件 · new.yaml');
});
test('watch updates do not replace an uploaded file and failed uploads keep the valid graph', async () => {
  let fail = false;
  const {panel, el} = setup(async path => {
    if (path === '/api/config') return {configured: true, source: 'server.yaml', data: data('server')};
    if (fail) throw Error('invalid YAML');
    return data('file');
  });
  await panel.open(file('local.yaml'));
  await panel.poll();
  assert.equal(panel.data.name, 'file');
  fail = true; await panel.open(file('bad.yaml'));
  assert.equal(panel.data.name, 'file');
  assert.match(el('config-error').textContent, /invalid YAML/);
  el('watch-config').onclick(); await tick();
  assert.equal(panel.data.name, 'server');
  assert.equal(el('config-error').hidden, true);
});
test('drop selects a single file and oversized files never reach the server', async () => {
  let calls = 0;
  const {panel, el} = setup(async () => {calls++; return data('dropped');});
  el('yaml-drop').events.drop({preventDefault() {}, dataTransfer: {files: [file('drop.yaml')]}});
  await tick(); await tick(); await tick();
  assert.equal(panel.data.name, 'dropped');
  await panel.open({name: 'large.yaml', size: 1024*1024+1});
  assert.equal(calls, 1);
  assert.match(el('config-error').textContent, /1 MiB/);
});
