/* ServiceDeck 控制台窗口：原生 JavaScript，只通过 window.pywebview.api（bridge.py）和 Python 通信。
 * 刷新节奏：窗口可见时每 3 秒刷新状态、每 10 秒扫描未纳入管理的服务、每 2 秒拉取选中服务的日志；窗口隐藏时全部暂停。 */
'use strict';

const S = {
  state: null,            // get_state() 的结果
  unmanaged: [],          // 未纳入管理的服务
  selected: null,         // {kind: 'service' | 'unmanaged', id}
  detail: null,           // 选中服务的详细配置（service_detail）
  search: '',
  logsTall: false,
  catalog: null,          // 模板目录
};
const UNMANAGED_KEY = '__unmanaged__';

// ---------- 小工具 ----------

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

async function api(name, ...args) {
  try {
    const r = await window.pywebview.api[name](...args);
    return r || { ok: false, message: '没有返回结果' };
  } catch (e) {
    return { ok: false, message: `和后台通信失败：${e && e.message ? e.message : e}` };
  }
}

let toastTimer = null;
function toast(msg, isError = false) {
  if (!msg) return;
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ''; }, isError ? 5000 : 2500);
}

function fmtUptime(sec) {
  if (!sec && sec !== 0) return '';
  const d = Math.floor(sec / 86400), hh = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  if (d) return `${d} 天 ${hh} 小时`;
  if (hh) return `${hh} 小时 ${m} 分`;
  if (m) return `${m} 分钟`;
  return `${sec} 秒`;
}

function serviceName(id) {
  const s = S.state && S.state.services[id];
  return s ? s.name : id;
}

// ---------- 弹窗 ----------

function openModal(el, { onClose } = {}) {
  const overlay = h('<div class="overlay"></div>');
  overlay.appendChild(el);
  $('#modal-root').appendChild(overlay);
  const close = () => { overlay.remove(); if (onClose) onClose(); };
  overlay._close = close;
  overlay.addEventListener('mousedown', e => { if (e.target === overlay) overlay._backdrop = true; });
  overlay.addEventListener('mouseup', e => {
    if (e.target === overlay && overlay._backdrop && !el.dataset.sticky) close();
    overlay._backdrop = false;
  });
  const first = el.querySelector('[autofocus], input, select, textarea, button.primary');
  if (first) setTimeout(() => first.focus(), 30);
  return close;
}

function topModal() {
  const all = $$('#modal-root .overlay');
  return all[all.length - 1];
}

function confirmDialog(prompt, { affected = [], okText = '确定', danger = false, title = '请确认' } = {}) {
  return new Promise(resolve => {
    const el = h(`<div class="dialog">
      <div class="dialog-head">${esc(title)}</div>
      <div class="dialog-body">
        <div class="confirm-text">${esc(prompt)}</div>
        ${affected.length ? `<ul class="affected">${affected.map(a => `<li>${esc(a)}</li>`).join('')}</ul>` : ''}
      </div>
      <div class="dialog-foot">
        <button data-act="no">取消</button>
        <button data-act="yes" class="${danger ? 'danger-fill' : 'primary'}">${esc(okText)}</button>
      </div></div>`);
    let done = false;
    const finish = v => { if (!done) { done = true; resolve(v); close(); } };
    const close = openModal(el, { onClose: () => finish(false) });
    el.querySelector('[data-act=yes]').onclick = () => finish(true);
    el.querySelector('[data-act=no]').onclick = () => finish(false);
    setTimeout(() => el.querySelector('[data-act=yes]').focus(), 30);
  });
}

function promptDialog(title, value = '', { placeholder = '', okText = '确定' } = {}) {
  return new Promise(resolve => {
    const el = h(`<div class="dialog">
      <div class="dialog-head">${esc(title)}</div>
      <div class="dialog-body"><input type="text" style="width:100%" placeholder="${esc(placeholder)}"></div>
      <div class="dialog-foot"><button data-act="no">取消</button><button data-act="yes" class="primary">${esc(okText)}</button></div></div>`);
    const input = el.querySelector('input');
    input.value = value;
    let done = false;
    const finish = v => { if (!done) { done = true; resolve(v); close(); } };
    const close = openModal(el, { onClose: () => finish(null) });
    el.querySelector('[data-act=yes]').onclick = () => finish(input.value.trim());
    el.querySelector('[data-act=no]').onclick = () => finish(null);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') finish(input.value.trim()); });
    setTimeout(() => { input.focus(); input.select(); }, 30);
  });
}

/* 调用需要"二次确认"的操作：后台返回 needs_confirm 时弹框，确认后带 confirm（强制结束再带 force）重试 */
async function withConfirm(name, args) {
  let flags = [];
  for (;;) {
    const r = await api(name, ...args, ...flags);
    if (!r.needs_confirm) return r;
    const force = r.confirm_kind === 'force_kill';
    const danger = force || r.confirm_kind === 'delete' || r.confirm_kind === 'stop_unmanaged';
    const affected = (r.affected || []).map(serviceName);
    const okText = force ? '强制结束' : r.confirm_kind === 'delete' ? '删除' : '确定';
    const yes = await confirmDialog(r.prompt, { affected, okText, danger });
    if (!yes) return { ok: false, cancelled: true };
    flags = force ? [true, true] : [true];
  }
}

function report(r, okMsg) {
  if (r.cancelled) return false;
  if (r.ok) { toast(okMsg || r.message); return true; }
  toast(r.message || '操作没有完成', true);
  return false;
}

// ---------- 数据刷新 ----------

async function refreshState() {
  const r = await api('get_state');
  if (!r.ok) return;
  S.state = r;
  renderAll();
}

async function refreshUnmanaged() {
  const r = await api('list_unmanaged');
  if (!r.ok) return;
  S.unmanaged = r.items || [];
  renderList();
  if (S.selected && S.selected.kind === 'unmanaged') renderDetail();
}

async function refreshDetail() {
  if (!S.selected || S.selected.kind !== 'service') { S.detail = null; return; }
  const r = await api('service_detail', S.selected.id);
  S.detail = r.ok ? r : null;
}

