/* ServiceDeck 界面道具（深色模式，按 1080P 画面放大），文案与真实产品一致（设计文档 5.3）。
 * 用法：场景给出"某一时刻的界面状态"对象，道具把它画出来；同样的状态永远画出同样的画面。 */
'use strict';

/* ---------- 片中的虚构服务（设计文档第 5 节） ---------- */
const SVC = {
  mysql: { name: 'MySQL', group: 'shared', port: 3306, pid: 812, cmd: '/opt/homebrew/opt/mysql/bin/mysqld_safe --datadir=/opt/homebrew/var/mysql' },
  redis: { name: 'Redis', group: 'shared', port: 6379, pid: 815, cmd: '/opt/homebrew/opt/redis/bin/redis-server /opt/homebrew/etc/redis.conf' },
  ollama: { name: 'Ollama', group: 'ai', port: 11434, pid: 820, cmd: '/opt/homebrew/bin/ollama serve' },
  sd: { name: 'Stable Diffusion WebUI', group: 'ai', port: 7860, pid: 833, cmd: './webui.sh --api', cwd: '~/Projects/sd-webui' },
  blog: { name: '个人博客', group: 'mine', port: 4000, pid: 846, cmd: 'npm run start', cwd: '~/Projects/my-blog', deps: ['mysql', 'redis'] },
  ledger: { name: '我的记账本', group: 'mine', port: 8000, pid: 2231, cmd: '~/Projects/my-ledger/.venv/bin/python -m uvicorn main:app --port 8000', cwd: '~/Projects/my-ledger' },
};
const GROUPS = [
  { id: 'shared', name: '公共服务', ids: ['mysql', 'redis'] },
  { id: 'ai', name: 'AI 工具', ids: ['ollama', 'sd'] },
  { id: 'mine', name: '我的项目', ids: ['blog', 'ledger'] },
];
const UNMANAGED = {
  http: { title: 'python3 -m http.server', port: 8080, lan: true },
  vite: { title: 'npm run dev', port: 5173, lan: false },
  jupyter: { title: 'jupyter notebook', port: 8888, lan: false, exe: '~/Projects/data-notes/.venv/bin/python3',
    command: 'jupyter notebook', cwd: '~/Projects/data-notes', pid: 5127, uptime: '8 秒' },
};
const DISPLAY = { healthy: ['green', '运行中'], starting: ['yellow', '启动中'], stopped: ['grey', '已停止'] };

/** 状态对象的默认值：6 个服务里前 5 个运行中（记账本尚未新增） */
function uiState(over = {}) {
  return Object.assign({
    manager: true,
    st: { mysql: 'healthy', redis: 'healthy', ollama: 'healthy', sd: 'healthy', blog: 'healthy' },
    uptime: {},          // id → 已运行秒数
    selected: null,      // 服务 id 或 'um:<id>'
    unmanaged: [],       // 未纳入管理的服务 id 列表
    fresh: null,         // 刚出现的行（高亮一下）
    modal: null,
    toast: null,
    press: null,         // 正被按下的按钮 data-k
    logs: {},            // id → 日志行数组
  }, over);
}

function summaryOf(s) {
  const ids = Object.keys(s.st);
  const running = ids.filter(id => s.st[id] === 'healthy').length;
  let color = 'grey';
  if (s.manager) {
    if (ids.some(id => s.st[id] === 'starting')) color = 'yellow';
    else if (running) color = 'green';
  }
  return { running, color };
}

/* ---------- 控制台窗口 ---------- */
const btn = (s, k, label, cls = '') => `<b class="btn ${cls}${s.press === k ? ' press' : ''}" data-k="${k}">${label}</b>`;

function rowHtml(s, id) {
  const d = SVC[id], st = s.st[id], [color] = DISPLAY[st];
  const meta = st === 'healthy' ? `:${d.port}` : DISPLAY[st][1];
  return `<div class="w-row${s.selected === id ? ' sel' : ''}${s.fresh === id ? ' fresh' : ''}" data-k="row-${id}">` +
    `<i class="dot ${color}"></i><span class="nm">${esc(d.name)}</span><span class="mt">${esc(meta)}</span></div>`;
}

