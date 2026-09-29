/* 预览播放器：只在预览模式使用（渲染模式 ?render=1 下不启动）。
 * 播放时以音乐的播放位置（没有音乐时以系统时钟）为准推进 t，保证音画同步。 */
'use strict';

(function () {
  const params = new URLSearchParams(location.search);
  // 先等字体加载好再搭建画面：很多位置（菜单栏图标、菜单宽度）是按实际排版量出来的
  const fonts = Promise.all(['400 20px "SD Sans"', '700 20px "SD Sans"', '400 20px "SD Mono"'].map(f => document.fonts.load(f, '字A')))
    .then(list => list.every(x => x.length > 0));
  if (params.has('render')) {
    document.body.classList.add('render');
    window.fontsReady = fonts.then(ok => { window.renderAt(0); return ok; });
    return;
  }
  fonts.then(start);
  function start() {
  const stage = document.getElementById('stage');
  const scrub = document.getElementById('scrub');
  const timeEl = document.getElementById('time');
  const playBtn = document.getElementById('play');
  const segs = document.getElementById('segs');
  const { SCENES, DURATION } = window.VIDEO;
  scrub.max = DURATION;

  function fit() {
    const vp = document.getElementById('viewport');
    const s = Math.min(vp.clientWidth / W, vp.clientHeight / H);
    stage.style.transform = `scale(${s})`;
    stage.style.width = W + 'px';
    stage.parentElement.style.setProperty('--s', s);
    stage.style.marginRight = `${W * s - W}px`;
    stage.style.marginBottom = `${H * s - H}px`;
  }
  addEventListener('resize', fit);
  fit();

  /* 音频：音乐用 <audio> 元素，音效用 WebAudio；文件不存在时静默跳过 */
  const plan = window.audioPlan();
  const musics = plan.music.map(c => { const a = new Audio(c.src); a.preload = 'auto'; return { c, a }; });
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const buffers = {};
  for (const e of plan.sfx) {
    if (e.src && !(e.src in buffers)) {
      buffers[e.src] = null;
      fetch(e.src).then(r => (r.ok ? r.arrayBuffer() : Promise.reject())).then(b => ctx.decodeAudioData(b)).then(buf => { buffers[e.src] = buf; }).catch(() => {});
    }
  }
  function playSfx(e) {
    const buf = buffers[e.src];
    if (!buf) return;
    const src = ctx.createBufferSource(), g = ctx.createGain();
    g.gain.value = e.vol;
    src.buffer = buf; src.connect(g).connect(ctx.destination); src.start();
  }

  let t = 0, playing = false, clockBase = 0, tBase = 0, lastT = 0;
  function syncMusic() {
    for (const { c, a } of musics) {
      const local = t - c.at + c.offset;
      const inside = t >= c.at && t < c.at + c.dur;
      if (playing && inside) {
        if (Math.abs(a.currentTime - local) > 0.08) a.currentTime = local;
        const fi = c.fadeIn ? clamp((t - c.at) / c.fadeIn) : 1, fo = c.fadeOut ? clamp((c.at + c.dur - t) / c.fadeOut) : 1;
        a.volume = clamp(c.vol * Math.min(fi, fo));
        if (a.paused) a.play().catch(() => {});
      } else if (!a.paused) a.pause();
    }
  }
  function show() {
    window.renderAt(t);
    scrub.value = t;
    const f = Math.round(t * 60) % 60;
    timeEl.textContent = `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}.${String(f).padStart(2, '0')} / ${Math.floor(DURATION / 60)}:${String(Math.round(DURATION % 60)).padStart(2, '0')}`;
    const cur = window.sceneAt(t);
    [...segs.children].forEach((b, i) => b.classList.toggle('cur', SCENES[i] === cur));
  }
  function tick() {
    if (!playing) return;
    t = tBase + (performance.now() - clockBase) / 1000;
    if (t >= DURATION) { t = DURATION; setPlaying(false); }
    for (const e of plan.sfx) if (e.at > lastT && e.at <= t) playSfx(e);
    lastT = t;
    syncMusic();
    show();
    requestAnimationFrame(tick);
  }
  function setPlaying(p) {
    playing = p;
    playBtn.textContent = p ? '暂停' : '播放';
    if (p) { ctx.resume(); if (t >= DURATION) t = 0; clockBase = performance.now(); tBase = t; lastT = t; requestAnimationFrame(tick); }
    syncMusic();
  }
  function seek(nt) { t = clamp(nt, 0, DURATION); tBase = t; clockBase = performance.now(); lastT = t; syncMusic(); show(); }

  playBtn.onclick = () => setPlaying(!playing);
  scrub.oninput = () => seek(+scrub.value);
  SCENES.forEach(s => { const b = el('button', null, segs, esc(s.name)); b.onclick = () => seek(s.start); });
  document.getElementById('safe-toggle').onchange = e => document.body.classList.toggle('show-safe', e.target.checked);
  addEventListener('keydown', e => {
    if (e.code === 'Space') { e.preventDefault(); setPlaying(!playing); }
    if (e.key === 'ArrowRight') seek(t + (e.shiftKey ? 1 / 60 : 1));
    if (e.key === 'ArrowLeft') seek(t - (e.shiftKey ? 1 / 60 : 1));
  });
  const startAt = parseFloat(location.hash.slice(1));
  seek(isNaN(startAt) ? 0 : startAt);
  }
})();