async function refreshLogs() {
  if (!S.selected || S.selected.kind !== 'service') return;
  const box = $('#logbox');
  if (!box) return;
  const id = S.selected.id;
  const r = await api('logs', id, S.logsTall ? 2000 : 200);
  if (!r.ok || !S.selected || S.selected.id !== id) return;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
  const text = (r.lines || []).join('\n');
  if (box.textContent !== text) {
    box.textContent = text || '（还没有日志）';
    box.classList.toggle('muted', !text);
    if (atBottom) box.scrollTop = box.scrollHeight;
  }
}

async function afterAction() {
  await refreshState();
  await refreshDetail();
  renderDetail();
  setTimeout(refreshState, 1200);
}

// ---------- 渲染：顶部 ----------

function renderAll() {
  renderSummary();
  renderBanner();
  renderList();
  renderDetail();
}

function renderSummary() {
  const st = S.state;
  if (!st) return;
  $('#summary-dot').className = 'dot ' + st.overall + (st.overall === 'yellow' ? ' pulse' : '');
  let text;
  if (st.registry_error) text = '服务清单有问题';
  else if (!st.manager_running) text = '服务管理器未运行';
  else text = `运行中 ${st.running_count}` + (st.error_count ? ` · 异常 ${st.error_count}` : '');
  $('#summary-text').textContent = text;
  $('#btn-stop-all').disabled = !st.manager_running;
}

function renderBanner() {
  const st = S.state, b = $('#banner');
  if (st.registry_error) {
    b.className = 'banner error';
    b.innerHTML = `<span class="grow">服务清单文件有问题：${esc(st.registry_error)}</span>
      <button id="banner-restore">恢复到上一个备份</button>`;
    b.hidden = false;
    $('#banner-restore').onclick = async () => {
      const r = await withConfirm('restore_backup', [0]);
      if (report(r, '已恢复到上一个备份')) refreshState();
    };
  } else if (!st.manager_running && Object.keys(st.services).length) {
    b.className = 'banner';
    b.innerHTML = `<span class="grow">服务管理器未运行，服务都没有在运行。</span><button class="primary" id="banner-start">启动全部服务</button>`;
    b.hidden = false;
    $('#banner-start').onclick = startAll;
  } else {
    b.hidden = true;
  }
}

// ---------- 渲染：左侧列表 ----------

function matches(svc) {
  const q = S.search.trim().toLowerCase();
  if (!q) return true;
  return [svc.name, svc.id, svc.port, svc.command].some(v => v != null && String(v).toLowerCase().includes(q));
}

function matchesUnmanaged(u) {
  const q = S.search.trim().toLowerCase();
  if (!q) return true;
  return [u.title, u.command, ...(u.ports || [])].some(v => String(v).toLowerCase().includes(q));
}

function collapsed(id, dflt = false) {
  const c = (S.state && S.state.settings && S.state.settings.collapsed) || {};
  return id in c ? c[id] : dflt;
}

function serviceRow(svc) {
  const selected = S.selected && S.selected.kind === 'service' && S.selected.id === svc.id;
  let meta = '', metaCls = '';
  if (svc.display === 'healthy') meta = svc.port ? `:${svc.port}` : '运行中';
  else if (svc.display === 'error') { meta = '异常'; metaCls = 'red'; }
  else if (svc.display !== 'manual') meta = svc.label;
  const tag = svc.manual ? '<span class="tag">手动开启</span>' : '';
  const pulse = svc.color === 'yellow' ? ' pulse' : '';
  return `<div class="row${selected ? ' selected' : ''}" data-kind="service" data-id="${esc(svc.id)}">
    <span class="dot ${svc.color}${pulse}"></span><span class="name">${esc(svc.name)}</span>
    <span class="meta ${metaCls}">${esc(meta)}</span>${tag}</div>`;
}

function renderList() {
  const st = S.state, list = $('#list');
  if (!st) return;
  const searching = !!S.search.trim();
  let html = '';
  for (const g of st.groups) {
    const svcs = g.services.map(id => st.services[id]).filter(matches);
    if (searching && !svcs.length) continue;
    const isCollapsed = !searching && collapsed(g.id);
    html += `<div class="group-head${isCollapsed ? ' collapsed' : ''}" data-group="${esc(g.id)}">
      <span class="caret">▾</span><span>${esc(g.name)}</span><span class="count">${g.running}/${g.total}</span></div>`;
    if (!isCollapsed) {
      html += svcs.map(serviceRow).join('');
      if (!svcs.length) html += '<div class="list-empty">（空分组）</div>';
    }
  }
  const um = S.unmanaged.filter(matchesUnmanaged);
  if (um.length) {
    const isCollapsed = !searching && collapsed(UNMANAGED_KEY, true);
    html += `<div class="group-head${isCollapsed ? ' collapsed' : ''}" data-group="${UNMANAGED_KEY}">
      <span class="caret">▾</span><span>未纳入管理</span><span class="count">${um.length}</span></div>`;
    if (!isCollapsed) {
      html += um.map(u => {
        const selected = S.selected && S.selected.kind === 'unmanaged' && S.selected.id === u.pid;
        const lan = u.exposure === 'lan' ? '<span class="tag warn">对局域网开放</span>' : '';
        return `<div class="row${selected ? ' selected' : ''}" data-kind="unmanaged" data-id="${u.pid}">
          <span class="dot hollow"></span><span class="name">${esc(u.title)}</span>
          <span class="meta">${u.ports.map(p => ':' + p).join(' ')}</span>${lan}</div>`;
      }).join('');
    }
  }
  if (!st.groups.length && !um.length && !st.registry_error) html = '<div class="list-empty">还没有服务</div>';
  if (searching && !html) html = '<div class="list-empty">没有找到匹配的服务</div>';
  const scroll = list.scrollTop;
  list.innerHTML = html;
  list.scrollTop = scroll;
}