function listHtml(s) {
  let h = '';
  for (const g of GROUPS) {
    const ids = g.ids.filter(id => id in s.st);
    if (!ids.length) continue;
    const run = ids.filter(id => s.st[id] === 'healthy').length;
    h += `<div class="w-gh"><span class="tri">▾</span><span>${g.name}</span><span class="cnt">${run}/${ids.length}</span></div>`;
    h += ids.map(id => rowHtml(s, id)).join('');
  }
  if (s.unmanaged.length) {
    h += `<div class="w-gh"><span class="tri">▾</span><span>未纳入管理</span><span class="cnt">${s.unmanaged.length}</span></div>`;
    h += s.unmanaged.map(id => {
      const u = UNMANAGED[id];
      return `<div class="w-row${s.selected === 'um:' + id ? ' sel' : ''}${s.fresh === 'um:' + id ? ' fresh' : ''}" data-k="um-${id}">` +
        `<i class="dot hollow"></i><span class="nm mono">${esc(u.title)}</span>` +
        (u.lan ? '<span class="tag warn">对局域网开放</span>' : `<span class="mt">:${u.port}</span>`) + '</div>';
    }).join('');
  }
  return h;
}

function fmtUp(sec) {
  sec = Math.max(0, Math.floor(sec));
  const m = Math.floor(sec / 60);
  return m ? `${m} 分钟` : `${sec} 秒`;
}

function detailHtml(s) {
  if (!s.selected) return '<div class="w-empty"><b>选择一个服务</b><span>从左侧列表里选一个服务，查看它的状态、日志和操作。</span></div>';
  if (s.selected.startsWith('um:')) {
    const u = UNMANAGED[s.selected.slice(3)];
    return `<div class="w-dh"><h2>${esc(u.title)}</h2><span class="pill"><i class="dot hollow"></i>未纳入管理</span></div>
      <div class="w-sub">这个程序占用了端口，但不是由 ServiceDeck 启动的。</div>
      <div class="w-acts">${btn(s, 'adopt', '纳入管理', 'pri')}${btn(s, 'stop-um', '停止', 'danger')}</div>
      <dl class="w-info"><dt>端口</dt><dd>${u.port}</dd><dt>访问范围</dt><dd>${u.lan ? '<span class="tag warn">对局域网开放</span>' : '仅本机可以访问'}</dd>
        <dt>程序</dt><dd class="mono sm">${esc(u.exe || '')}</dd><dt>完整命令</dt><dd><code>${esc(u.command || u.title)}</code></dd>
        <dt>工作文件夹</dt><dd>${esc(u.cwd || '')}</dd><dt>PID</dt><dd>${u.pid || ''}</dd><dt>已运行</dt><dd>${u.uptime || ''}</dd></dl>`;
  }
  const id = s.selected, d = SVC[id], st = s.st[id], [color, label] = DISPLAY[st];
  const active = st !== 'stopped';
  let sub = '';
  if (st === 'healthy') sub = `已运行 ${fmtUp(s.uptime[id] ?? 0)} · PID ${d.pid}`;
  else if (st === 'starting') sub = `PID ${d.pid}`;
  else if (!s.manager) sub = '服务管理器未运行';
  const g = GROUPS.find(x => x.id === d.group);
  const info = [['端口', d.port], ['分组', g.name]];
  if (d.deps) info.push(['依赖', d.deps.map(x => SVC[x].name).join('、')]);
  info.push(['命令', `<code>${esc(d.cmd)}</code>`]);
  if (d.cwd) info.push(['文件夹', `<span class="link">${esc(d.cwd)}</span>`]);
  const logs = s.logs[id] || [];
  return `<div class="w-dh"><h2>${esc(d.name)}</h2><span class="pill"><i class="dot ${color}"></i>${label}</span></div>
    <div class="w-sub">${esc(sub)}</div>
    <div class="w-acts">${active ? btn(s, 'stop', '停止') : btn(s, 'start', '启动', 'pri')}${btn(s, 'restart', '重启')}${btn(s, 'edit', '编辑')}
      ${btn(s, 'web', '打开网页', st === 'healthy' ? '' : 'dis')}<span class="sp"></span>${btn(s, 'del', '删除', 'danger')}</div>
    <dl class="w-info">${info.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    <div class="w-lh">最新日志</div>
    <div class="w-log">${logs.length ? logs.slice(-5).map(l => `<div>${esc(l)}</div>`).join('') : '<div class="muted">（还没有日志）</div>'}</div>`;
}

