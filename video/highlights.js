/* 第二幕：五个亮点（设计文档 4.2）。
 * 每段：stateAt(lt) 给出该时刻的界面状态，cursor 给出光标关键帧（引用元素 data-k），关键时间都落在半拍（0.5 秒）网格上：
 * 段内偶数秒 = 小节线（音乐 120 BPM，每段都从小节线开始）。 */
'use strict';

const TEXT = { x: 150, y: 350, size: 84, width: 640 };
const WIN = { x: 830, y: 150, w: 980, h: 760 };

/** 段落编号（如"01 · 看得清"），整段常驻 */
class SceneLabel {
  constructor(parent, text) { this.e = place(el('div', 'bt-label', parent, esc(text)), TEXT.x, TEXT.y - 70); this.e.style.position = 'absolute'; }
  render(lt, tIn, tOut) {
    const p = prog(lt, tIn, tIn + 0.35, easeOut), q = prog(lt, tOut, tOut + 0.3, easeOut);
    setVis(this.e, p * (1 - q), `translateY(${(1 - p) * 10 - q * 12}px)`);
  }
}
const bigText = (root, over = {}) => new BigText(root, { ...TEXT, ...over });
/** 让大字最后一个字落在 tLand */
const land = (text, tLand) => BigText.inFor(text, tLand);

/** 在 [t0, t1] 区间打字，返回已打出的部分 */
const typed = (lt, t0, text, cps = 20) => [...text].slice(0, typedCount(lt, t0, [...text].length, cps)).join('');

/** 光标在各关键帧之间移动；clicks 是按下的时间点 */
function renderUiCursor(prop, lt, keys, clicks, stateAt, hideAfter = Infinity) {
  if (!prop._path) prop._path = cursorPath(prop, keys, stateAt);
  prop.renderCursor(lt, prop._path, clicks, lt < hideAfter);
}

/* ================= 01 看得清（0:23–0:37） ================= */
const SEE_COLORS = [[2, 'green', '全部正常'], [4, 'yellow', '正在启动'], [6, 'red', '有服务异常'], [8, 'grey', '服务管理器没运行']];
const sceneSee = {
  id: 'see', name: '01 看得清', dur: 14, fadeIn: 0.3, fadeOut: 0.4,
  build(root) {
    this.desk = new Prop(root, { x: 0, y: 0, w: W, h: H, tilt: 0, kind: 'free' });
    this.bar = new MenuBar(this.desk.box);
    this.desk.box.insertBefore(this.bar.root, this.desk.layer);
    this.bar.sd.dataset.k = 'sd';
    this.menu = el('div', 'menu', this.desk.layer);
    this.legend = place(el('div', 'legend-box', root), 1080, 330);
    this.rows = SEE_COLORS.map(([, c, label]) => el('div', 'legend-row', this.legend, `<i class="dot ${c}"></i>${label}`));
    this.label = new SceneLabel(root, '01 · 看得清');
    this.text = bigText(root);
    this.sd = offsetIn(this.bar.sd, this.desk.box);
    this.menu.innerHTML = menuHtml(uiState(), null);
    place(this.menu, Math.min(this.sd.x - 40, W - 360), 50);
  },
  render(lt) {
    // 镜头：推近到菜单栏右侧，SD 图标横向移到画面 x≈1330；菜单栏始终贴着画面顶边（不露出上方的壁纸）
    const p = prog(lt, 0.1, 1.3), z = lerp(1, 1.5, p);
    const tx = lerp(0, 1330 - this.sd.x * 1.5, p), ty = 0;
    this.desk.box.style.transformOrigin = '0 0';
    this.desk.box.style.transform = `translate(${tx}px, ${ty}px) scale(${z})`;
    // 圆点颜色轮换（每个小节线换一次）
    const idx = SEE_COLORS.reduce((a, [t], i) => (lt >= t ? i : a), 0);
    const all = lt >= 10;
    const color = all || lt < 2 ? 'green' : SEE_COLORS[idx][1];
    this.bar.dot.style.background = COLORS[color];
    this.bar.dot.style.opacity = color === 'yellow' ? 0.55 + 0.45 * Math.cos(lt * Math.PI * 2 / 1.2) : 1;
    const lp = prog(lt, 1.5, 1.9, easeOut) * (1 - prog(lt, 10.3, 10.7));
    setVis(this.legend, lp, `translateY(${(1 - prog(lt, 1.5, 1.9, easeOut)) * 14}px)`);
    this.rows.forEach((r, i) => {
      const on = all || (lt >= 2 && idx === i);
      r.style.opacity = on ? 1 : 0.28;
      const d = r.firstElementChild;
      d.style.opacity = on && SEE_COLORS[i][1] === 'yellow' ? 0.55 + 0.45 * Math.cos(lt * Math.PI * 2 / 1.2) : 1;
    });
    // 11 秒点开菜单，光标往下划到各个服务
    const open = lt >= 11;
    this.bar.sd.classList.toggle('open', open);
    this.bar.sd.style.background = open ? 'rgba(255,255,255,.2)' : '';
    const hl = lt >= 12.4 ? 'm-sd' : lt >= 11.8 ? 'm-mysql' : null;
    if (this._hl !== hl) { this.menu.innerHTML = menuHtml(uiState(), hl); this._hl = hl; }
    setVis(this.menu, open ? prog(lt, 11, 11.15, easeOut) : 0, open ? `translateY(${(1 - prog(lt, 11, 11.15, easeOut)) * -6}px)` : '');
    renderUiCursor(this.desk, lt, [
      { t: 10.4, x: this.sd.x + 120, y: this.sd.y + 160 },
      { t: 10.9, x: this.sd.x + 6, y: this.sd.y + 4 },
      { t: 11.8, k: 'm-mysql', at: 11.8, dx: -40 },
      { t: 12.4, k: 'm-sd', at: 12.4, dx: -80 },
    ], [11], () => uiState());
    this.label.render(lt, 1.2, 13.3);
    this.text.render(lt, '一个[圆点]，\n看清所有服务', land('一个[圆点]，\n看清所有服务', 2.0), 13.3);
  },
  sfx(push) {
    ['ding3', 'ding4', 'ding2', 'ding1', 'ding6'].forEach((d, i) => push(2 + i * 2, d, 0.45));
    push(11, 'click', 0.5);
  },
};