$('#list').addEventListener('click', async e => {
  const head = e.target.closest('.group-head');
  if (head) {
    const id = head.dataset.group;
    const now = !head.classList.contains('collapsed');
    S.state.settings.collapsed = { ...(S.state.settings.collapsed || {}), [id]: now };
    renderList();
    api('set_collapsed', id, now);
    return;
  }
  const row = e.target.closest('.row');
  if (row) select(row.dataset.kind, row.dataset.kind === 'unmanaged' ? Number(row.dataset.id) : row.dataset.id);
});

async function select(kind, id) {
  S.selected = { kind, id };
  S.logsTall = false;
  renderList();
  if (kind === 'service') await refreshDetail();
  renderDetail(true);
  refreshLogs();
}

// ---------- 渲染：右侧详情 ----------

let lastDetailKey = null;

function renderDetail(force = false) {
  const el = $('#detail'), st = S.state;
  if (!st) return;
  let key, html;
  if (!S.selected) {
    const hasAny = Object.keys(st.services).length > 0;
    key = 'empty:' + hasAny;
    html = hasAny
      ? `<div class="empty-state"><h2>选择一个服务</h2><p>从左侧列表里选一个服务，查看它的状态、日志和操作。</p></div>`
      : `<div class="empty-state"><h2>还没有服务</h2>
          <p>点「新增服务」，选一种程序类型，再选文件夹或文件，ServiceDeck 会自动生成运行命令。</p>
          <button class="primary" data-act="add">＋ 新增服务</button></div>`;
  } else if (S.selected.kind === 'service') {
    const svc = st.services[S.selected.id];
    if (!svc) { S.selected = null; return renderDetail(true); }
    key = 'svc:' + JSON.stringify([svc, S.detail && S.detail.service, S.logsTall]);
    html = serviceDetailHtml(svc, S.detail);
  } else {
    const u = S.unmanaged.find(x => x.pid === S.selected.id);
    if (!u) {
      key = 'gone';
      html = `<div class="empty-state"><h2>这个进程已经不在了</h2><p>它可能已经退出，或者已被纳入管理。</p></div>`;
    } else {
      key = 'um:' + JSON.stringify(u);
      html = unmanagedDetailHtml(u);
    }
  }
  if (!force && key === lastDetailKey) return;
  lastDetailKey = key;
  const logText = $('#logbox') ? $('#logbox').textContent : null;
  const logScroll = $('#logbox') ? $('#logbox').scrollTop : 0;
  el.innerHTML = html;
  const box = $('#logbox');
  if (box && logText !== null) { box.textContent = logText; box.scrollTop = logScroll; }
}

function serviceDetailHtml(svc, d) {
  const cfg = d ? d.service : null;
  const active = ['healthy', 'starting', 'stopping'].includes(svc.display);
  let sub = '';
  if (svc.display === 'healthy' || svc.display === 'starting') {
    sub = [svc.uptime_seconds != null ? `已运行 ${fmtUptime(svc.uptime_seconds)}` : '', svc.pid ? `PID ${svc.pid}` : '']
      .filter(Boolean).join(' · ');
  } else if (svc.display === 'manual') sub = '手动开启的服务，平时停着是正常的';
  else if (svc.display === 'stopped' && !st().manager_running) sub = '服务管理器未运行';
  const reason = svc.reason ? `<div class="reason">${esc(svc.reason)}。看看下面的日志找原因，修好后点「启动」。</div>` : '';
  const info = [];
  info.push(['端口', svc.port ? esc(svc.port) : '<span class="muted">未设置</span>']);
  if (d) {
    info.push(['分组', esc(d.group_name)]);
    if (d.depends_on.length) info.push(['依赖', esc(d.depends_on.join('、'))]);
    if (d.dependents.length) info.push(['被依赖', esc(d.dependents.join('、'))]);
  }
  info.push(['命令', `<code class="cmd">${esc(svc.command)}</code>`]);
  if (svc.cwd) info.push(['文件夹', `<button class="link" data-act="reveal" data-path="${esc(svc.cwd)}">${esc(svc.cwd)}</button>`]);
  if (cfg && cfg.env_file) info.push(['环境变量文件', esc(cfg.env_file)]);
  info.push(['全部启动', svc.autostart ? '跟随「全部启动」' : '不跟随（手动开启）']);
  if (svc.start_warning) info.push(['启动前提示', esc(svc.start_warning)]);
  return `
    <div class="detail-head"><h2 class="selectable">${esc(svc.name)}</h2>
      <span class="pill"><span class="dot ${svc.color}"></span>${esc(svc.label)}</span></div>
    <div class="sub">${esc(sub)}</div>
    ${reason}
    <div class="actions">
      ${active ? '<button data-act="stop">停止</button>' : '<button class="primary" data-act="start">启动</button>'}
      <button data-act="restart">重启</button>
      <button data-act="edit">编辑</button>
      ${svc.port ? `<button data-act="web" ${svc.display === 'healthy' ? '' : 'disabled'}>打开网页</button>` : ''}
      <button data-act="console" title="在系统的「控制台」App 中打开完整日志">在「控制台」中看日志</button>
      <span class="spacer"></span>
      <button class="danger" data-act="delete">删除</button>
    </div>
    <dl class="info">${info.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    <div class="logs-head"><span>最新日志</span><span class="grow"></span>
      <button class="link" data-act="toggle-logs">${S.logsTall ? '收起' : '展开全部'}</button></div>
    <pre class="logbox${S.logsTall ? ' tall' : ''}" id="logbox"></pre>`;
}

function st() { return S.state; }