function consoleHtml(s) {
  const sum = summaryOf(s);
  const sumText = s.manager ? `运行中 ${sum.running}` : '服务管理器未运行';
  let h = `<div class="w-top"><div class="w-sum"><i class="dot ${sum.color}"></i>${sumText}</div>
    <div class="w-search">搜索名称、端口、命令</div>
    <div class="w-btns">${btn(s, 'startall', '全部启动')}${btn(s, 'stopall', '全部停止', s.manager ? '' : 'dis')}${btn(s, 'add', '＋ 新增服务', 'pri')}</div></div>`;
  if (!s.manager) h += `<div class="w-banner"><span>服务管理器未运行，服务都没有在运行。</span>${btn(s, 'banner-start', '启动全部服务', 'pri')}</div>`;
  h += `<div class="w-main"><div class="w-list">${listHtml(s)}</div><div class="w-detail">${detailHtml(s)}</div></div>`;
  if (s.modal) h += `<div class="w-overlay">${modalHtml(s)}</div>`;
  if (s.toast) h += `<div class="w-toast">${esc(s.toast)}</div>`;
  return h;
}

/* ---------- 弹窗：选模板 / 新增表单 / 纳入管理 ---------- */
const TEMPLATES = [
  ['python', 'Python 程序', '文件夹里的 Python 程序（自动使用 .venv 或 uv run）'],
  ['node', 'Node 程序', '带 package.json 的 Node 项目，从 scripts 里选一个运行'],
  ['executable', '可执行文件', '直接运行一个程序文件'],
  ['script', 'Shell 脚本', '运行一个 .sh 脚本'],
  ['custom', '自定义命令', '自己写命令（兜底）'],
];

function field(m, key, label, value, { ph = '', mono = false, btnK = null, help = null } = {}) {
  const has = value != null && value !== '';
  const caret = m.focus === key ? '<i class="caret"></i>' : '';
  const cls = ['inp', has ? '' : 'ph', mono ? 'mono' : '', m.flash && m.flash.includes(key) ? 'flash' : '', m.focus === key ? 'focus' : ''].join(' ');
  const inp = `<div class="${cls}" data-k="f-${key}">${has ? esc(value) : esc(ph)}${caret}</div>`;
  return `<div class="fld"><label>${label}</label>${btnK ? `<div class="wb">${inp}<b class="btn${m.press === btnK ? ' press' : ''}" data-k="${btnK}">选择…</b></div>` : inp}` +
    (help ? `<div class="help">${help}</div>` : '') + '</div>';
}

