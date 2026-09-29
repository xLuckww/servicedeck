/* 第三幕：快剪 · 数字 · 反转与落版（设计文档 4.2、4.3）。
 * 钢琴和弦（audio.py）：快剪 99 秒起分解和弦；数字段每 3 秒一个和弦（103/106/109/112）；反转 115.3 挂留和弦，118.6 主和弦。 */
'use strict';

/* 结尾数字：渲染成片前按设计文档 4.3 重新核对，只能向下取整 */
const FACTS = {
  days: 1,          // 首次提交 2026-09-28 12:50 → 验收 15:16
  lines: 5700,      // src/ 与 scripts/ 程序文件共 5729 行（2026-09-29 统计）
  mine: 0,          // 负责人确认：一行代码都没写（决策 V8）
  tests: 240,       // 默认运行 244 项（2026-09-29 统计）
};

/* 快剪素材：负责人在对话里的原话（已获负责人同意，2026-09-29）、真实提交记录、设计文档 */
const MONTAGE_QUOTES = [
  '你先阅读并理解一下我这个项目，然后给我这个项目做一个演示动画',
  '我想要做一个横屏的产品动画视频，用于发布到视频平台作为宣传介绍',
  '音乐和音效你可以全部用代码合成，不用任何外部素材',
];
const MONTAGE_COMMITS = [
  '设计阶段：ServiceDeck 设计文档、实施计划与原型',
  '第 0 阶段：项目骨架（mise、pyproject、uv 依赖、最小测试、命令行入口）',
  '第 1 阶段：服务清单（数据模型、读写、校验、备份、对比）',
  '第 2 阶段：运行环境（PATH、工具绝对路径）与五种模板',
  '第 3 阶段：process-compose 配置生成、客户端、启停与热更新',
  '第 5 阶段：发现未纳入管理的服务（真实样本测试 + 集成测试）',
  '第 7 阶段：App 外壳（菜单栏图标、窗口显示/隐藏、单实例、登录启动）',
  '第 8 阶段：控制台窗口界面（列表、详情、新增/编辑表单、试运行）',
  '第 9 阶段：打包安装（install_app.sh、图标、卸载脚本、README）',
];
/* 真实测试输出的最后一行（渲染成片前重新运行 uv run pytest 并替换，不编造耗时） */
const PYTEST_SUMMARY = '244 passed, 12 skipped in 85.92s (0:01:25)';  // 2026-09-29 真实运行结果
const MONTAGE_DECISIONS = [
  ['S3', '新增服务的方式', '模板引导：先选程序类型，再点选文件夹或文件'],
  ['S4', '界面形态', '常驻菜单栏的 App：菜单栏图标和控制台窗口'],
  ['S13', '退出方式', '⌘Q、程序坞"退出"都真正退出 App，服务继续运行'],
  ['S16', '服务管理器的运行方式', '交给 macOS 托管，与 App 没有父子关系'],
];

const sceneMontage = {
  id: 'montage', name: '快剪', dur: 4, fadeIn: 0.15, fadeOut: 0.35,
  bg: () => 0.6,
  build(root) {
    const card = (x, y, w, rot, html, cls = '') => {
      const c = place(el('div', 'mcard ' + cls, root, html), x, y, w);
      c.dataset.rot = rot;
      return c;
    };
    this.cards = [
      card(170, 170, 760, -3, `<div class="bubble">${esc(MONTAGE_QUOTES[0])}</div>`, 'chat'),
      card(1020, 130, 720, 2.5, `<div class="doc-t">ServiceDeck 设计文档</div><table>${MONTAGE_DECISIONS.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table>`, 'doc'),
      card(260, 520, 820, 1.5, `<div class="gl-t">git log</div>${MONTAGE_COMMITS.map(c => `<div class="gl"><span>●</span>${esc(c)}</div>`).join('')}`, 'mterm'),
      card(1100, 560, 700, -2, `<div class="bubble">${esc(MONTAGE_QUOTES[1])}</div>`, 'chat'),
      card(640, 330, 640, 3, `<div class="pytest">$ uv run pytest<br><span class="ok">${esc(PYTEST_SUMMARY)}</span></div>`, 'mterm'),
      card(560, 700, 800, -1.5, `<div class="bubble">${esc(MONTAGE_QUOTES[2])}</div>`, 'chat'),
    ];
  },
  render(lt) {
    this.cards.forEach((c, i) => {
      const t0 = i * 0.5;                       // 每半拍飞入一张
      const p = prog(lt, t0, t0 + 0.3, easeOut);
      const rot = +c.dataset.rot;
      setVis(c, p, `translateY(${(1 - p) * 60}px) rotate(${rot * p}deg) scale(${0.92 + 0.08 * p + 0.02 * (lt - t0) / 4})`);
    });
  },
  sfx(push) { for (let i = 0; i < 6; i++) push(i * 0.5, 'whoosh', 0.18); },
};