function unmanagedDetailHtml(u) {
  const lan = u.exposure === 'lan'
    ? '<span class="tag warn">对局域网开放</span> 同一网络里的其他设备也能访问它'
    : '仅本机可以访问';
  return `
    <div class="detail-head"><h2 class="selectable">${esc(u.title)}</h2>
      <span class="pill"><span class="dot hollow"></span>未纳入管理</span></div>
    <div class="sub">这个程序占用了端口，但不是由 ServiceDeck 启动的（可能是你在终端里手动运行的）。</div>
    <div class="actions">
      <button class="primary" data-act="adopt">纳入管理</button>
      <button class="danger" data-act="stop-um">停止</button>
    </div>
    <dl class="info">
      <dt>端口</dt><dd>${u.ports.map(esc).join('、')}</dd>
      <dt>访问范围</dt><dd>${lan}</dd>
      <dt>程序</dt><dd class="selectable">${esc(u.exe)}</dd>
      <dt>完整命令</dt><dd><code class="cmd">${esc(u.command)}</code></dd>
      <dt>工作文件夹</dt><dd>${u.cwd ? esc(u.cwd) : '<span class="muted">未知</span>'}</dd>
      <dt>PID</dt><dd>${u.pid}</dd>
      <dt>已运行</dt><dd>${esc(fmtUptime(u.uptime_seconds))}</dd>
    </dl>`;
}

$('#detail').addEventListener('click', async e => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const act = btn.dataset.act;
  const id = S.selected && S.selected.id;
  if (act === 'add') return openServiceForm();
  if (act === 'reveal') return report(await api('reveal', btn.dataset.path), ' ');
  if (act === 'toggle-logs') { S.logsTall = !S.logsTall; renderDetail(true); return refreshLogs(); }
  if (act === 'start' || act === 'stop' || act === 'restart') {
    btn.disabled = true;
    const r = await withConfirm(act, [id]);
    report(r);
    return afterAction();
  }
  if (act === 'edit') return openServiceForm({ editId: id });
  if (act === 'web') return report(await api('open_web', id), ' ');
  if (act === 'console') return report(await api('open_log', id), ' ');
  if (act === 'delete') {
    const r = await withConfirm('delete_service', [id]);
    if (report(r)) { S.selected = null; afterAction(); }
    return;
  }
  if (act === 'adopt') {
    const r = await api('adopt', id);
    if (!r.ok) return toast(r.message, true);
    return openServiceForm({ draft: r.draft, hints: r.hints, adopting: true });
  }
  if (act === 'stop-um') {
    const r = await withConfirm('stop_unmanaged', [id]);
    if (report(r)) { S.selected = null; refreshUnmanaged(); renderDetail(true); }
  }
});

// ---------- 顶部按钮 ----------

async function startAll() {
  const r = await withConfirm('start_all', []);
  report(r, '正在启动全部服务');
  afterAction();
}

$('#btn-start-all').onclick = startAll;
$('#btn-stop-all').onclick = async () => {
  const yes = await confirmDialog('停止全部服务和服务管理器？之后可以用「全部启动」再启动。', { okText: '全部停止', danger: true });
  if (!yes) return;
  const r = await api('stop_all');
  report(r);
  afterAction();
};
$('#btn-add').onclick = () => openServiceForm();
$('#btn-settings').onclick = () => openSettings();
$('#search').addEventListener('input', e => { S.search = e.target.value; renderList(); });

document.addEventListener('keydown', e => {
  const modal = topModal();
  if (e.key === 'Escape' && modal && !modal.querySelector('.dialog').dataset.sticky) { modal._close(); return; }
  if (e.metaKey && e.key === 'f') { e.preventDefault(); $('#search').focus(); }
  if (e.metaKey && e.key === 'n' && !modal) { e.preventDefault(); openServiceForm(); }
});

// ---------- 新增 / 编辑服务 ----------

async function loadCatalog() {
  if (!S.catalog) {
    const r = await api('template_catalog');
    S.catalog = r.ok ? r.templates : [];
  }
  return S.catalog;
}

async function openServiceForm({ editId = null, draft = null, hints = [], adopting = false } = {}) {
  const catalog = await loadCatalog();
  if (editId) {
    const r = await api('draft_for_edit', editId);
    if (!r.ok) return toast(r.message, true);
    draft = r.draft;
  }
  if (draft) return showForm({ catalog, editId, draft, hints, adopting });

  // 第一步：选程序类型
  const el = h(`<div class="dialog wide">
    <div class="dialog-head">新增服务：它是什么程序？</div>
    <div class="dialog-body"><div class="template-grid">
      ${catalog.map(t => `<button class="template-card" data-t="${t.id}"><b>${esc(t.label)}</b><span>${esc(t.description)}</span></button>`).join('')}
    </div></div>
    <div class="dialog-foot"><button data-act="cancel">取消</button></div></div>`);
  const close = openModal(el);
  el.querySelector('[data-act=cancel]').onclick = close;
  el.querySelectorAll('.template-card').forEach(b => b.onclick = () => {
    close();
    showForm({ catalog, draft: { template: b.dataset.t, template_fields: {} } });
  });
}