function modalHtml(s) {
  const m = s.modal;
  if (m.kind === 'templates') {
    return `<div class="dlg wide"><div class="dlg-h">新增服务：它是什么程序？</div><div class="dlg-b"><div class="tpl-grid">
      ${TEMPLATES.map(([id, l, d]) => `<div class="tpl${m.hover === id ? ' hov' : ''}${s.press === 'tpl-' + id ? ' press' : ''}" data-k="tpl-${id}"><b>${l}</b><span>${d}</span></div>`).join('')}
      </div></div><div class="dlg-f"><span class="grow"></span>${btn(s, 'cancel', '取消')}</div></div>`;
  }
  if (m.kind === 'form') {
    const trial = m.trial ? `<div class="trial"><div class="trial-h"><b>试运行</b><span class="grow">${m.trial.status}</span>${btn(s, 'trial-stop', '停止试运行')}</div>
      <div class="w-log sm">${m.trial.logs.map(l => `<div>${esc(l)}</div>`).join('') || '<div class="muted">（还没有输出）</div>'}</div></div>` : '';
    return `<div class="dlg wide form"><div class="dlg-h">新增服务 <span class="muted">· Python 程序</span></div><div class="dlg-b">
      <div class="legend">程序</div>
      ${field(m, 'folder', '程序所在文件夹', m.folder, { btnK: 'pick' })}
      ${field(m, 'entry', '入口', m.entry, { help: '入口文件（如 main.py），或模块写法（如 uvicorn app:app --port 8000）' })}
      <div class="fld"><label>将运行的命令 <span class="muted">（自动生成，可以修改）</span></label>
        <div class="inp mono area${m.cmd ? '' : ' ph'}${m.flash && m.flash.includes('cmd') ? ' flash' : ''}" data-k="f-cmd">${esc(m.cmd || '')}</div>
        <div class="help">${m.cmd ? '在文件夹 ~/Projects/my-ledger 中运行' : '&nbsp;'}</div></div>
      <div class="row3">${field(m, 'name', '名称', m.name, { ph: '比如：我的网站' })}${field(m, 'group', '分组', m.group || '公共服务')}${field(m, 'port', '端口', m.port, { ph: '如 8000' })}</div>
      </div>${trial}<div class="dlg-f">${btn(s, 'trial', '试运行 15 秒')}<span class="grow"></span>${btn(s, 'cancel', '取消')}${btn(s, 'save', '保存', 'pri')}</div></div>`;
  }
  if (m.kind === 'adopt') {
    const u = UNMANAGED.jupyter;
    return `<div class="dlg wide form"><div class="dlg-h">纳入管理 <span class="muted">· 自定义命令</span></div><div class="dlg-b">
      <div class="note"><div>环境变量无法自动获取，如果这个程序需要，请在「高级」里补充。</div><div>保存后会先停掉原来手动启动的进程，再由 ServiceDeck 启动，避免端口冲突。</div></div>
      ${field(m, 'command', '命令', u.command, { mono: true })}
      ${field(m, 'folder', '工作文件夹', u.cwd, { btnK: 'pick' })}
      <div class="row3">${field(m, 'name', '名称', m.name, { ph: '比如：我的网站' })}${field(m, 'group', '分组', '我的项目')}${field(m, 'port', '端口', String(u.port))}</div>
      </div><div class="dlg-f">${btn(s, 'trial', '试运行 15 秒')}<span class="grow"></span>${btn(s, 'cancel', '取消')}${btn(s, 'save', '保存', 'pri')}</div></div>`;
  }
  return '';
}

/* ---------- 菜单栏下拉菜单（深色） ---------- */
function menuHtml(s, hl) {
  const sum = summaryOf(s);
  const it = (k, label, extra = '', { dot = null, dis = false, head = false, arrow = false } = {}) =>
    `<div class="mi${dis ? ' dis' : ''}${head ? ' head' : ''}${hl === k ? ' hl' : ''}" data-k="${k}">${dot ? `<i class="dot ${dot}"></i>` : ''}` +
    `<span>${esc(label)}</span>${extra ? `<span class="ex">${esc(extra)}</span>` : ''}${arrow ? '<span class="ar">›</span>' : ''}</div>`;
  let h = it('sum', `运行中 ${sum.running} 个`, '', { dis: true }) + it('open', '打开控制台') + it('startall', '全部启动') + it('stopall', '全部停止');
  for (const g of GROUPS) {
    const ids = g.ids.filter(id => id in s.st);
    const run = ids.filter(id => s.st[id] === 'healthy').length;
    h += '<div class="msep"></div>' + it('g-' + g.id, `${g.name}  ${run}/${ids.length}`, '', { head: true });
    for (const id of ids) {
      const st = s.st[id];
      h += it('m-' + id, SVC[id].name, st === 'healthy' ? `端口 ${SVC[id].port}` : DISPLAY[st][1], { dot: DISPLAY[st][0], arrow: true });
    }
  }
  h += '<div class="msep"></div>' + it('settings', '设置', '', { arrow: true }) + it('quit', '退出 ServiceDeck');
  return h;
}

/* ---------- 道具容器：一个可倾斜、可推镜头的"窗口"，里面带光标 ---------- */
const CURSOR_SVG = '<svg viewBox="0 0 22 30"><path d="M2 2 L2 24 L7.5 18.8 L11.2 27.5 L14.8 26 L11.2 17.5 L18.5 17.5 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';

