/* 各段画面（设计文档 4.2）。每段：
 *   id / name / dur：段落编号、名称、时长（秒）
 *   fadeIn / fadeOut：段首淡入、段尾淡出（秒，淡到舞台底色）
 *   build(root)：只在开始时调用一次，创建本段需要的元素
 *   render(lt)：lt 为段内时间（秒），把本段画面设置成该时刻的样子；必须只依赖 lt
 *   sfx(push)：登记音效事件 push(段内时间, 音效名, 音量) */
'use strict';

/* ================= 0:00–0:15 痛点 ================= */
const PAIN_TERMS = [
  { x: 840, y: 110, title: 'mysql', prompt: 'me@MacBook ~ % ', cmd: 'mysql.server start',
    out: [{ text: 'Starting MySQL' }, { text: '. SUCCESS!' }] },
  { x: 900, y: 240, title: 'redis', prompt: 'me@MacBook ~ % ', cmd: 'redis-server',
    out: [{ text: 'Redis version=7.4.1, bits=64', cls: 't-dim' }, { text: 'Ready to accept connections tcp' }] },
  { x: 960, y: 370, title: 'ollama', prompt: 'me@MacBook ~ % ', cmd: 'ollama serve',
    out: [{ text: 'level=INFO msg="Listening on 127.0.0.1:11434"', cls: 't-dim' }] },
  { x: 880, y: 500, title: 'stable-diffusion-webui', prompt: 'me@MacBook sd-webui % ', cmd: './webui.sh',
    out: [{ text: 'Launching Web UI with arguments:', cls: 't-dim' }, { text: 'Loading weights [6ce0161689] ...' }] },
  { x: 940, y: 630, title: 'my-blog', prompt: 'me@MacBook my-blog % ', cmd: 'npm run start',
    out: [{ text: '> my-blog@1.0.0 start', cls: 't-dim' }, { text: 'Error: listen EADDRINUSE: address already in use :::4000', cls: 't-err' }] },
];
const PAIN_APPEAR = [0.5, 2.0, 3.4, 4.8, 6.2];
const PAIN_CPS = 20;
const painTimes = i => {
  const a = PAIN_APPEAR[i], typeAt = a + 0.35, typeEnd = typeAt + PAIN_TERMS[i].cmd.length / PAIN_CPS;
  return { a, typeAt, typeEnd, outAt: k => typeEnd + 0.25 + k * (i === 4 && k === 1 ? 0.55 : 0.3) };
};
const PAIN_ERR_AT = painTimes(4).outAt(1);

const scenePain = {
  id: 'pain', name: '痛点', dur: 15, fadeIn: 0.2, fadeOut: 0.4,
  bg: lt => 0.7 * (1 - prog(lt, 13.4, 14.6)),
  build(root) {
    this.cam = el('div', 'cam', root);
    this.terms = PAIN_TERMS.map(d => new Terminal(this.cam, { x: d.x, y: d.y, w: 880, h: 230, title: d.title }));
    this.texts = [
      new BigText(root, { x: 150, y: 380, size: 96, width: 700 }),
      new BigText(root, { x: 150, y: 380, size: 96, width: 700 }),
      new BigText(root, { x: 150, y: 380, size: 96, width: 700, color: 'red' }),
    ];
  },
  render(lt) {
    this.cam.style.transform = `scale(${1 + 0.04 * lt / this.dur})`;
    const dim = 1 - prog(lt, 13.4, 14.6) * 0.85;
    PAIN_TERMS.forEach((d, i) => {
      const tm = painTimes(i), term = this.terms[i];
      const p = prog(lt, tm.a, tm.a + 0.25, easeOut);
      let shake = 0;
      if (i === 4) { const q = clamp((lt - PAIN_ERR_AT) / 0.5); if (q > 0 && q < 1) shake = Math.sin(q * Math.PI * 7) * 14 * (1 - q); }
      setVis(term.root, p * dim, `translateX(${shake}px) scale(${0.96 + 0.04 * p})`);
      const typed = typedCount(lt, tm.typeAt, d.cmd.length, PAIN_CPS);
      const outs = d.out.filter((_, k) => lt >= tm.outAt(k));
      const caret = lt >= tm.a && lt < tm.typeEnd + 0.2 && Math.floor(lt * 2.5) % 2 === 0;
      term.setContent(d.prompt, d.cmd, typed, outs, caret || (lt < tm.typeAt && lt >= tm.a));
    });
    this.texts[0].render(lt, '我的 Mac 上\n跑着 5 个服务', 1.0, 5.0);
    this.texts[1].render(lt, '每一个，\n都靠[敲命令]', 5.5, 9.2);
    this.texts[2].render(lt, '哪个[挂了]？\n不知道', 9.6, 13.6);
  },
  sfx(push) {
    PAIN_TERMS.forEach((d, i) => {
      const tm = painTimes(i);
      for (let c = 0; c < d.cmd.length; c++) push(tm.typeAt + c / PAIN_CPS, 'key' + (1 + Math.floor(rand(i * 50 + c) * 3)), 0.35);
    });
    push(PAIN_ERR_AT, 'error', 0.6);
  },
};