/* ================= 02 一键启动（0:37–0:51） ================= */
const START_GREEN = { mysql: 3.0, redis: 3.5, ollama: 4.5, blog: 6.0, sd: 8.0 };
const BLOG_LOGS = [[3.6, '> my-blog@1.0.0 start'], [4.2, '> node server.js'], [5.0, 'Connected to MySQL at 127.0.0.1:3306'],
  [5.5, 'Connected to Redis at 127.0.0.1:6379'], [6.0, 'Blog listening on http://localhost:4000']];
function startState(lt) {
  const on = lt >= 2;
  const st = {};
  for (const id of ['mysql', 'redis', 'ollama', 'sd', 'blog']) st[id] = !on ? 'stopped' : lt >= START_GREEN[id] ? 'healthy' : 'starting';
  return uiState({
    manager: on, st, selected: 'blog',
    uptime: { blog: Math.max(0, lt - START_GREEN.blog) },
    logs: { blog: BLOG_LOGS.filter(([t]) => lt >= t).map(([, l]) => l) },
    press: lt >= 2 && lt < 2.12 ? 'banner-start' : null,
  });
}
const sceneStart = {
  id: 'start', name: '02 一键启动', dur: 14, fadeIn: 0.3, fadeOut: 0.4,
  build(root) {
    this.prop = new Prop(root, WIN);
    this.label = new SceneLabel(root, '02 · 一键启动');
    this.text = bigText(root);
  },
  render(lt) {
    this.prop.setState(startState(lt));
    this.prop.pulse(lt);
    const inP = prog(lt, 0, 0.7, easeOut);
    this.prop.camera(1 + 0.03 * lt / this.dur, 490, 380, `translateX(${(1 - inP) * 60}px)`);
    this.prop.box.style.opacity = inP;
    renderUiCursor(this.prop, lt, [
      { t: 0.8, x: 560, y: 520 },
      { t: 1.8, k: 'banner-start', at: 1.0 },
      { t: 2.9, x: 520, y: 300 },
    ], [2], startState, 7);
    this.label.render(lt, 0.4, 13.3);
    this.text.render(lt, '按依赖顺序，\n逐个[亮起]', land('按依赖顺序，\n逐个[亮起]', 2.0), 13.3);
  },
  sfx(push) {
    push(2, 'click', 0.5);
    ['mysql', 'redis', 'ollama', 'blog', 'sd'].forEach((id, i) => push(START_GREEN[id], 'ding' + (i + 1), 0.4));
  },
};

