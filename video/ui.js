/* 画面组件与动画工具。
 * 铁律：所有动效都由时间 t 计算（设计文档 7.2），组件只提供"给定时间，设置成该时刻的样子"的方法，
 * 不使用 setTimeout、CSS transition/animation。 */
'use strict';

const W = 1920, H = 1080;

/* ---------- 数学与缓动 ---------- */
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, p) => a + (b - a) * p;
const easeOut = p => 1 - Math.pow(1 - p, 3);
const easeInOut = p => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
/** 时间 t 在 [a, b] 区间内的进度（0~1），可选缓动 */
const prog = (t, a, b, ease = easeInOut) => ease(clamp((t - a) / (b - a)));
/** 可复现的伪随机数（同一个 i 永远得到同一个值） */
const rand = i => { const x = Math.sin(i * 12.9898 + 78.233) * 43758.5453; return x - Math.floor(x); };

/* ---------- DOM ---------- */
function el(tag, cls, parent, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  if (parent) parent.appendChild(e);
  return e;
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function place(e, x, y, w, h) {
  Object.assign(e.style, { left: x + 'px', top: y + 'px' });
  if (w != null) e.style.width = w + 'px';
  if (h != null) e.style.height = h + 'px';
  return e;
}
/** 元素在某个祖先元素内的布局坐标（不受 transform 影响） */
function offsetIn(e, ancestor) {
  let x = e.offsetWidth / 2, y = e.offsetHeight / 2;
  while (e && e !== ancestor) { x += e.offsetLeft; y += e.offsetTop; e = e.offsetParent; }
  return { x, y };
}
function setVis(e, opacity, transform = '') {
  e.style.opacity = opacity;
  e.style.transform = transform;
  e.style.visibility = opacity <= 0.001 ? 'hidden' : 'visible';
}

/* ---------- 大字（设计 6.4：全片唯一的文字动效） ----------
 * 文字里用 [方括号] 标出关键词，\n 换行。 */
const CHAR_GAP = 0.03, CHAR_DUR = 0.35;
class BigText {
  constructor(parent, { x, y, size = 88, label = null, color = 'green', width = 900 }) {
    this.root = place(el('div', 'bigtext', parent), x, y, width);
    this.root.style.fontSize = size + 'px';
    this.labelEl = label ? el('div', 'bt-label', this.root, esc(label)) : null;
    this.textEl = el('div', 'bt-text', this.root);
    this.kwColor = color;
    this.chars = [];
    this.key = null;
  }
  setText(text) {
    if (text === this.key) return;
    this.key = text;
    this.textEl.innerHTML = '';
    this.chars = [];
    let kw = false;
    for (const ch of text) {
      if (ch === '[') { kw = true; continue; }
      if (ch === ']') { kw = false; continue; }
      if (ch === '\n') { el('br', null, this.textEl); continue; }
      const s = el('span', 'bt-ch' + (kw ? ' kw' : ''), this.textEl, ch === ' ' ? '&nbsp;' : esc(ch));
      this.chars.push({ s, kw });
    }
  }
  /** 让最后一个字恰好在 tLand 落下时的出场时间 */
  static inFor(text, tLand) {
    const n = [...text].filter(c => !'[]\n'.includes(c)).length;
    return tLand - (n - 1) * CHAR_GAP - CHAR_DUR;
  }
  /** t：当前时间；tIn：开始出场；tOut：开始退场（null 表示不退场） */
  render(t, text, tIn, tOut = null) {
    this.setText(text);
    const out = tOut == null ? 0 : prog(t, tOut, tOut + 0.3, easeOut);
    const visible = t >= tIn - 0.25 && out < 1;
    this.root.style.visibility = visible ? 'visible' : 'hidden';
    if (!visible) return;
    if (this.labelEl) {
      const p = prog(t, tIn - 0.2, tIn + 0.15, easeOut);
      setVis(this.labelEl, p * (1 - out), `translateY(${(1 - p) * 10 - out * 12}px)`);
    }
    const n = this.chars.length;
    const kwAt = tIn + (n - 1) * CHAR_GAP + CHAR_DUR + 0.15;
    const kwP = prog(t, kwAt, kwAt + 0.3);
    this.chars.forEach(({ s, kw }, i) => {
      const p = prog(t, tIn + i * CHAR_GAP, tIn + i * CHAR_GAP + CHAR_DUR, easeOut);
      s.style.opacity = p * (1 - out);
      s.style.transform = `translateY(${(1 - p) * 16 - out * 12}px)`;
      if (kw) s.style.color = mixColor('#F2F4F7', COLORS[this.kwColor], kwP);
    });
  }
}

const COLORS = { green: '#32D74B', yellow: '#FFD60A', red: '#FF453A', grey: '#8E8E93', white: '#F2F4F7', dim: '#8A94A3' };
function mixColor(a, b, p) {
  const pa = [1, 3, 5].map(i => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map(i => parseInt(b.slice(i, i + 2), 16));
  return `rgb(${pa.map((v, i) => Math.round(lerp(v, pb[i], p))).join(',')})`;
}

/* ---------- 终端窗口 ---------- */
class Terminal {
  constructor(parent, { x, y, w = 760, h = 300, title = 'zsh' }) {
    this.root = place(el('div', 'term', parent), x, y, w, h);
    const bar = el('div', 'term-bar', this.root);
    el('div', 'lights', bar, '<i></i><i></i><i></i>');
    el('div', 'term-title', bar, esc(title));
    this.body = el('div', 'term-body', this.root);
    this.key = null;
  }
  /** lines: [{text, cls}]；typed：第一行（命令）已打出的字数 */
  setContent(prompt, cmd, typed, outputs, showCaret) {
    const key = [cmd, typed, outputs.length, showCaret].join('|');
    if (key === this.key) return;
    this.key = key;
    let html = `<div><span class="t-prompt">${esc(prompt)}</span>${esc(cmd.slice(0, typed))}${showCaret ? '<span class="t-caret"></span>' : ''}</div>`;
    html += outputs.map(o => `<div class="${o.cls || ''}">${esc(o.text)}</div>`).join('');
    this.body.innerHTML = html;
  }
}

/** 打字进度：从 t0 开始，每秒 cps 个字 */
const typedCount = (t, t0, len, cps = 22) => Math.max(0, Math.min(len, Math.floor((t - t0) * cps)));

/* ---------- 菜单栏 ---------- */
class MenuBar {
  constructor(parent) {
    this.root = el('div', 'menubar', parent);
    el('div', 'mb-left', this.root, '<b>访达</b><span>文件</span><span>编辑</span><span>显示</span><span>前往</span><span>窗口</span>');
    el('div', 'notch', this.root);
    const right = el('div', 'mb-right', this.root);
    for (let i = 0; i < 4; i++) el('span', 'mb-ico', right, ICONS[['cloud', 'clip', 'bolt', 'eye'][i]]);
    this.sd = el('span', 'sd', right, '<span class="dot"></span>SD');
    this.dot = this.sd.querySelector('.dot');
    el('span', 'mb-ico', right, ICONS.wifi);
    el('span', 'clock', right, '周二 10:24');
  }
}

const ICONS = {
  cloud: '<svg viewBox="0 0 16 16"><path d="M4.5 12.5h7a3 3 0 0 0 .3-6A4 4 0 0 0 4.1 7a2.8 2.8 0 0 0 .4 5.5z"/></svg>',
  clip: '<svg viewBox="0 0 16 16"><rect x="3.5" y="3" width="9" height="11" rx="1.5"/><path d="M6 3V2h4v1M6 7h4M6 10h3"/></svg>',
  bolt: '<svg viewBox="0 0 16 16"><path d="M9 1.5 3.5 9H8l-1 5.5L12.5 7H8z"/></svg>',
  eye: '<svg viewBox="0 0 16 16"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/></svg>',
  wifi: '<svg viewBox="0 0 16 16"><path d="M1.5 6.2a9.5 9.5 0 0 1 13 0M3.8 8.7a6.2 6.2 0 0 1 8.4 0M6 11.1a3 3 0 0 1 4 0"/><circle cx="8" cy="13.2" r=".6"/></svg>',
};

/* ---------- App 图标 ---------- */
function appIcon(parent, size) {
  const e = el('div', 'app-icon', parent, 'SD');
  Object.assign(e.style, { width: size + 'px', height: size + 'px', fontSize: size * 0.36 + 'px', borderRadius: size * 0.225 + 'px' });
  return e;
}

/* ---------- 背景（按时间缓慢变化，全片共用一层，跨段不断） ----------
 * mac：类 macOS 深色壁纸——几团低饱和的柔光缓慢漂移 + 细颗粒 + 暗角（不用绿色，绿色留给状态灯）
 * grid：灰色点阵 + 暗角 */
class Backdrop {
  constructor(parent, variant = 'mac') {
    this.variant = variant;
    this.root = el('div', 'backdrop ' + variant, parent);
    this.blobs = variant === 'mac' ? [
      { c: '58, 92, 140', r: 900, x: 0.18, y: 0.30, ax: 60, ay: 40, sp: 0.05, ph: 0 },
      { c: '84, 72, 130', r: 820, x: 0.82, y: 0.78, ax: 70, ay: 50, sp: 0.04, ph: 2 },
      { c: '40, 110, 130', r: 700, x: 0.62, y: 0.18, ax: 50, ay: 30, sp: 0.06, ph: 4 },
      { c: '130, 96, 80', r: 600, x: 0.30, y: 0.95, ax: 40, ay: 30, sp: 0.05, ph: 1 },
    ].map(b => Object.assign(b, { e: el('div', 'blob', this.root) })) : [];
    el('div', 'grain', this.root);
    el('div', 'vignette', this.root);
    if (variant === 'black') this.root.style.display = 'none';
  }
  render(t, level) {
    this.root.style.opacity = level;
    for (const b of this.blobs) {
      const x = b.x * W + Math.sin(t * b.sp * 2 * Math.PI + b.ph) * b.ax;
      const y = b.y * H + Math.cos(t * b.sp * 2 * Math.PI + b.ph) * b.ay;
      Object.assign(b.e.style, { left: x - b.r + 'px', top: y - b.r + 'px', width: b.r * 2 + 'px', height: b.r * 2 + 'px',
        background: `radial-gradient(closest-side, rgba(${b.c}, 0.34), rgba(${b.c}, 0.12) 55%, rgba(${b.c}, 0) 100%)` });
    }
  }
}

/** 细颗粒纹理：用固定种子生成，保证每次渲染一致 */
function grainDataUrl() {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const g = c.getContext('2d'), img = g.createImageData(256, 256);
  for (let i = 0; i < 256 * 256; i++) { const v = Math.floor(rand(i + 7) * 255); img.data.set([v, v, v, 255], i * 4); }
  g.putImageData(img, 0, 0);
  return c.toDataURL();
}