/* ================= 0:15–0:22 产品出场 ================= */
const sceneIntro = {
  id: 'intro', name: '出场', dur: 8, fadeIn: 0, fadeOut: 0.3,
  bg: lt => prog(lt, 0.9, 2.4),
  build(root) {
    this.cam = el('div', 'cam', root);
    this.bar = new MenuBar(this.cam);
    this.barParts = [...this.bar.root.children].filter(c => !c.classList.contains('mb-right'));
    this.rightParts = [...this.bar.root.querySelector('.mb-right').children].filter(c => c !== this.bar.sd);
    const center = el('div', null, root);
    Object.assign(center.style, { position: 'absolute', left: 0, right: 0, top: '300px', display: 'flex', flexDirection: 'column', alignItems: 'center' });
    this.icon = appIcon(center, 220);
    this.name = new BigText(center, { x: 0, y: 0, size: 120, width: 1920 });
    Object.assign(this.name.root.style, { position: 'relative', textAlign: 'center', marginTop: '46px', left: 0, top: 0 });
    this.tag = new BigText(center, { x: 0, y: 0, size: 52, width: 1920 });
    Object.assign(this.tag.root.style, { position: 'relative', textAlign: 'center', marginTop: '18px', left: 0, top: 0, fontWeight: 400 });
    this.dotC = null;
  },
  render(lt) {
    if (!this.dotC) this.dotC = offsetIn(this.bar.dot, this.cam);
    // 镜头：从圆点特写（7 倍）拉开到全景，之后极缓推近
    const pull = prog(lt, 0.7, 2.6);
    const z = Math.pow(7, 1 - pull) * (1 + 0.03 * prog(lt, 2.6, 8, p => p));
    const sx = lerp(W / 2, this.dotC.x, pull), sy = lerp(H / 2, this.dotC.y, pull);  // 圆点在画面上的位置：从正中移到菜单栏
    this.cam.style.transform = `translate(${sx - this.dotC.x * z}px, ${sy - this.dotC.y * z}px) scale(${z})`;
    // 黑暗中只有圆点亮起，随后菜单栏其余部分显现
    const dotOn = prog(lt, 0.05, 0.5, easeOut);
    this.bar.dot.style.opacity = dotOn;
    this.bar.dot.style.boxShadow = `0 0 ${lerp(26, 6, pull)}px ${lerp(6, 1, pull)}px rgba(50, 215, 75, ${0.55 * dotOn})`;
    const rest = prog(lt, 0.9, 2.2);
    this.bar.root.style.background = `rgba(28, 33, 41, ${0.92 * rest})`;
    this.bar.root.style.borderBottomColor = `rgba(255,255,255,${0.06 * rest})`;
    [...this.barParts, ...this.rightParts].forEach(e => { e.style.opacity = rest; });
    this.bar.sd.style.color = `rgba(233, 237, 242, ${rest})`;
    // 图标、名字、口号
    const ip = prog(lt, 2.6, 3.2, easeOut);
    setVis(this.icon, ip, `scale(${0.85 + 0.15 * ip})`);
    this.name.render(lt, 'ServiceDeck', 3.0);
    this.tag.render(lt, '把本机服务，装进[菜单栏]', 3.6);
  },
  sfx(push) { push(0.05, 'ding', 0.5); push(2.6, 'whoosh', 0.35); },
};

/* ================= 尚未制作的段落：占位 ================= */
function placeholder(id, name, dur, note) {
  return {
    id, name, dur, fadeIn: 0.2, fadeOut: 0.2,
    build(root) { this.box = el('div', 'placeholder', root, `${esc(name)}<br><small>${esc(note)}</small>`); },
    render() {},
    sfx() {},
  };
}

const SCENE_DEFS = [
  scenePain,
  sceneIntro,
  sceneSee,
  sceneStart,
  sceneAdd,
  sceneAdopt,
  sceneCalm,
  sceneMontage,
  sceneNumbers,
  sceneReveal,
];
