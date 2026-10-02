import { App } from '@modelcontextprotocol/ext-apps';
import { OpenAIExtensions } from '@openai/mcp-extensions/app';

const app = new App({ name: 'Practice space planner', version: '0.2.0' }, { availableDisplayModes: ['inline', 'fullscreen'] });
const extensions = new OpenAIExtensions(app);
const { catalog, groups, markets } = JSON.parse(document.querySelector('#catalog').textContent);
const $ = id => document.getElementById(id);
let current = null, previous = null, revision = 0, contextId;
function options(select, rows, selected) {
  select.replaceChildren(...rows.map(([value, label]) => { const item = document.createElement('option'); item.value = value; item.textContent = label; return item; }));
  if (selected) select.value = selected;
}
options($('vertical'), Object.entries(groups).map(([key, group]) => [key, group.title]));
options($('market'), Object.entries(markets));
function subtypeOptions(selected) {
  const rows = catalog.filter(row => row.group === $('vertical').value);
  options($('subtype'), rows.map(row => [row.value, row.label]), selected);
  setCapacity();
}
function setCapacity() {
  const row = catalog.find(row => row.value === $('subtype').value);
  $('rooms-label').textContent = row.group === 'dental' ? 'Operatories' : 'Exam / private treatment rooms';
  $('providers').max = row.singleProvider ? 1 : 50;
  if (row.singleProvider) $('providers').value = 1;
  $('capacity-note').textContent = row.singleProvider ? 'This specialty has a single-provider reference band. It does not scale with room count.' : 'Counts describe the planned program; equipment and room fit still need review.';
}
subtypeOptions();
$('vertical').addEventListener('change', () => subtypeOptions());
$('subtype').addEventListener('change', setCapacity);
function controls(inputs) {
  const row = catalog.find(row => row.value === inputs.practice_type || (inputs.practice_type === 'dental' && row.value === 'dental_gp'));
  if (!row) return;
  $('vertical').value = row.group; subtypeOptions(row.value);
  $('providers').value = inputs.providers;
  $('rooms').value = row.group === 'dental' ? inputs.operatories : inputs.exam_rooms;
  $('market').value = inputs.market || 'other_market';
}
function readControls() {
  const dental = $('vertical').value === 'dental';
  return { practice_type: $('subtype').value, providers: Number($('providers').value), operatories: dental ? Number($('rooms').value) : 0,
    exam_rooms: dental ? 0 : Number($('rooms').value), market: $('market').value };
}
function validInputs(inputs) {
  return inputs && catalog.some(row => row.value === inputs.practice_type || inputs.practice_type === 'dental') &&
    ['providers', 'operatories', 'exam_rooms'].every(key => Number.isInteger(inputs[key]) && inputs[key] >= (key === 'providers' ? 1 : 0) && inputs[key] <= (key === 'providers' ? 50 : 100));
}
function element(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node; }
function list(title, rows) { const section = document.createElement('section'); section.append(element('h3', title)); const ul = document.createElement('ul'); ul.append(...rows.map(row => element('li', row))); section.append(ul); return section; }
const range = value => `${value.low.toLocaleString()}–${value.high.toLocaleString()}`;
function render(plan) {
  const root = $('plan'); root.replaceChildren();
  if (!plan) { $('status').textContent = 'Choose a program to create a space plan.'; return; }
  const r = plan.results;
  root.append(element('h2', catalog.find(row => row.value === r.practice_type || (r.practice_type === 'dental' && row.value === 'dental_gp'))?.label || 'Practice program'));
  root.append(element('div', `${range(r.usable_square_feet)} usable SF`, 'area'));
  root.append(element('p', `${r.usable_area_type} · ${r.source_class}`, 'muted'), element('p', r.sizing_rule));
  root.append(element('p', r.parking.spaces_needed ? `${range(r.parking.spaces_needed)} parking spaces (preliminary)` : 'Parking need unknown: no training ratio supplied.'));
  root.append(element('p', r.parking.area_basis + '. ' + r.parking.status, 'muted'));
  root.append(list('Room functions', r.rooms.map(row => `${row.room} · ${row.source_class}`)));
  const checks = document.createElement('section'); checks.append(element('h3', 'Building due diligence'));
  for (const [topic, questions] of Object.entries(r.due_diligence)) {
    const details = document.createElement('details'); details.append(element('summary', topic.toUpperCase()));
    const ul = document.createElement('ul'); ul.append(...questions.map(question => element('li', question))); details.append(ul); checks.append(details);
  }
  root.append(checks);
  for (const [title, key] of [['Warnings', 'warnings'], ['Missing inputs', 'missing_inputs'], ['Assumptions', 'assumptions'], ['Limitations', 'limitations']]) {
    if (plan[key]?.length) root.append(list(title, plan[key]));
  }
  root.append(element('p', plan.market_note || 'Local requirements remain unverified.', 'muted'), element('p', plan.notice, 'muted'));
}
function adopt(plan) {
  if (!validInputs(plan?.inputs_used) || !plan.results?.usable_square_feet || !Array.isArray(plan.results.rooms) || !plan.results.due_diligence) return false;
  previous = current; current = plan; $('undo').disabled = !previous; controls({ ...plan.inputs_used, market: plan.market }); render(plan); return true;
}
async function publish(plan) {
  if (!extensions.modelContext) return;
  const receipt = await extensions.modelContext.update({
    content: [{ type: 'text', text: `${range(plan.results.usable_square_feet)} usable SF; preliminary ${plan.results.practice_type} space plan.`, _meta: { 'openai/title': 'Current space plan' } }],
    structuredContent: { practice_space_plan: { inputs: { ...plan.inputs_used, market: plan.market || 'other_market' } } },
  });
  contextId = receipt?.updateId;
}
async function update(inputs, share = true) {
  const token = ++revision;
  $('status').classList.add('busy'); $('status').textContent = 'Updating space plan…';
  try {
    const response = await app.callServerTool({ name: 'update_practice_space_plan', arguments: inputs });
    if (token !== revision) return;
    if (response.isError || !adopt(response.structuredContent)) throw new Error('invalid plan');
    $('status').textContent = 'Space plan updated.';
    if (share) await publish(current).catch(() => { $('sync-note').textContent = 'Plan updated; conversation context could not be shared.'; });
  } catch {
    if (token === revision) $('status').textContent = 'Plan could not update. Check the selected program and counts, then retry.';
  } finally { if (token === revision) $('status').classList.remove('busy'); }
}
$('program').addEventListener('submit', event => { event.preventDefault(); if ($('program').reportValidity()) void update(readControls()); });
$('undo').addEventListener('click', () => { if (previous) void update({ ...previous.inputs_used, market: previous.market || 'other_market' }); });
function syncContext() {
  const context = extensions.modelContext?.getCurrent();
  if (context === undefined) return;
  if (context !== null && context.updateId === contextId) return;
  contextId = context?.updateId;
  if (context === null) { ++revision; current = previous = null; $('undo').disabled = true; render(null); return; }
  const inputs = context.structuredContent?.practice_space_plan?.inputs;
  if (validInputs(inputs)) void update({ practice_type: inputs.practice_type, providers: inputs.providers, operatories: inputs.operatories,
    exam_rooms: inputs.exam_rooms, market: Object.hasOwn(markets, inputs.market) ? inputs.market : 'other_market' }, false);
}
app.ontoolresult = response => {
  if (response.isError) { $('status').textContent = 'The last requested plan failed. Check program counts.'; return; }
  if (adopt(response.structuredContent)) { ++revision; $('status').classList.remove('busy'); $('status').textContent = 'Space plan updated from conversation.'; void publish(current).catch(() => { $('sync-note').textContent = 'Plan is visible; context sharing failed.'; }); }
  else if (!current) render(null);
};
app.addEventListener('hostcontextchanged', syncContext);
app.connect().then(() => {
  syncContext();
  $('sync-note').textContent = extensions.modelContext ? 'Plan context is shared with this conversation. Removing it clears the panel.' : 'This host does not support Model-App Context. Planner controls remain available.';
}).catch(() => { $('status').textContent = 'Open this planner in a connected MCP App host to calculate a plan.'; });