class Prop {
  /** kind: 'window'（控制台窗口）或 'free'（自由摆放，如菜单栏） */
  constructor(parent, { x, y, w, h, tilt = 0, kind = 'window', title = 'ServiceDeck' }) {
    this.box = place(el('div', 'prop', parent), x, y, w, h);
    this.tilt = tilt;
    if (kind === 'window') {
      this.win = el('div', 'sdw', this.box);
      el('div', 'sdw-title', this.win, `<div class="lights"><i class="r" data-k="close"></i><i class="y"></i><i class="g"></i></div>${esc(title)}`);
      this.body = el('div', 'sdw-body', this.win);
    }
    this.layer = el('div', 'prop-layer', this.box);   // 菜单等浮层
    this.ripple = el('div', 'ripple', this.box);
    this.cursor = el('div', 'cursor', this.box, CURSOR_SVG);
    this.key = null;
    this.cache = {};
    this.state = null;
  }
  setState(s, draw = consoleHtml) {
    this.state = s;
    if (!this.body) return;
    const html = draw(s);
    if (html !== this.key) {
      this.body.innerHTML = html; this.key = html;
      if (this._pulseT != null) this.pulse(this._pulseT);  // 重画后补上闪烁亮度
    }
  }
  /** 黄色圆点呼吸闪烁（按时间） */
  pulse(t) {
    this._pulseT = t;
    const o = 0.55 + 0.45 * Math.cos(t * Math.PI * 2 / 1.2);
    this.box.querySelectorAll('.dot.yellow').forEach(d => { d.style.opacity = o; });
  }
  /** 某个元素（data-k）在"某个状态"下的中心位置（道具内坐标）；按 (状态键, data-k) 缓存 */
  measure(k, stateKey, applyState) {
    const ck = stateKey + '|' + k;
    if (!(ck in this.cache)) {
      const saved = this.state;
      applyState();
      const e = this.box.querySelector(`[data-k="${k}"]`);
      this.cache[ck] = e ? offsetIn(e, this.box) : { x: 0, y: 0 };
      if (saved) this.setState(saved);
    }
    return this.cache[ck];
  }
  /** 光标：pts = [{t, x, y}]（道具内坐标），clicks = [t…] */
  renderCursor(lt, pts, clicks = [], visible = true) {
    if (!visible || !pts.length || lt < pts[0].t - 0.3) { this.cursor.style.visibility = 'hidden'; this.ripple.style.visibility = 'hidden'; return; }
    const posAt = t => {
      let x = pts[0].x, y = pts[0].y;
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i], b = pts[i + 1];
        if (t >= a.t && t <= b.t) { const p = prog(t, a.t, b.t); return { x: lerp(a.x, b.x, p), y: lerp(a.y, b.y, p) }; }
        if (t > b.t) { x = b.x; y = b.y; }
      }
      return { x, y };
    };
    const { x, y } = posAt(lt);
    const appear = prog(lt, pts[0].t - 0.3, pts[0].t, easeOut);
    this.cursor.style.visibility = 'visible';
    this.cursor.style.opacity = appear;
    const down = clicks.some(c => lt >= c && lt < c + 0.12);
    this.cursor.style.transform = `translate(${x - 3}px, ${y - 3}px) scale(${down ? 0.88 : 1})`;
    const last = clicks.filter(c => lt >= c).pop();
    if (last != null && lt - last < 0.5) {
      const p = (lt - last) / 0.5, c = posAt(last);   // 涟漪留在点击的位置
      Object.assign(this.ripple.style, { visibility: 'visible', left: c.x + 'px', top: c.y + 'px', opacity: 1 - p, transform: `translate(-50%, -50%) scale(${0.4 + p})` });
    } else this.ripple.style.visibility = 'hidden';
  }
  /** 镜头：以道具内某点为中心放大 z 倍 */
  camera(z = 1, fx = 0, fy = 0, extra = '') {
    this.box.style.transformOrigin = `${fx}px ${fy}px`;
    this.box.style.transform = `rotate(${this.tilt}deg) scale(${z}) ${extra}`;
  }
}

/** 根据光标关键帧（引用元素 data-k 或直接坐标）算出道具内坐标 */
function cursorPath(prop, keys, stateAt) {
  return keys.map(k => {
    if (k.x != null) return k;
    const at = k.at ?? k.t;
    const p = prop.measure(k.k, `${at}`, () => prop.setState(stateAt(at)));
    return { t: k.t, x: p.x + (k.dx || 0), y: p.y + (k.dy || 0) };
  });
}
