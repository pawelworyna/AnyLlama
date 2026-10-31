'use strict';
let state, csrf = '', page = 'overview', wizardProvider = null, firstLoad = true;
const content = document.querySelector('#content');
const dialog = document.querySelector('#modal');
const E = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const urlId = value => encodeURIComponent(value);
const providerMark = kind => state.presets[kind]?.mark || '<>';
const requiresKey = kind => state.presets[kind]?.requires_key !== false;
const providerNote = kind => state.presets[kind]?.note || 'Select a chat model. If the catalog omits capabilities or prices, set them in Models → Configure.';
const names = {overview:'Overview',providers:'Providers',models:'Models',activity:'Activity',settings:'Settings'};
const button = (label, action, primary = false, extra = '') => `<button class="button ${primary?'primary':''}" data-action="${action}" ${extra}>${label}</button>`;
const cap = (value, label) => `<span class="capability ${value===true?'yes':''}" title="${E(label)}: ${value===null || value===undefined?'unknown':value?'supported':'not supported'}">${E(label)}${value===null || value===undefined?' ?':value?'':' —'}</span>`;
const num = v => v === null || v === undefined ? '—' : Number(v).toLocaleString('en-US');
const money = value => value === null || value === undefined ? '—' : value === 0 ? '$0.00' : value < 0.000001 ? '<$0.000001' : '$'+Number(value).toFixed(6);
const priceFields = {input_price_per_million:'Input',output_price_per_million:'Output',cached_input_price_per_million:'Cached input',cache_write_price_per_million:'Cache write'};
const relative = value => {
  if (!value) return 'Not checked yet';
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  return seconds < 60 ? 'Just now' : seconds < 3600 ? `${Math.floor(seconds/60)}m ago` : `${Math.floor(seconds/3600)}h ago`;
};
const heading = (title, description, action='') => `<div class="page-heading"><div><h1>${title}</h1><p>${description}</p></div>${action}</div>`;
const empty = (icon, title, description, action='') => `<div class="empty-state"><div class="empty-icon" aria-hidden="true">${icon}</div><h3>${title}</h3><p>${description}</p>${action}</div>`;
const endpoint = value => `<div class="endpoint"><code>${E(value)}</code><button class="copy" data-action="copy" data-value="${E(value)}" aria-label="Copy endpoint" title="Copy endpoint">⧉</button></div>`;