/* ================= 03 自己能加（0:51–1:11） ================= */
const LEDGER_CMD = SVC.ledger.cmd;
const UVICORN_LOGS = ['INFO:     Started server process [2231]', 'INFO:     Waiting for application startup.',
  'INFO:     Application startup complete.', 'INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)'];
const ENTRY = 'uvicorn main:app --port 8000';
function addState(lt) {
  const base = { selected: 'blog', uptime: { blog: 40 + lt, ledger: Math.max(0, lt - 16) }, logs: { blog: BLOG_LOGS.map(([, l]) => l) } };
  const pressAt = (k, t) => (lt >= t && lt < t + 0.12 ? k : null);
  const press = pressAt('add', 1.0) || pressAt('tpl-python', 2.5) || pressAt('pick', 3.5) || pressAt('trial', 9.5) || pressAt('save', 14.0);
  if (lt < 1.1 || lt >= 14.0) {
    const s = uiState({ ...base, press });
    if (lt >= 14.0) {
      s.st = { ...s.st, ledger: lt >= 16 ? 'healthy' : 'starting' };
      s.selected = 'ledger';
      s.fresh = lt < 15.5 ? 'ledger' : null;
      s.toast = lt < 16.2 ? '已新增「我的记账本」' : null;
      s.logs = { ...s.logs, ledger: UVICORN_LOGS.filter((_, i) => lt >= 14.4 + i * 0.5) };
    }
    return s;
  }
  if (lt < 2.6) return uiState({ ...base, press, modal: { kind: 'templates', hover: lt >= 2.2 ? 'python' : lt >= 1.7 ? 'node' : null } });
  const m = { kind: 'form', flash: [] };
  if (lt >= 3.5) { m.folder = '~/Projects/my-ledger'; m.name = 'my-ledger'; if (lt < 4.3) m.flash.push('folder', 'name'); }
  if (lt >= 4.1) m.entry = typed(lt, 4.3, ENTRY, 21);
  if (lt >= 4.1 && lt < 5.9) m.focus = 'entry';
  if (lt >= 5.9) { m.cmd = LEDGER_CMD; m.port = '8000'; if (lt < 6.8) m.flash.push('cmd', 'port'); }
  if (lt >= 7.7) { m.name = typed(lt, 7.9, '我的记账本', 8) || ''; if (lt < 8.6) m.focus = 'name'; }
  if (lt >= 9.0) { m.group = '我的项目'; if (lt < 9.6) m.flash.push('group'); }
  if (lt >= 9.5) {
    const logs = UVICORN_LOGS.filter((_, i) => lt >= 10 + i * 0.5);
    const remain = 15 - Math.floor(lt - 9.5);
    const port = lt >= 12 ? '<span class="ok-text">端口 8000 已起来 ✓</span>' : '端口 8000 还没起来';
    m.trial = { logs, status: `${lt >= 10 ? '运行中' : '启动中'}，还剩 ${remain} 秒 · ${port}` };
  }
  return uiState({ ...base, press, modal: m });
}
const sceneAdd = {
  id: 'add', name: '03 自己能加', dur: 20, fadeIn: 0.3, fadeOut: 0.4,
  build(root) {
    this.prop = new Prop(root, WIN);
    this.label = new SceneLabel(root, '03 · 自己能加');
    this.t1 = bigText(root); this.t2 = bigText(root); this.t3 = bigText(root);
  },
  render(lt) {
    this.prop.setState(addState(lt));
    this.prop.pulse(lt);
    // 镜头：5.9 秒推近到自动生成的命令，12 秒推近到"端口已起来 ✓"
    const cmdP = prog(lt, 6.0, 6.6) * (1 - prog(lt, 7.2, 7.8));
    const okP = prog(lt, 12.0, 12.6) * (1 - prog(lt, 13.2, 13.7));
    if (!this.focus) {
      this.focus = {
        cmd: this.prop.measure('f-cmd', 'cmd', () => this.prop.setState(addState(6.5))),
        ok: this.prop.measure('trial-stop', 'ok', () => this.prop.setState(addState(12.5))),
      };
      this.prop.setState(addState(lt));
    }
    const f = cmdP > 0 ? this.focus.cmd : okP > 0 ? { x: this.focus.ok.x - 260, y: this.focus.ok.y } : { x: 490, y: 380 };
    this.prop.camera(1 + 0.22 * Math.max(cmdP, okP), f.x, f.y);
    renderUiCursor(this.prop, lt, [
      { t: 0.3, x: 520, y: 460 }, { t: 0.9, k: 'add', at: 0.5 },
      { t: 1.7, k: 'tpl-node', at: 1.5 }, { t: 2.3, k: 'tpl-python', at: 1.5 },
      { t: 3.3, k: 'pick', at: 3.0 },
      { t: 4.0, k: 'f-entry', at: 4.2, dx: -150 },
      { t: 7.6, k: 'f-name', at: 7.8, dx: 40 },
      { t: 8.7, k: 'f-group', at: 8.8 },
      { t: 9.4, k: 'trial', at: 9.0 },
      { t: 13.8, k: 'save', at: 13.0 },
      { t: 15.0, x: 600, y: 330 },
    ], [1.0, 2.5, 3.5, 4.1, 7.7, 8.8, 9.5, 14.0], addState, 15.5);
    this.label.render(lt, 0.3, 19.4);
    this.t1.render(lt, '选个文件夹，\n命令[自动生成]', land('选个文件夹，\n命令[自动生成]', 2.0), 8.6);
    this.t2.render(lt, '先[试运行]，\n再保存', land('先[试运行]，\n再保存', 10.0), 14.3);
    this.t3.render(lt, '不写[一行代码]', land('不写[一行代码]', 16.0), null);
  },
  sfx(push) {
    for (const t of [1.0, 2.5, 3.5, 4.1, 7.7, 8.8, 9.5, 14.0]) push(t, 'click', 0.45);
    for (let i = 0; i < ENTRY.length; i++) push(4.3 + i / 21, 'key' + (1 + (i % 3)), 0.25);
    for (let i = 0; i < 5; i++) push(7.9 + i / 8, 'key' + (1 + (i % 3)), 0.25);
    push(5.9, 'pop', 0.4);
    push(12.0, 'ding4', 0.45);
    push(16.0, 'ding6', 0.45);
  },
};