/* ================= 数字（1:43–1:55） ================= */
const sceneNumbers = {
  id: 'numbers', name: '数字', dur: 12, fadeIn: 0.3, fadeOut: 0.3,
  bg: () => 0.35,
  build(root) {
    const line = () => { const e = el('div', 'numline', root); e.style.top = '410px'; return e; };
    this.lines = [line(), line(), line(), line()];
    this.lines[0].innerHTML = '<span class="nsm">从想法到每天在用</span><span class="nbig"><b data-n="days">1</b> 天</span>';
    this.lines[1].innerHTML = '<span class="nbig"><b data-n="lines">0</b> 行代码</span><span class="nsm">我写了 <b class="zero">0</b> 行</span>';
    this.lines[2].innerHTML = '<span class="nbig"><b data-n="tests">0</b> 项自动测试</span><span class="nsm">守着它</span>';
    this.lines[3].innerHTML = '<span class="nbig">一个产品经理 <span class="x">×</span> Claude</span>';
    this.nums = [...root.querySelectorAll('[data-n]')];
    this.zero = root.querySelector('.zero');
  },
  render(lt) {
    // 四句分别落在 0/3/6/9 秒（与钢琴和弦同步），每句停留约 2.5 秒
    this.lines.forEach((e, i) => {
      const a = i * 3, p = prog(lt, a, a + 0.45, easeOut), q = i < 3 ? prog(lt, a + 2.6, a + 2.9, easeOut) : 0;
      setVis(e, p * (1 - q), `translateY(${(1 - p) * 24 - q * 18}px)`);
    });
    for (const n of this.nums) {
      const key = n.dataset.n, target = FACTS[key];
      const a = { days: 0, lines: 3, tests: 6 }[key];
      const v = Math.round(target * prog(lt, a + 0.1, a + 1.1, easeOut));
      n.textContent = v.toLocaleString('en-US').replace(/,/g, '');
    }
    this.zero.style.color = mixColor('#F2F4F7', COLORS.green, prog(lt, 4.3, 4.7));
  },
  sfx() {},
};

/* ================= 反转与落版（1:55–2:03） ================= */
const CODE_EXCERPT = `/* ================= 02 一键启动（0:37–0:51） ================= */
const START_GREEN = { mysql: 3.0, redis: 3.5, ollama: 4.5, blog: 6.0, sd: 8.0 };
function startState(lt) {
  const on = lt >= 2;
  const st = {};
  for (const id of ['mysql', 'redis', 'ollama', 'sd', 'blog'])
    st[id] = !on ? 'stopped' : lt >= START_GREEN[id] ? 'healthy' : 'starting';
  return uiState({ manager: on, st, selected: 'blog' });
}
const sceneStart = {
  id: 'start', name: '02 一键启动', dur: 14,
  render(lt) {
    this.prop.setState(startState(lt));
    this.text.render(lt, '按依赖顺序，\\n逐个[亮起]', land(..., 2.0));
  },
};`;
const GITHUB_SVG = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>';

const sceneReveal = {
  id: 'reveal', name: '反转与落版', dur: 8, fadeIn: 0.2, fadeOut: 0.6,
  bg: lt => 0.35 + 0.65 * prog(lt, 3.4, 4.2),
  build(root) {
    // 编辑器窗口：左边是正在播放的"这支视频"，右边是生成它的代码
    this.editor = place(el('div', 'editor', root), 140, 200, 1640, 580);
    el('div', 'editor-bar', this.editor, '<div class="lights"><i></i><i></i><i></i></div>video/index.html — ServiceDeck 宣传片');
    this.preview = el('div', 'editor-preview', this.editor);
    this.code = el('pre', 'editor-code', this.editor, highlightJs(CODE_EXCERPT));
    this.frame = el('div', 'reveal-frame', root);   // 全屏的反转句，稍后缩进左侧预览区
    this.text = new BigText(this.frame, { x: 0, y: 0, size: 96, width: 1920 });
    Object.assign(this.text.root.style, { left: 0, top: '400px', textAlign: 'center' });
    this.end = el('div', 'endcard', root);
    appIcon(this.end, 170);
    this.end.insertAdjacentHTML('beforeend', `<div class="end-name">ServiceDeck</div><div class="end-sub">一个产品经理 × Claude</div>
      <div class="end-gh">${GITHUB_SVG}<span>GitHub：xLuckww</span></div>`);
  },
  render(lt) {
    const text = '对了，你正在看的这支视频，\n也是[我和 AI] 做的';
    this.text.render(lt, text, 0.3);
    // 2.0 秒起：反转句缩小进编辑器左侧的预览区，代码从右侧浮现
    const pull = prog(lt, 2.0, 2.9);
    const ep = prog(lt, 2.0, 2.9, easeOut);
    const endP = prog(lt, 3.6, 4.2, easeOut);
    setVis(this.editor, ep * (1 - endP), `scale(${1.25 - 0.25 * ep})`);
    const s = lerp(1, 0.42, pull);
    this.frame.style.transformOrigin = '0 0';
    this.frame.style.transform = `translate(${lerp(0, 171, pull)}px, ${lerp(0, 277, pull)}px) scale(${s})`;
    this.frame.style.opacity = 1 - endP;
    const cp = prog(lt, 2.5, 3.3, easeOut);
    setVis(this.code, cp, `translateX(${(1 - cp) * 40}px)`);
    // 3.6 秒落版（与 118.6 秒的主和弦同步）
    setVis(this.end, endP, `translateY(${(1 - endP) * 20}px)`);
  },
  sfx() {},
};

/** 极简的 JavaScript 语法着色（只给结尾代码片段用） */
function highlightJs(code) {
  return code.split('\n').map(line => {
    if (/^\s*\/\*/.test(line)) return `<span class="c">${esc(line)}</span>`;
    return esc(line)
      .replace(/(&#39;[^&]*?&#39;)/g, '<span class="s">$1</span>')
      .replace(/\b(const|function|return|for|of|let)\b/g, '<span class="k">$1</span>')
      .replace(/(?<![#\w])(\d+(\.\d+)?)\b/g, '<span class="n">$1</span>');
  }).join('\n');
}