async function api(path, method='GET', data) {
  const headers = {'Accept':'application/json'};
  if (method !== 'GET') { headers['X-CSRF-Token'] = csrf; headers['Content-Type'] = 'application/json'; }
  const response = await fetch(`/admin/api/${path}`, {method, headers, body: data === undefined ? undefined : JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || result.error || 'Something went wrong. Try again.');
  return result;
}
function toast(message, error=false) {
  const node = document.createElement('div'); node.className = `toast ${error?'error':''}`; node.textContent = message;
  const region = document.querySelector('#toast-region'); region.append(node); while (region.children.length > 3) region.firstElementChild.remove(); setTimeout(() => node.remove(), error?10000:4000);
}
function applyTheme(value) {
  document.documentElement.dataset.theme = value === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light') : value;
}
async function load(renderPage=true) {
  state = await api('state'); csrf = state.csrf; applyTheme(state.settings.theme);
  document.querySelector('#nav-provider-count').textContent = state.providers.length;
  document.querySelector('#live-indicator').innerHTML = `<span class="small-dot"></span> Proxy ${state.paused?'paused':'running'}`;
  refreshUpdateStatus();
  if (renderPage) render();
}
function render() {
  document.querySelector('#page-label').textContent = names[page];
  document.querySelectorAll('[data-nav]').forEach(node => {node.classList.toggle('active', node.dataset.nav===page); if(node.dataset.nav===page) node.setAttribute('aria-current','page'); else node.removeAttribute('aria-current');});
  ({overview:overview,providers:providers,models:models,activity:activity,settings:settings})[page]();
}
function activityTable(limit=100) {
  const rows = state.activity.slice(0,limit);
  if (!rows.length) return empty('⌁','A little quiet here','Requests from Visual Studio will appear here, with timing and token usage.');
  return `<div class="table-wrap"><table class="activity-table"><thead><tr><th>Model / provider</th><th>Status</th><th title="Time to first content, reasoning or tool fragment; streamed requests only">First token</th><th>Duration</th><th>Output tok/s</th><th>Tokens in / out</th><th>Cost (USD)</th><th>When</th></tr></thead><tbody>${rows.map(row=>{
    const speed=row.tokens_per_second;
    const detail=[row.cached_input_tokens!=null?`${num(row.cached_input_tokens)} cached input`:null,row.reasoning_tokens!=null?`${num(row.reasoning_tokens)} reasoning (included in output)`:null].filter(Boolean).join(' · ');
    return `<tr><td class="model-name"><strong>${E(row.model.split('/').slice(1).join('/'))}</strong><small>${E(row.provider)}</small></td>
      <td><span class="pill ${row.status===200?'positive':'warning'}">${row.status===200?'Completed':row.status===499?'Cancelled':E(row.status)}</span>${row.error?`<small>${E(row.error)}</small>`:''}</td>
      <td>${row.first_token_ms==null?'—':`${Math.round(row.first_token_ms)} ms`}</td><td>${(row.duration_ms/1000).toFixed(2)}s</td>
      <td class="mono" title="${row.throughput_source==='provider'?'Provider token count / generation duration':'Approximation from output tokens and first-to-last output fragment; includes reasoning and tool output. Chunk buffering affects accuracy.'}">${speed==null?'—':`${row.throughput_source==='stream'?'≈ ':''}${Number(speed).toFixed(1)}`}</td>
      <td class="mono">${num(row.input_tokens)} / ${num(row.output_tokens)}${detail?`<small>${E(detail)}</small>`:''}</td>
      <td class="mono">${money(row.cost_usd)}${row.cost_source?`<small>${row.cost_source==='reported'?'Reported':'Estimate'}</small>`:''}</td><td class="muted">${relative(row.created_at)}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}
function overview() {
  const enabled = state.models.filter(m=>m.enabled).length;
  const completed = state.activity.filter(a=>a.status===200);
  const latency = completed.filter(a=>a.first_token_ms!==null);
  content.innerHTML = heading('Your workspace, connected.','Bring your favorite models into Visual Studio.',button('+ Add provider','add-provider',true)) +
  `<div class="hero"><div><div class="eyebrow">ONE ENDPOINT. EVERY POSSIBILITY.</div><h2>Your models.<br>Your development flow.</h2><p>A small bridge between Visual Studio and the models you choose. Your keys stay on this machine.</p><div class="actions">${button('Connect Visual Studio ↗','guide')}${button(state.paused?'▶ Resume proxy':'Ⅱ Pause proxy','pause')}</div></div><div class="hero-graphic" aria-label="OpenRouter, Ollama and TensorX connect through AnyLlama to Visual Studio"><div class="flow-stack"><div class="flow-provider">↗ &nbsp; OpenRouter</div><div class="flow-provider">o &nbsp; Ollama Cloud</div><div class="flow-provider">Tx &nbsp; TensorX</div></div><span class="flow-arrow">→</span><div class="flow-center"><img src="/admin/static/icon.svg" width="45" height="45" alt="AnyLlama"></div><span class="flow-arrow">→</span><div class="vs-mark"><strong>⋈</strong>Visual Studio</div></div></div>` +
  `<div class="stats"><div class="stat"><div class="stat-label">Connected providers <span>⌘</span></div><div class="stat-number">${state.providers.length}</div><div class="stat-note">${state.providers.filter(p=>p.refreshed_at&&!p.error).length} connections checked</div></div><div class="stat"><div class="stat-label">Enabled models <span>▦</span></div><div class="stat-number">${enabled}</div><div class="stat-note">${state.models.length} in your catalog</div></div><div class="stat"><div class="stat-label">Recent requests <span>⌁</span></div><div class="stat-number">${state.activity.length}</div><div class="stat-note">${completed.length} completed · latest 100</div></div><div class="stat"><div class="stat-label">Avg. first token <span>◷</span></div><div class="stat-number">${latency.length?(latency.reduce((n,a)=>n+a.first_token_ms,0)/latency.length/1000).toFixed(2)+'<small>s</small>':'—'}</div><div class="stat-note">Across completed recent requests</div></div></div>` +
  `<div class="two-col"><section class="panel"><div class="panel-header"><h2>Your providers</h2><a class="text-link" href="#providers">Manage providers →</a></div>${state.providers.length?`<div class="panel-body">${state.providers.map(p=>`<div class="provider-top"><div class="provider-mark">${E(providerMark(p.kind))}</div><div><h3>${E(p.name)}</h3><small>${state.models.filter(m=>m.provider_id===p.id&&m.enabled).length} models enabled</small></div><span class="pill ${p.error?'warning':p.refreshed_at?'positive':'neutral'}">${p.error?'Needs attention':p.refreshed_at?'Connected':'Unchecked'}</span></div>`).join('')}</div>`:empty('⌘','Choose your first provider','Connect an account, select a model, and make yourself at home.',button('+ Connect a provider','add-provider'))}</section><section class="panel"><div class="panel-header"><h2>Your local endpoint</h2><span class="pill positive">LOOPBACK</span></div><div class="panel-body"><div class="field-label">Ollama-compatible</div>${endpoint(state.endpoint)}<div class="field-label spaced" >OpenAI-compatible</div>${endpoint(state.endpoint+'/v1')}<div class="note-box">In Visual Studio, add an <strong>Ollama</strong> provider using the OpenAI-compatible address above. No local Ollama installation needed.</div></div></section></div>` +
  `<section class="panel"><div class="panel-header"><h2>Recent activity</h2><a href="#activity" class="text-link">View all activity →</a></div>${activityTable(5)}</section>`;
}
function providers() {
  content.innerHTML = heading('Providers','Your accounts, connected on your terms.',button('+ Add provider','add-provider',true)) +
  (state.providers.length?`<div class="provider-grid">${state.providers.map(p=>`<section class="provider-card"><div class="provider-top"><div class="provider-mark">${E(providerMark(p.kind))}</div><div><h2>${E(p.name)}</h2><small>${E(state.presets[p.kind].name)}</small></div><span class="pill ${p.error?'warning':p.refreshed_at?'positive':'neutral'}">${p.error?'Needs attention':p.refreshed_at?'Connected':'Unchecked'}</span></div>${endpoint(p.base_url)}<div class="provider-meta"><span>${p.has_key?'Key saved securely':'No key required'}</span><span>${state.models.filter(m=>m.provider_id===p.id&&m.enabled).length} enabled models</span></div><div class="provider-meta"><span>Catalog checked</span><span>${relative(p.refreshed_at)}</span></div>${p.error?`<div class="error-note">${E(p.error)} Last cached models are preserved.</div>`:''}<div class="actions">${button('↻ Check & refresh','refresh-provider',false,`data-id="${p.id}"`)}${button('Edit','edit-provider',false,`data-id="${p.id}"`)}${button('Remove','delete-provider',false,`data-id="${p.id}"`)}</div></section>`).join('')}</div>`: `<section class="panel">${empty('⌘','All your providers. One place.','Choose a built-in provider profile or connect any OpenAI-compatible endpoint.',button('+ Add your first provider','add-provider',true))}</section>`) +
  '<p class="hint">API keys are stored in your system credential store. AnyLlama never sends your requests to a different connection automatically.</p>';
}
function modelRows() {
  const query = (document.querySelector('#model-search')?.value || '').toLowerCase();
  const filter = document.querySelector('#provider-filter')?.value || '';
  const enabledOnly = document.querySelector('#enabled-filter')?.checked;
  const rows = state.models.filter(m=>(!filter||m.provider_id===filter)&&(!enabledOnly||m.enabled)&&`${m.name} ${m.upstream_id} ${m.provider_name}`.toLowerCase().includes(query));
  const target = document.querySelector('#model-list');
  target.innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Enabled</th><th>Model</th><th>Provider</th><th>Capabilities</th><th>Context</th><th></th></tr></thead><tbody>${rows.map(m=>`<tr><td><input class="switch" type="checkbox" data-model-toggle="${E(m.id)}" aria-label="Enable ${E(m.name)}" ${m.enabled?'checked':''}></td><td class="model-name"><strong>${E(m.name)}</strong><small class="mono">${E(m.upstream_id)}</small></td><td>${E(m.provider_name)}</td><td><div class="capabilities">${cap(m.tools,'Tools')}${cap(m.vision,'Vision')}${cap(m.reasoning,'Reasoning')}</div></td><td class="mono">${num(m.context_length)}</td><td>${button('Configure','edit-model',false,`data-id="${E(m.id)}"`)}</td></tr>`).join('')}</tbody></table></div>`:empty('▦',state.models.length?'No matching models':'Your model catalog is empty',state.providers.length?'Refresh a provider to discover its models, or add a model manually.':'Connect a provider to start building your model list.',button(state.providers.length?'+ Add model manually':'+ Add provider',state.providers.length?'add-model':'add-provider'));
  document.querySelector('#model-count').textContent = `${rows.length} models · ${state.models.filter(m=>m.enabled).length} enabled`;
}
function models() {
  content.innerHTML = heading('Models','A curated model list for your workspace. Enable only what you need.',state.providers.length?button('+ Add model manually','add-model',true):button('+ Add provider','add-provider',true)) +
    `<div class="toolbar"><input class="search" id="model-search" type="search" placeholder="Search models…" aria-label="Search models"><select class="filter-select" id="provider-filter" aria-label="Filter by provider"><option value="">All providers</option>${state.providers.map(p=>`<option value="${p.id}">${E(p.name)}</option>`).join('')}</select><label class="checkbox-label"><input id="enabled-filter" type="checkbox"> Enabled only</label><span id="model-count" class="count-note"></span></div><section class="panel" id="model-list"></section><p class="hint">Tools are required for Agent. “?” means the provider did not advertise this capability. Configure a model to set missing metadata.</p>`;
  modelRows();
}
function activity() {
  content.innerHTML = heading('Activity','Understand every request, without saving your conversations.',button('↻ Refresh','refresh')) +
    `<section class="panel"><div class="panel-header"><h2>Request history</h2><span class="hint">Latest 100 requests · refreshes every 10s</span></div>${activityTable()}</section><p class="hint">≈ tok/s is estimated from stream timing, including reasoning and tools. Cost is reported by the provider or estimated from model rates; it is not an invoice. Missing data appears as —. Cancelled requests may have incomplete usage. No prompts or responses are saved.</p>`;
}
function updateSummary() {
  const u=state.updates;
  const errors={network:'GitHub could not be reached. Try again later.',rate_limit:'GitHub rate limit reached. Try again later.',no_release:'No published stable release was found yet.',invalid_release:'The release does not contain a package for this system.'};
  const status=u.available?`Version ${E(u.latest_version)} is available.`:u.latest_version?'You’re up to date.':u.checked_at?'No release information available.':'Not checked yet.';
  return `<p>Installed: <strong>v${E(state.version)}</strong> · ${status}</p>${u.error?`<p class="hint">${E(errors[u.error]||'The update check failed.')}${u.available?' Showing the last known release.':''}</p>`:''}<p class="hint">Last check: ${relative(u.checked_at)}</p><a class="text-link" href="${E(u.release_url)}" target="_blank" rel="noopener noreferrer">${u.available?'Open release to download →':'View GitHub releases →'}</a>`;
}
function refreshUpdateStatus() {
  const u=state.updates, link=document.querySelector('#update-link');
  link.hidden=!u.available; link.href=u.release_url; link.textContent=`Update v${u.latest_version}`;
  const summary=document.querySelector('#update-status'); if(summary) summary.innerHTML=updateSummary();
}
function settings() {
  const s = state.settings;
  content.innerHTML = heading('Settings','Make this little bridge your own.') + `<form id="settings-form" class="settings-layout"><section class="panel"><div class="settings-section"><h2>Connection</h2><p>Available only on this computer.</p><div class="field-row"><div class="field"><label for="port">Proxy port</label><input id="port" name="port" type="number" min="1024" max="65535" required value="${s.port}"><small>Changing the port requires restarting AnyLlama and updating VS / VS Code.</small>${button('Use Ollama default (11434)','ollama-port',false,'type="button"')}<small>Ollama’s standard address is http://localhost:11434. Use it only when this port is free. Add or update the Ollama connection in your IDE; automatic registration is not guaranteed.</small></div><div class="field"><label for="connect-timeout">Connect timeout (seconds)</label><input id="connect-timeout" name="connect_timeout" type="number" min="1" max="120" required value="${s.connect_timeout}"></div></div><div class="field"><label for="read-timeout">Stream inactivity timeout (seconds)</label><input id="read-timeout" name="read_timeout" type="number" min="5" max="3600" required value="${s.read_timeout}"><small>Increase this for models that think for a long time before responding.</small></div>${state.restart_required?'<div class="note-box">Restart AnyLlama to apply the new port. The current endpoint is still active.</div>':''}</div><div class="settings-section"><h2>Appearance & startup</h2><p>A familiar workspace, whenever you need it.</p><div class="field"><label for="theme">Theme</label><select id="theme" name="theme"><option value="system" ${s.theme==='system'?'selected':''}>Use system setting</option><option value="light" ${s.theme==='light'?'selected':''}>Light</option><option value="dark" ${s.theme==='dark'?'selected':''}>Dark</option></select></div>${state.autostart_available?`<label class="checkbox-label"><input name="autostart" type="checkbox" ${s.autostart?'checked':''}> Start AnyLlama when I sign in to Windows</label>`:'<small>Automatic startup is not available on this platform.</small>'}</div><div class="settings-section"><h2>Updates</h2><div id="update-status">${updateSummary()}</div><p class="hint">Checks public GitHub release metadata once a day. No keys, prompts or conversation data are sent. Download and install updates yourself after exiting AnyLlama.</p><label class="checkbox-label"><input name="check_updates" type="checkbox" ${s.check_updates?'checked':''}> Check for updates automatically</label><div class="actions spaced">${button('Check for updates','check-updates',false,'type="button"')}</div></div><div class="settings-section"><h2>Your data</h2><p>Export connections, model selections and settings. API keys are excluded.</p><a class="button" href="/admin/api/export" download>↓ Export configuration</a><p class="hint">Configuration stays on this device. Keys stay in your system credential store.</p></div><div class="dialog-footer"><button class="button primary" type="submit">Save settings</button></div></section></form>`;
}
function openModal(title, description, body, footer='', formId='') {
  document.querySelector('#modal-content').innerHTML = `${formId?`<form id="${formId}">`:''}<div class="dialog-header"><div><h2 id="dialog-title">${title}</h2><p>${description}</p></div><button class="close-dialog" type="button" data-action="close-modal" aria-label="Close dialog">×</button></div><div class="dialog-body">${body}<p class="inline-error" id="dialog-error" role="alert"></p></div>${footer?`<div class="dialog-footer">${footer}</div>`:''}${formId?'</form>':''}`;
  if (!dialog.open) dialog.showModal();
}
const progress = step => `<div class="wizard-progress"><span class="${step===1?'current':''}">01 Connect</span>→<span class="${step===2?'current':''}">02 Choose models</span>→<span class="${step===3?'current':''}">03 Visual Studio</span></div>`;
function openProvider(id) {
  const p = id ? state.providers.find(p=>p.id===id) : null;
  wizardProvider = p?.id || null;
  openModal(p?'Edit connection':'Bring a provider to your workspace',p?'Update the name or replace its saved API key.':'Your keys stay local. Your models come with you.',
    `${p?'':progress(1)}<input type="hidden" name="provider_id" value="${p?.id||''}"><div class="field"><label for="kind">Provider</label><select id="kind" name="kind" ${p?'disabled':''}>${Object.entries(state.presets).map(([key,preset])=>`<option value="${key}" ${p?.kind===key?'selected':''}>${E(preset.name)}</option>`).join('')}</select><small id="provider-note">${E(providerNote(p?.kind||'openrouter'))}</small></div><div class="field"><label for="provider-name">Connection name</label><input id="provider-name" name="name" value="${E(p?.name||'OpenRouter')}" required maxlength="80" autocomplete="off"></div><div class="field"><label for="base-url">API base URL</label><input id="base-url" name="base_url" type="url" value="${E(p?.base_url||state.presets.openrouter.base_url)}" required ${p?'readonly':''}><small>${p?'Create a new connection to use a different API URL.':'Use the provider API base URL, including /v1 where required. HTTP on a private network is unencrypted, including prompts and API keys.'}</small></div><div class="field"><label for="api-key">API key ${p?'(leave blank to keep current key)':''}</label><input id="api-key" name="api_key" type="password" autocomplete="new-password" spellcheck="false" placeholder="${p?.has_key?'••••••••  Saved securely':'Paste your API key'}" ${!p?'required':''}><small id="key-note">Stored in your system credential store. Never included in exports.</small></div>`,
    `<button type="button" class="button" data-action="close-modal">Cancel</button><button class="button primary" type="submit">${p?'Save connection':'Save & check connection →'}</button>`, 'provider-form');
}
function chooseModels(id) {
  const provider = state.providers.find(p=>p.id===id);
  const list = state.models.filter(m=>m.provider_id===id);
  openModal('Choose your models',`Connected to ${E(provider.name)}. These models will appear in Visual Studio.`,progress(2)+
    (list.length?`<div class="dialog-models">${list.map(m=>`<label class="dialog-model"><input type="checkbox" name="selected" value="${E(m.id)}" ${m.enabled?'checked':''}><div><strong>${E(m.name)}</strong><small>${E(m.upstream_id)} · Tools: ${m.tools===true?'supported':m.tools===false?'not supported':'unknown'}</small></div></label>`).join('')}</div>`:`<div class="note-box">No catalog models were returned. You can add a model manually in Models after finishing setup.</div>`) + `<input name="provider_id" type="hidden" value="${id}">`,
    '<button type="button" class="button" data-action="close-modal">Later</button><button class="button primary" type="submit">Save models & continue →</button>','selection-form');
}
function guide(wizard=false) {
  openModal('Your next stop: Visual Studio','One address connects your selected models.',(wizard?progress(3):'')+
    `<div class="setup-step"><span class="step-number">1</span><div><strong>Open model management</strong><p>In Visual Studio 2026, open Chat → model picker → Manage Models.</p></div></div><div class="setup-step"><span class="step-number">2</span><div><strong>Add an Ollama provider</strong><p>Choose Add model provider → Ollama. Set Endpoint URL to:</p></div></div>${endpoint(state.endpoint+'/v1')}<div class="setup-step"><span class="step-number">3</span><div><strong>Pick a model and start building</strong><p>Enable the new Copilot chat experience in VS 18.11 and create a new chat, then select your model. Older builds call this Agent (Preview). If the model list stays outdated after changing selections, remove and add the Ollama provider again using this address.</p></div></div><div class="note-box">For Agent, choose a model with <strong>Tools</strong> support. Your provider API key stays in AnyLlama; no Ollama installation is needed.</div><div class="note-box"><strong>Models greyed out?</strong> The tooltip “Your own models work only in Agent (Preview)” refers to the Visual Studio chat session. In VS 18.11, check Tools → Options → GitHub → Copilot → Copilot Chat → <strong>Use new Copilot chat experience</strong>, restart VS after changing it, then create a new thread. If VS says your GitHub account is blocked from preview Copilot features, check Editor preview features in your GitHub Copilot settings or ask your organization administrator.</div><p class="hint">Alternative: add an OpenAI-compatible provider using ${E(state.endpoint)}/v1 and a placeholder key if required by VS. Supported capabilities depend on the model.</p>`,
    button('Done, let’s build','close-modal',true));
}
function metadataFields(m={}) {
  return `<div class="field-row">${['tools','vision','reasoning'].map(key=>`<div class="field"><label for="cap-${key}">${key[0].toUpperCase()+key.slice(1)}</label><select name="${key}" id="cap-${key}"><option value="unknown" ${m[key]===null||m[key]===undefined?'selected':''}>Unknown</option><option value="true" ${m[key]===true?'selected':''}>Supported</option><option value="false" ${m[key]===false?'selected':''}>Not supported</option></select></div>`).join('')}<div class="field"><label for="context-length">Context tokens</label><input id="context-length" name="context_length" type="number" min="1" value="${E(m.context_length||'')}" placeholder="Unknown"></div><div class="field"><label for="output-tokens">Max output tokens</label><input id="output-tokens" name="max_output_tokens" type="number" min="1" value="${E(m.max_output_tokens||'')}" placeholder="Unknown"></div></div>`;
}
function pricingFields(m={}) {
  return `<details class="pricing-fields"><summary>Pricing (optional · USD per 1M tokens)</summary><p class="hint">Catalog rates are loaded automatically where available. Change a rate to override it; leave unchanged to keep automatic updates. Blank means unknown. Zero means free. Estimates exclude taxes, credits and subscription plans.</p><div class="field-row">${Object.entries(priceFields).map(([key,label])=>`<div class="field"><label for="${key}">${label} price</label><input id="${key}" name="${key}" type="number" min="0" step="any" value="${E(m[key]??'')}" placeholder="Unknown"></div>`).join('')}</div></details>`;
}
function modelDialog(id) {
  const m = id ? state.models.find(m=>m.id===id) : {};
  openModal(id?'Configure model':'Add a model manually','Metadata and pricing overrides survive catalog refreshes.',
    `${id?`<input name="model_id" type="hidden" value="${E(id)}"><p class="hint mono">${E(m.upstream_id)}</p>`:`<div class="field"><label for="model-provider">Connection</label><select id="model-provider" name="provider_id">${state.providers.map(p=>`<option value="${p.id}">${E(p.name)}</option>`).join('')}</select></div><div class="field"><label for="upstream-id">Provider model ID</label><input id="upstream-id" name="upstream_id" required maxlength="300" placeholder="e.g. vendor/model-name"></div>`}<div class="field"><label for="model-name">Display name</label><input id="model-name" name="name" value="${E(m.name||'')}" required maxlength="300"></div>${metadataFields(m)}${pricingFields(m)}<label class="checkbox-label"><input name="enabled" type="checkbox" ${m.enabled?'checked':''}> Make this model available in Visual Studio</label>`,
    '<button type="button" class="button" data-action="close-modal">Cancel</button><button class="button primary" type="submit">Save model</button>','model-form');
}
async function busy(node, fn) {
  if (node?.disabled) return;
  if (node) node.disabled=true;
  try { await fn(); } finally {if(node) node.disabled=false;}
}
document.addEventListener('click', async event => {
  const target = event.target.closest('[data-action]'); if (!target) return;
  const {action,id} = target.dataset;
  try { await busy(target, async()=> {
    if(action==='add-provider') openProvider();
    if(action==='edit-provider') openProvider(id);
    if(action==='close-modal') dialog.close();
    if(action==='guide') guide();
    if(action==='add-model') modelDialog();
    if(action==='edit-model') modelDialog(id);
    if(action==='copy') {await navigator.clipboard.writeText(target.dataset.value); toast('Endpoint copied');}
    if(action==='ollama-port') {
      const port=document.querySelector('#port'); port.value='11434'; port.focus();
      toast('Ollama default selected. Save settings, then restart AnyLlama and update your IDE endpoint.');
    }
    if(action==='check-updates') {
      await api('updates/check','POST'); await load(false);
      toast(state.updates.available?'A new version is available on GitHub.':state.updates.error?'Update check could not confirm a new version. See the status above.':'Update check completed.');
    }
    if(action==='refresh') await load();
    if(action==='pause') {await api('pause','POST',{paused:!state.paused}); await load(); toast(state.paused?'Proxy paused. Active requests continue.':'Proxy resumed');}
    if(action==='refresh-provider') {target.textContent='Checking…'; try {const r=await api(`providers/${id}/refresh`,'POST'); toast(`Connection checked · ${r.count} models found`);} finally {await load();}}
    if(action==='delete-provider') {if(confirm('Remove this connection, its saved key and model selections?')){await api(`providers/${id}`,'DELETE'); await load(); toast('Connection removed');}}
    if(action==='retry-setup') {const result=await api(`providers/${wizardProvider}/refresh`,'POST'); await load(); toast(`${result.count} models found`); chooseModels(wizardProvider);}
    if(action==='skip-check') {await load(); chooseModels(wizardProvider);}
  }); } catch(error) {toast(error.message,true);}
});
document.addEventListener('change', async event => {
  const node = event.target;
  if(node.id==='kind') {
    const preset=state.presets[node.value]; document.querySelector('#provider-name').value=preset.name; document.querySelector('#base-url').value=preset.base_url;
    document.querySelector('#provider-note').textContent=providerNote(node.value);
    document.querySelector('#api-key').value='';
    document.querySelector('#api-key').required=requiresKey(node.value); document.querySelector('#key-note').textContent=!requiresKey(node.value)?'Optional unless your server requires authentication. Saved in your system credential store when supplied.':'Stored in your system credential store.';
  }
  if(node.id==='provider-filter'||node.id==='enabled-filter') modelRows();
  if(node.dataset.modelToggle) {
    node.disabled=true;
    try {const updated=await api(`models/${urlId(node.dataset.modelToggle)}`,'PATCH',{enabled:node.checked}); const index=state.models.findIndex(m=>m.id===updated.id); state.models[index]=updated; toast(node.checked?'Model enabled. Refresh your models in VS.':'Model disabled'); document.querySelector('#model-count').textContent=`${state.models.length} models · ${state.models.filter(m=>m.enabled).length} enabled`;}
    catch(error) {node.checked=!node.checked; toast(error.message,true);} finally {node.disabled=false;}
  }
});
document.addEventListener('input', event=>{if(event.target.id==='model-search') modelRows();});
document.addEventListener('submit', async event => {
  const form=event.target; if(!['provider-form','selection-form','model-form','settings-form'].includes(form.id)) return;
  event.preventDefault(); const submit=form.querySelector('[type=submit]'); const f=new FormData(form); const values=Object.fromEntries(f);
  try {await busy(submit, async()=> {
    const errorNode=form.querySelector('#dialog-error'); if(errorNode) errorNode.textContent='';
    if(form.id==='provider-form') {
      const existing=values.provider_id;
      const body={name:values.name,kind:existing?state.providers.find(p=>p.id===existing).kind:values.kind,base_url:values.base_url};
      if(values.api_key || !existing) body.api_key=values.api_key||null;
      const saved=await api(existing?`providers/${existing}`:'providers',existing?'PUT':'POST',body);
      form.querySelector('[name=api_key]').value=''; wizardProvider=saved.id;
      if(existing) {dialog.close(); await load(); toast('Connection saved'); return;}
      submit.textContent='Checking connection…';
      try {await api(`providers/${saved.id}/refresh`,'POST'); await load(); chooseModels(saved.id);}
      catch(error) {await load(); openModal('Connection saved. Let’s check it.','Your key is safely stored. The provider could not be reached.',`${progress(1)}<div class="error-note">${E(error.message)}</div><p class="hint">You can retry the check, or continue and add models manually.</p>`,button('Continue manually','skip-check')+button('Retry connection','retry-setup',true));}
    }
    if(form.id==='selection-form') {
      const selected=new Set(f.getAll('selected'));
      for(const m of state.models.filter(m=>m.provider_id===values.provider_id)) if(m.enabled!==selected.has(m.id)) await api(`models/${urlId(m.id)}`,'PATCH',{enabled:selected.has(m.id)});
      await load(); guide(true);
    }
    if(form.id==='model-form') {
      const body={name:values.name,enabled:f.has('enabled')};
      for(const key of ['tools','vision','reasoning']) body[key]=values[key]==='unknown'?null:values[key]==='true';
      for(const key of ['context_length','max_output_tokens']) body[key]=values[key]?Number(values[key]):null;
      const original=state.models.find(m=>m.id===values.model_id);
      for(const key of Object.keys(priceFields)) {
        const value=values[key]===''?null:Number(values[key]);
        if(!original || value!==(original[key]??null)) body[key]=value;
      }
      if(values.model_id) await api(`models/${urlId(values.model_id)}`,'PATCH',body);
      else {body.upstream_id=values.upstream_id; await api(`providers/${values.provider_id}/models`,'POST',body);}
      dialog.close(); await load(); toast('Model saved');
    }
    if(form.id==='settings-form') {
      await api('settings','PUT',{port:Number(values.port),connect_timeout:Number(values.connect_timeout),read_timeout:Number(values.read_timeout),theme:values.theme,autostart:f.has('autostart'),check_updates:f.has('check_updates')}); await load(); toast('Settings saved'+(state.restart_required?' · restart to apply the port':''));
    }
  });} catch(error) {const target=form.querySelector('#dialog-error'); if(target) target.textContent=error.message; else toast(error.message,true);}
});
function route() {
  page=location.hash.slice(1); if(!names[page]) page='overview'; if(state) render();
}
window.addEventListener('hashchange',route);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if(state) applyTheme(state.settings.theme);});
setInterval(async()=>{if(state&&page==='activity'&&!dialog.open){try{await load();}catch{/* The explicit refresh button can retry. */}}},10000);
async function boot() {
  const hash=new URLSearchParams(location.hash.slice(1)); const token=hash.get('token');
  if(token) {
    history.replaceState(null,'',location.pathname+'#overview');
    const response=await fetch('/admin/api/session',{method:'POST',headers:{Authorization:`Bearer ${token}`}});
    if(!response.ok) throw new Error('This dashboard link has expired. Open it again from the tray icon.');
  }
  route(); await load(); document.body.dispatchEvent(new Event('status-ready'));
  if(firstLoad&&!state.providers.length) openProvider(); firstLoad=false;
}
boot().catch(error=>{content.innerHTML=empty('⌘','Open your workspace from the tray',E(error.message)+' Use the AnyLlama tray icon → Open dashboard.');});

setInterval(async()=>{if(state&&page!=='activity'&&!dialog.open){try{await load(false);}catch{/* Keep using cached update status. */}}},60000);