/* ================= 04 不漏网（1:11–1:25） ================= */
const ADOPT_CMDS = [
  { t: 0.8, cmd: 'python3 -m http.server 8080', out: ['Serving HTTP on :: port 8080 ...'], row: 'http', rowAt: 2.5 },
  { t: 3.0, cmd: 'npm run dev', out: ['➜  Local: http://localhost:5173/'], row: 'vite', rowAt: 4.0 },
  { t: 4.4, cmd: 'jupyter notebook', out: ['Jupyter Server at localhost:8888'], row: 'jupyter', rowAt: 5.5 },
];
const ADOPT_CPS = 20;
function adoptState(lt) {
  const s = uiState({
    st: { mysql: 'healthy', redis: 'healthy', ollama: 'healthy', sd: 'healthy', blog: 'healthy', ledger: 'healthy' },
    selected: 'ledger', uptime: { ledger: 5 + lt }, logs: { ledger: UVICORN_LOGS },
    unmanaged: ADOPT_CMDS.filter(c => lt >= c.rowAt).map(c => c.row),
  });
  const newest = ADOPT_CMDS.filter(c => lt >= c.rowAt && lt < c.rowAt + 1).pop();
  s.fresh = newest ? 'um:' + newest.row : null;
  if (lt >= 7.3) s.selected = 'um:jupyter';
  s.press = [['um-jupyter', 7.3], ['adopt', 8.5], ['cancel', 12.3]].map(([k, t]) => (lt >= t && lt < t + 0.12 ? k : null)).find(Boolean) || null;
  if (lt >= 8.5 && lt < 12.4) s.modal = { kind: 'adopt', flash: lt < 9.6 ? ['command', 'folder', 'port'] : [] };
  return s;
}
const sceneAdopt = {
  id: 'adopt', name: '04 不漏网', dur: 14, fadeIn: 0.3, fadeOut: 0.4,
  build(root) {
    this.prop = new Prop(root, WIN);
    this.term = new Terminal(root, { x: 150, y: 600, w: 640, h: 300, title: 'zsh' });
    this.label = new SceneLabel(root, '04 · 不漏网');
    this.t1 = bigText(root); this.t2 = bigText(root);
  },
  render(lt) {
    this.prop.setState(adoptState(lt));
    const modalP = prog(lt, 9.0, 9.6) * (1 - prog(lt, 11.2, 11.8));
    if (!this.mf) { this.mf = this.prop.measure('f-command', 'm', () => this.prop.setState(adoptState(9.2))); this.prop.setState(adoptState(lt)); }
    this.prop.camera(1 + 0.18 * modalP, this.mf.x, this.mf.y + 60);
    // 终端：三条命令依次敲下
    const tp = prog(lt, 0.3, 0.6, easeOut) * (1 - prog(lt, 6.2, 6.7));
    setVis(this.term.root, tp, `translateY(${(1 - prog(lt, 0.3, 0.6, easeOut)) * 30 + prog(lt, 6.2, 6.7) * 30}px)`);
    const key = ADOPT_CMDS.map(c => typedCount(lt, c.t, c.cmd.length, ADOPT_CPS) + ':' + c.out.filter((_, i) => lt >= c.t + c.cmd.length / ADOPT_CPS + 0.2 + i * 0.2).length).join('|');
    if (key !== this._tk) {
      this._tk = key;
      let h = '';
      for (const c of ADOPT_CMDS) {
        if (lt < c.t - 0.3) break;
        const n = typedCount(lt, c.t, c.cmd.length, ADOPT_CPS);
        h += `<div><span class="t-prompt">me@MacBook ~ % </span>${esc(c.cmd.slice(0, n))}</div>`;
        c.out.forEach((o, i) => { if (lt >= c.t + c.cmd.length / ADOPT_CPS + 0.2 + i * 0.2) h += `<div class="t-dim">${esc(o)}</div>`; });
      }
      this.term.body.innerHTML = h;
    }
    renderUiCursor(this.prop, lt, [
      { t: 6.6, x: 560, y: 600 }, { t: 7.2, k: 'um-jupyter', at: 6.0 },
      { t: 8.3, k: 'adopt', at: 7.5 }, { t: 12.1, k: 'cancel', at: 10.0 }, { t: 13.0, x: 620, y: 420 },
    ], [7.3, 8.5, 12.3], adoptState);
    this.label.render(lt, 0.3, 13.3);
    this.t1.render(lt, '终端里随手跑的\n也[逃不掉]', land('终端里随手跑的\n也[逃不掉]', 2.5), 6.8);
    this.t2.render(lt, '一键[纳入管理]', land('一键[纳入管理]', 8.5), 13.3);
  },
  sfx(push) {
    ADOPT_CMDS.forEach(c => {
      for (let i = 0; i < c.cmd.length; i++) push(c.t + i / ADOPT_CPS, 'key' + (1 + (i % 3)), 0.25);
      push(c.rowAt, 'pop', 0.45);
    });
    for (const t of [7.3, 8.5, 12.3]) push(t, 'click', 0.45);
  },
};