function showForm({ catalog, editId = null, draft, hints = [], adopting = false }) {
  const tpl = catalog.find(t => t.id === draft.template) || catalog.find(t => t.id === 'custom');
  const st = S.state;
  const groups = st.groups;
  const others = Object.values(st.services).filter(s => s.id !== editId);
  const health = draft.health || null;
  const title = editId ? `编辑「${esc(draft.name)}」` : adopting ? '纳入管理' : '新增服务';

  const fieldHtml = f => {
    const v = esc((draft.template_fields || {})[f.key] ?? '');
    let input;
    if (f.kind === 'folder' || f.kind === 'file') {
      input = `<div class="with-btn"><input type="text" data-tf="${f.key}" value="${v}" spellcheck="false">
        <button type="button" data-pick="${f.kind}" data-for="${f.key}">选择…</button></div>`;
    } else if (f.kind === 'select') {
      input = `<select data-tf="${f.key}" data-value="${v}"><option value="">（先选择文件夹）</option></select>`;
    } else {
      input = `<input type="text" data-tf="${f.key}" value="${v}" spellcheck="false" class="${f.key === 'command' ? 'mono' : ''}">`;
    }
    return `<div class="field" data-field="template_fields.${f.key}">
      <label>${esc(f.label)}${f.required ? '' : ' <span class="muted">（可选）</span>'}</label>${input}
      ${f.help ? `<div class="help">${esc(f.help)}</div>` : ''}<div class="err" hidden></div></div>`;
  };

  const envRows = Object.entries(draft.env || {});
  const el = h(`<div class="dialog wide" data-sticky="1">
    <div class="dialog-head"><span>${title}</span><span class="muted">· ${esc(tpl.label)}</span><span class="grow"></span>
      ${!editId && !adopting ? '<button class="link" data-act="change-type">更换类型</button>' : ''}</div>
    <div class="dialog-body">
      ${hints.length ? `<div class="note"><ul>${hints.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
      <div class="general-errors" hidden></div>
      <fieldset><legend>程序</legend>
        ${tpl.fields.map(fieldHtml).join('')}
        <div class="field" data-field="command" ${tpl.id === 'custom' ? 'hidden' : ''}>
          <label>将运行的命令 <span class="muted">（自动生成，可以修改）</span>
            <button type="button" class="link" data-act="regen" hidden>恢复自动生成</button></label>
          <textarea class="mono" rows="2" data-k="command" spellcheck="false"></textarea>
          <div class="help" data-k="cwd-help"></div>
          <ul class="hints" data-k="hints"></ul>
          <div class="err" hidden></div>
        </div>
      </fieldset>
      <fieldset><legend>基本设置</legend>
        <div class="row2">
          <div class="field" data-field="name"><label>名称</label>
            <input type="text" data-k="name" value="${esc(draft.name || '')}" placeholder="比如：我的网站"><div class="err" hidden></div></div>
          <div class="field" data-field="group"><label>分组</label>
            <select data-k="group">
              ${groups.map(g => `<option value="${esc(g.id)}" ${g.id === draft.group ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}
              <option value="__new__" ${groups.length ? '' : 'selected'}>＋ 新建分组…</option>
            </select>
            <input type="text" data-k="new-group" placeholder="新分组名称" value="${groups.length ? '' : '我的服务'}" style="margin-top:6px" ${groups.length ? 'hidden' : ''}>
            <div class="err" hidden></div></div>
          <div class="field" data-field="port"><label>端口 <span class="muted">（可选）</span></label>
            <input type="text" data-k="port" value="${esc(draft.port ?? '')}" inputmode="numeric" placeholder="如 8000"><div class="err" hidden></div></div>
        </div>
        <div class="help" style="margin-top:-4px">填了端口，启动前会检查它有没有被别的程序占用，并用它判断服务是否已经起来。</div>
      </fieldset>
      <details class="advanced" ${editId && (envRows.length || draft.env_file || (draft.depends_on || []).length || health || draft.restart !== 'on_failure' || draft.autostart === false || draft.start_warning) ? 'open' : ''}>
        <summary>高级设置</summary>
        <div class="field" data-field="env"><label>环境变量</label>
          <div data-k="env-rows"></div>
          <button type="button" data-act="add-env">＋ 添加一行</button>
          <div class="help">密码、密钥等敏感内容不要填在这里，请放进 .env 文件，再在下面选择那个文件。</div>
          <div class="err" hidden></div></div>
        <div class="field" data-field="env_file"><label>环境变量文件 <span class="muted">（可选，如 .env）</span></label>
          <div class="with-btn"><input type="text" data-k="env_file" value="${esc(draft.env_file || '')}" spellcheck="false">
            <button type="button" data-pick="file" data-for-k="env_file">选择…</button></div><div class="err" hidden></div></div>
        <div class="field" data-field="depends_on"><label>依赖 <span class="muted">（先等这些服务启动好，再启动它）</span></label>
          <div class="dep-list">${others.length ? others.map(s => `<label class="checkline"><input type="checkbox" data-dep="${esc(s.id)}"
            ${(draft.depends_on || []).includes(s.id) ? 'checked' : ''}> ${esc(s.name)}</label>`).join('') : '<span class="muted">没有其他服务</span>'}</div>
          <div class="err" hidden></div></div>
        <div class="field" data-field="health"><label>怎样算启动成功</label>
          <select data-k="health-type">
            <option value="">自动：有端口就看端口能否连上，没有端口就看程序是否在运行</option>
            <option value="http" ${health && health.type === 'http' ? 'selected' : ''}>打开某个网址返回正常（需要端口）</option>
            <option value="exec" ${health && health.type === 'exec' ? 'selected' : ''}>运行一条检查命令成功</option>
          </select>
          <div class="with-btn" data-k="health-extra" style="margin-top:6px">
            <input type="text" data-k="health-value" spellcheck="false" class="mono"
              value="${esc(health ? (health.type === 'http' ? health.path : health.command) : '')}">
            <input type="number" data-k="health-timeout" min="5" style="width:110px" title="最长等待秒数"
              value="${esc(health ? health.timeout : 60)}"> <span class="muted" style="align-self:center">秒内</span>
          </div>
          <div class="err" hidden></div></div>
        <div class="field" data-field="restart"><label>崩溃后</label>
          <select data-k="restart">
            <option value="on_failure">自动重启（最多连续 3 次）</option>
            <option value="always" ${draft.restart === 'always' ? 'selected' : ''}>总是重启（最多连续 3 次）</option>
            <option value="no" ${draft.restart === 'no' ? 'selected' : ''}>不重启</option>
          </select></div>
        <label class="checkline"><input type="checkbox" data-k="autostart" ${draft.autostart === false ? '' : 'checked'}> 跟随「全部启动」一起启动（取消勾选就是"手动开启"）</label>
        <div class="field" data-field="start_warning" style="margin-top:8px"><label>启动前提醒 <span class="muted">（可选，有风险的服务用，如"开启后本机服务可从公网访问"）</span></label>
          <input type="text" data-k="start_warning" value="${esc(draft.start_warning || '')}"><div class="err" hidden></div></div>
      </details>
    </div>
    <div class="trial" data-k="trial" hidden>
      <div class="trial-head"><b>试运行</b><span data-k="trial-status" class="grow"></span>
        <button data-act="trial-stop">停止试运行</button></div>
      <pre class="logbox" data-k="trial-logs"></pre>
    </div>
    <div class="dialog-foot">
      <button data-act="trial" title="用当前填写的内容临时运行 15 秒，不保存">试运行 15 秒</button>
      <span class="grow"></span>
      <button data-act="cancel">取消</button>
      <button class="primary" data-act="save">保存</button>
    </div></div>`);

  const q = sel => el.querySelector(sel);
  const K = k => el.querySelector(`[data-k="${k}"]`);
  let trialId = null, trialTimer = null;
  const stopTrialQuietly = () => {
    clearInterval(trialTimer);
    if (trialId) api('stop_trial', trialId);
    trialId = null;
  };
  const close = openModal(el, { onClose: stopTrialQuietly });

  // 命令预览
  const cmdBox = K('command');
  let commandEdited = false, lastGenerated = null;
  cmdBox.value = draft.command || '';
  const hasInputs = Object.values(draft.template_fields || {}).some(v => v);
  if (editId && !hasInputs && draft.command) commandEdited = true;  // 手写进清单、没有模板输入的服务
  cmdBox.addEventListener('input', () => { commandEdited = cmdBox.value.trim() !== (lastGenerated || ''); q('[data-act=regen]').hidden = !commandEdited; });
  q('[data-act=regen]').onclick = () => { commandEdited = false; q('[data-act=regen]').hidden = true; runPreview(); };

  const inputs = () => {
    const o = {};
    el.querySelectorAll('[data-tf]').forEach(i => { if (i.value.trim()) o[i.dataset.tf] = i.value.trim(); });
    return o;
  };

  const setFieldError = (field, msg, fix) => {
    const box = el.querySelector(`[data-field="${field}"]`);
    if (!box) return false;
    box.classList.toggle('has-err', !!msg);
    const err = box.querySelector('.err');
    err.hidden = !msg;
    err.innerHTML = msg ? esc(msg) + (fix === 'chmod' ? ' <button type="button" class="link" data-act="fix-chmod">一键添加执行权限</button>' : '') : '';
    return true;
  };
  const clearErrors = () => {
    el.querySelectorAll('.field .err').forEach(e => { e.hidden = true; e.innerHTML = ''; });
    el.querySelectorAll('.field.has-err').forEach(e => e.classList.remove('has-err'));
    q('.general-errors').hidden = true;
  };
  const showErrors = errors => {
    clearErrors();
    const general = [];
    for (const e of errors || []) {
      let field = e.field === 'health' || String(e.field).startsWith('health') ? 'health' : e.field;
      if (tpl.id === 'custom' && field === 'command') field = 'template_fields.command';
      if (!setFieldError(field, e.message, e.fix)) general.push(e.message);
    }
    if (general.length) { q('.general-errors').hidden = false; q('.general-errors').innerHTML = general.map(esc).join('<br>'); }
    const first = el.querySelector('.field.has-err, .general-errors:not([hidden])');
    if (first) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
  };

  let previewSeq = 0;
  async function runPreview() {
    const seq = ++previewSeq;
    const inp = inputs();
    if (!Object.keys(inp).length) return;
    const r = await api('preview', tpl.id, inp);
    if (seq !== previewSeq) return;
    el.querySelectorAll('[data-field^="template_fields."]').forEach(f => setFieldError(f.dataset.field, ''));
    if (!r.ok) {
      (r.errors || []).forEach(e => setFieldError(e.field, e.message, e.fix));
      K('hints').innerHTML = '';
      return;
    }
    lastGenerated = r.command;
    if (!commandEdited) cmdBox.value = r.command;
    else if (cmdBox.value.trim() === r.command) { commandEdited = false; }
    q('[data-act=regen]').hidden = !commandEdited;
    K('cwd-help').textContent = r.cwd ? `在文件夹 ${r.cwd} 中运行` : '';
    K('hints').innerHTML = (r.hints || []).map(x => `<li>${esc(x)}</li>`).join('');
    const port = K('port');
    if (r.suggested_port && !port.value) port.value = r.suggested_port;
  }
  let previewTimer = null;
  const schedulePreview = () => { clearTimeout(previewTimer); previewTimer = setTimeout(runPreview, 250); };

  // Node 模板：文件夹变了就重新读取 package.json 的 scripts
  async function loadChoices() {
    const sel = el.querySelector('select[data-tf]');
    if (!sel) return;
    const folder = (el.querySelector('[data-tf=folder]') || {}).value;
    if (!folder) return;
    const r = await api('template_choices', tpl.id, sel.dataset.tf, { folder });
    const cur = sel.value || sel.dataset.value;
    if (!r.ok) {
      sel.innerHTML = '<option value="">（读取不到可选项）</option>';
      (r.errors || []).forEach(e => setFieldError(e.field, e.message, e.fix));
      return;
    }
    sel.innerHTML = '<option value="">请选择…</option>' + r.choices.map(c =>
      `<option value="${esc(c.value)}" ${c.value === cur ? 'selected' : ''}>${esc(c.label)}</option>`).join('');
    if (!sel.value && r.choices.length === 1) sel.value = r.choices[0].value;
  }

  el.querySelectorAll('[data-tf]').forEach(i => {
    i.addEventListener('input', () => { schedulePreview(); if (i.dataset.tf === 'folder') loadChoices(); });
    i.addEventListener('change', schedulePreview);
  });

  // 名称：选了文件夹或文件、名称还空着时，用它的名字做默认值
  const suggestName = path => {
    const nm = K('name');
    if (!nm.value && path) nm.value = path.replace(/\/+$/, '').split('/').pop().replace(/\.(sh|py|js)$/, '');
  };

  el.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.pick) {
      const r = await api(b.dataset.pick === 'folder' ? 'pick_folder' : 'pick_file');
      if (!r.ok) return r.cancelled ? null : toast(r.message, true);
      const target = b.dataset.for ? el.querySelector(`[data-tf="${b.dataset.for}"]`) : K(b.dataset.forK);
      target.value = r.path;
      if (b.dataset.for) { suggestName(r.path); if (b.dataset.for === 'folder') loadChoices(); runPreview(); }
      return;
    }
    const act = b.dataset.act;
    if (act === 'cancel') close();
    else if (act === 'change-type') { close(); openServiceForm(); }
    else if (act === 'add-env') addEnvRow('', '');
    else if (act === 'del-env') b.closest('.env-row').remove();
    else if (act === 'fix-chmod') {
      const r = await api('fix_permission', inputs().file);
      if (report(r)) runPreview();
    } else if (act === 'save') save(b);
    else if (act === 'trial') startTrial(b);
    else if (act === 'trial-stop') { if (trialId) { await api('stop_trial', trialId); pollTrial(); } }
  });

  // 用户改了某个输入框，就清掉它旁边的旧错误提示
  el.addEventListener('input', e => {
    const f = e.target.closest('.field');
    if (f && f.classList.contains('has-err')) setFieldError(f.dataset.field, '');
  });

  // 分组：新建
  K('group').addEventListener('change', () => { K('new-group').hidden = K('group').value !== '__new__'; if (!K('new-group').hidden) K('new-group').focus(); });

  // 健康检查
  const syncHealth = () => {
    const t = K('health-type').value;
    K('health-extra').hidden = !t;
    K('health-value').placeholder = t === 'http' ? '网址路径，如 /health' : '检查命令，成功时退出码为 0';
  };
  K('health-type').addEventListener('change', syncHealth);
  syncHealth();

  // 环境变量
  function addEnvRow(k, v) {
    const row = h(`<div class="env-row"><input type="text" placeholder="名称，如 PORT" class="mono" spellcheck="false">
      <input type="text" placeholder="值" class="mono" spellcheck="false"><button type="button" data-act="del-env" title="删除这一行">−</button></div>`);
    row.children[0].value = k;
    row.children[1].value = v;
    K('env-rows').appendChild(row);
  }
  envRows.forEach(([k, v]) => addEnvRow(k, v));

  function collectDraft() {
    const env = {};
    K('env-rows').querySelectorAll('.env-row').forEach(r => {
      const k = r.children[0].value.trim();
      if (k) env[k] = r.children[1].value;
    });
    const ht = K('health-type').value;
    const hv = K('health-value').value.trim();
    const timeout = parseInt(K('health-timeout').value, 10) || 60;
    const portText = K('port').value.trim();
    const d = {
      name: K('name').value.trim(),
      group: K('group').value,
      template: tpl.id,
      template_fields: inputs(),
      command: commandEdited ? cmdBox.value.trim() : '',
      port: portText === '' ? null : (/^\d+$/.test(portText) ? parseInt(portText, 10) : portText),
      env,
      env_file: K('env_file').value.trim() || null,
      depends_on: $$('[data-dep]', el).filter(c => c.checked).map(c => c.dataset.dep),
      health: ht ? (ht === 'http' ? { type: 'http', path: hv, timeout } : { type: 'exec', command: hv, timeout }) : null,
      restart: K('restart').value,
      autostart: K('autostart').checked,
      start_warning: K('start_warning').value.trim() || null,
    };
    if (draft.shutdown_signal && draft.shutdown_signal !== 15) d.shutdown_signal = draft.shutdown_signal;
    if (draft.adopt_pid) d.adopt_pid = draft.adopt_pid;
    if (!d.command && commandEdited === false && !Object.keys(d.template_fields).length) d.command = cmdBox.value.trim();
    return d;
  }

  async function save(btn) {
    const d = collectDraft();
    if (d.group === '__new__') {
      const name = K('new-group').value.trim();
      if (!name) return showErrors([{ field: 'group', message: '请填写新分组的名称' }]);
      const g = await api('add_group', name);
      if (!g.ok) return showErrors([{ field: 'group', message: g.message }]);
      d.group = g.id;
      await refreshState();
      const opt = document.createElement('option');
      opt.value = g.id; opt.textContent = name;
      K('group').prepend(opt); K('group').value = g.id; K('new-group').hidden = true;
    }
    btn.disabled = true;
    const r = await withConfirm('save_service', [d, editId]);
    btn.disabled = false;
    if (r.cancelled) return;
    if (!r.ok) { showErrors(r.errors); return toast(r.message, true); }
    stopTrialQuietly();
    close();
    toast(editId ? '已保存' : `已新增「${d.name}」`);
    await refreshState();
    select('service', r.id);
    if (adopting) refreshUnmanaged();
  }

  async function startTrial(btn) {
    stopTrialQuietly();
    btn.disabled = true;
    const r = await api('start_trial', collectDraft());
    btn.disabled = false;
    if (!r.ok) { showErrors(r.errors); return toast(r.message, true); }
    trialId = r.trial_id;
    K('trial').hidden = false;
    K('trial-logs').textContent = '';
    clearInterval(trialTimer);
    trialTimer = setInterval(pollTrial, 1000);
    pollTrial();
  }

  async function pollTrial() {
    if (!trialId) return;
    const r = await api('trial_status', trialId);
    if (!r.ok) return;
    const logs = K('trial-logs');
    const atBottom = logs.scrollHeight - logs.scrollTop - logs.clientHeight < 30;
    logs.textContent = (r.logs || []).join('\n') || '（还没有输出）';
    if (atBottom) logs.scrollTop = logs.scrollHeight;
    let status;
    const portText = r.port ? (r.port_up ? `<span class="ok-text">端口 ${r.port} 已起来 ✓</span>` : `端口 ${r.port} 还没起来`) : '';
    if (r.finished) {
      clearInterval(trialTimer);
      const exited = r.exit_code != null && r.exit_code !== 0 && r.exit_code !== -1;
      const portFinal = r.port ? (r.port_up ? `<span class="ok-text">端口 ${r.port} 起来过 ✓</span>` : `<span class="bad-text">端口 ${r.port} 没有起来 ✗</span>`) : '';
      status = `已结束 · ${exited ? `<span class="bad-text">程序退出（退出码 ${r.exit_code}）</span>` : '没有发现问题'} ${portFinal ? '· ' + portFinal : ''}`;
      q('[data-act=trial-stop]').hidden = true;
      trialId = null;
    } else {
      status = `${r.running ? '运行中' : '启动中'}，还剩 ${r.remaining} 秒 ${portText ? '· ' + portText : ''}`;
      q('[data-act=trial-stop]').hidden = false;
    }
    K('trial-status').innerHTML = status;
  }

  // 初次打开：Node 读 scripts，然后生成命令预览
  (async () => {
    await loadChoices();
    if (hasInputs || !editId) await runPreview();
    if (editId && hasInputs && lastGenerated !== null && draft.command && draft.command !== lastGenerated) {
      commandEdited = true; cmdBox.value = draft.command; q('[data-act=regen]').hidden = false;
    }
  })();
}

// ---------- 设置 ----------

async function openSettings() {
  const el = h(`<div class="dialog wide">
    <div class="dialog-head">设置</div>
    <div class="dialog-body">
      <div class="setting-line"><div class="grow">登录时启动 ServiceDeck<div class="help">开机登录后自动打开（只出现菜单栏图标）</div></div>
        <input type="checkbox" class="switch" data-setting="login_launch"></div>
      <div class="setting-line"><div class="grow">开机后自动启动服务<div class="help">每次开机后第一次打开 ServiceDeck 时，自动执行一次「全部启动」</div></div>
        <input type="checkbox" class="switch" data-setting="boot_autostart"></div>
      <h3 class="section">分组 <span class="muted">（拖动调整顺序）</span></h3>
      <div data-k="groups"></div>
      <button data-act="new-group">＋ 新建分组</button>
      <h3 class="section">服务清单备份 <span class="muted">（每次保存前自动备份，保留最近 20 份）</span></h3>
      <div data-k="backups"></div>
    </div>
    <div class="dialog-foot"><button class="primary" data-act="close">完成</button></div></div>`);
  const close = openModal(el);
  const K = k => el.querySelector(`[data-k="${k}"]`);

  const s = (await api('get_settings'));
  el.querySelectorAll('[data-setting]').forEach(c => {
    c.checked = !!s[c.dataset.setting];
    c.onchange = async () => { const r = await api('set_setting', c.dataset.setting, c.checked); if (!r.ok) toast(r.message, true); };
  });

  async function renderGroups() {
    await refreshState();
    const gs = S.state.groups;
    K('groups').innerHTML = gs.length ? gs.map(g => `<div class="group-item" draggable="true" data-id="${esc(g.id)}">
      <span class="handle">⠿</span><span class="grow">${esc(g.name)} <span class="muted">${g.services.length} 个服务</span></span>
      <button class="link" data-act="rename">改名</button><button class="link danger" data-act="del">删除</button></div>`).join('')
      : '<div class="muted">还没有分组，新增服务时可以顺便新建。</div>';
  }
  async function renderBackups() {
    const r = await api('list_backups');
    const items = (r.items || []);
    K('backups').innerHTML = items.length ? items.map(b => `<div class="backup-item"><span class="grow">${esc(b.time)} · ${b.services == null ? '无法读取' : b.services + ' 个服务'}</span>
      <button data-act="restore" data-index="${b.index}">恢复</button></div>`).join('') : '<div class="muted">还没有备份</div>';
  }
  await renderGroups();
  renderBackups();

  let dragId = null;
  K('groups').addEventListener('dragstart', e => { const it = e.target.closest('.group-item'); dragId = it && it.dataset.id; });
  K('groups').addEventListener('dragover', e => {
    const it = e.target.closest('.group-item');
    if (!it) return;
    e.preventDefault();
    $$('.group-item', el).forEach(x => x.classList.toggle('drag-over', x === it));
  });
  K('groups').addEventListener('drop', async e => {
    e.preventDefault();
    const it = e.target.closest('.group-item');
    $$('.group-item', el).forEach(x => x.classList.remove('drag-over'));
    if (!it || !dragId || it.dataset.id === dragId) return;
    const ids = S.state.groups.map(g => g.id).filter(id => id !== dragId);
    ids.splice(ids.indexOf(it.dataset.id), 0, dragId);
    const r = await api('reorder_groups', ids);
    if (!r.ok) toast(r.message, true);
    await renderGroups();
  });

  el.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    const act = b.dataset.act;
    const gid = b.closest('.group-item') && b.closest('.group-item').dataset.id;
    if (act === 'close') close();
    else if (act === 'new-group') {
      const name = await promptDialog('新建分组', '', { placeholder: '分组名称' });
      if (name) { report(await api('add_group', name)); renderGroups(); }
    } else if (act === 'rename') {
      const g = S.state.groups.find(x => x.id === gid);
      const name = await promptDialog('分组改名', g.name);
      if (name && name !== g.name) { report(await api('rename_group', gid, name)); renderGroups(); }
    } else if (act === 'del') {
      const g = S.state.groups.find(x => x.id === gid);
      if (!(await confirmDialog(`删除分组「${g.name}」？`, { okText: '删除', danger: true }))) return;
      report(await api('delete_group', gid));
      renderGroups();
    } else if (act === 'restore') {
      const r = await withConfirm('restore_backup', [Number(b.dataset.index)]);
      if (report(r, '已恢复')) { renderBackups(); refreshState(); }
    }
  });
}

// ---------- 定时刷新（窗口隐藏时暂停） ----------

function visible() { return !document.hidden; }

setInterval(() => { if (visible() && window.pywebview) refreshState(); }, 3000);
setInterval(() => { if (visible() && window.pywebview) refreshUnmanaged(); }, 10000);
setInterval(() => { if (visible() && window.pywebview) refreshLogs(); }, 2000);
document.addEventListener('visibilitychange', () => {
  if (visible() && window.pywebview) { refreshState(); refreshUnmanaged(); refreshLogs(); }
});

async function init() {
  await refreshState();
  refreshUnmanaged();
}

if (window.pywebview && window.pywebview.api) init();
else window.addEventListener('pywebviewready', init);