/* ================= 05 放得下心（1:25–1:39） ================= */
const ARCH_SVCS = ['mysql', 'redis', 'ollama', 'sd', 'blog', 'ledger'];
function calmState() {
  return uiState({ st: { mysql: 'healthy', redis: 'healthy', ollama: 'healthy', sd: 'healthy', blog: 'healthy', ledger: 'healthy' },
    selected: 'ledger', uptime: { ledger: 20 }, logs: { ledger: UVICORN_LOGS } });
}
const sceneCalm = {
  id: 'calm', name: '05 放得下心', dur: 14, fadeIn: 0.3, fadeOut: 0.8,
  build(root) {
    this.desk = new Prop(root, { x: 0, y: 0, w: W, h: H, tilt: 0, kind: 'free' });
    this.bar = new MenuBar(this.desk.box);
    this.desk.box.insertBefore(this.bar.root, this.desk.layer);
    this.bar.sd.dataset.k = 'sd';
    this.win = new Prop(this.desk.box, { ...WIN, y: 170, tilt: 0 });
    this.desk.box.insertBefore(this.win.box, this.desk.layer);
    this.win.cursor.remove(); this.win.ripple.remove();
    this.win.setState(calmState());
    this.menu = el('div', 'menu', this.desk.layer);
    this.menu.innerHTML = menuHtml(calmState(), null);
    this.sd = offsetIn(this.bar.sd, this.desk.box);
    place(this.menu, Math.min(this.sd.x - 40, W - this.menu.offsetWidth - 24), 50);
    // 结构示意（屏幕坐标）
    this.arch = el('div', null, root);
    Object.assign(this.arch.style, { position: 'absolute', inset: 0 });
    const cols = [860, 1180, 1500], rows = [690, 800];
    const svcHtml = ARCH_SVCS.map((id, i) => `<div class="arch-svc" style="left:${cols[i % 3]}px;top:${rows[Math.floor(i / 3)]}px;width:300px">` +
      `<b><i class="dot"></i>${esc(SVC[id].name)}</b><span>:${SVC[id].port} · 运行中</span></div>`).join('');
    const lines = ARCH_SVCS.map((_, i) => { const x = cols[i % 3] + 150, y = rows[Math.floor(i / 3)]; return `<path d="M1310 555 V${Math.min(y, 690) - 30} H${x} V${y}"/>`; }).join('');
    this.arch.innerHTML = `<svg class="arch-lines" viewBox="0 0 1920 1080"><path class="cut" d="M1310 300 V430"/><text x="1330" y="375">已断开</text>${lines}</svg>` +
      `<div class="arch-node off" style="left:1050px;top:190px;width:520px"><b>ServiceDeck App</b><span>菜单栏图标 + 控制台窗口 · 已退出</span></div>` +
      `<div class="arch-node" style="left:960px;top:430px;width:700px"><b>服务管理器</b><span>在后台常驻，真正负责运行服务</span></div>` + svcHtml;
    this.archApp = this.arch.querySelector('.arch-node.off');
    this.archDots = [...this.arch.querySelectorAll('.arch-svc .dot')];
    this.label = new SceneLabel(root, '05 · 放得下心');
    this.text = bigText(root);
  },
  render(lt) {
    // 1.5 秒关窗口；2.5 秒点开菜单；3.5 秒退出，菜单栏图标消失
    const close = prog(lt, 1.5, 1.8, easeOut);
    setVis(this.win.box, 1 - close, `scale(${1 - 0.08 * close})`);
    const menuOn = lt >= 2.5 && lt < 3.5;
    const hl = lt >= 3.3 ? 'quit' : null;
    if (this._hl !== hl) { this.menu.innerHTML = menuHtml(calmState(), hl); this._hl = hl; }
    setVis(this.menu, menuOn ? prog(lt, 2.5, 2.65, easeOut) : 0);
    this.bar.sd.style.background = menuOn ? 'rgba(255,255,255,.2)' : '';
    this.bar.sd.style.opacity = 1 - prog(lt, 3.6, 4.2);
    const deskOut = prog(lt, 4.4, 5.0);
    this.desk.box.style.opacity = 1 - 0.75 * deskOut;
    renderUiCursor(this.desk, lt, [
      { t: 0.5, x: 1300, y: 600 }, { t: 1.3, k: 'close', at: 0 },
      { t: 2.3, k: 'sd', at: 0, dx: 4 },
      { t: 3.3, k: 'quit', at: 3.3, dx: -60 },
    ], [1.5, 2.5, 3.5], calmState, 4.3);
    // 结构示意：App 已退出（虚线、变暗），服务的绿灯照常亮着
    const ap = prog(lt, 4.6, 5.4, easeOut);
    setVis(this.arch, ap * (1 - prog(lt, 13.0, 13.4)), `translateY(${(1 - ap) * 20}px)`);
    this.archApp.style.opacity = 0.45;
    this.archDots.forEach((d, i) => { d.style.opacity = 0.6 + 0.4 * Math.cos((lt - i * 0.15) * Math.PI * 2 / 1.6); });
    this.label.render(lt, 0.3, 13.0);
    this.text.render(lt, '关掉 App，\n服务[照常运行]', land('关掉 App，\n服务[照常运行]', 6.0), 13.0);
  },
  sfx(push) {
    for (const t of [1.5, 2.5, 3.5]) push(t, 'click', 0.45);
    push(1.5, 'whoosh', 0.3);
    push(6.0, 'ding6', 0.4);
  },
};
