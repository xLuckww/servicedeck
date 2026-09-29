/* 总时间轴（设计文档 4.1、7.2、8.3）。
 * window.renderAt(t)：把整个画面设置成第 t 秒的样子（预览和渲染共用）。
 * window.audioPlan()：音乐片段与音效事件表，渲染脚本据此混音。 */
'use strict';

const FPS = 60;

/* 音乐：选定曲目后填写。at = 在片中第几秒开始播放；offset = 从曲子第几秒开始取；
 * bpm / firstBeat 用于把关键事件对齐到节拍（beat(n) 换算成片中秒数）。 */
const MUSIC = {
  // 配乐由 video/audio.py 用代码合成，与画面等长（0–15 秒无音乐），所以只有一个片段、从 0 秒开始
  clips: [{ src: 'assets/audio/music.wav', at: 0, offset: 0, dur: 123, fadeIn: 0, fadeOut: 0, vol: 1 }],
  bpm: 120,       // 与 audio.py 的 BPM 一致：一小节 2 秒
  firstBeat: 15,  // 电子乐第一拍在片中第 15 秒
};
const beat = n => (MUSIC.bpm ? MUSIC.firstBeat + n * 60 / MUSIC.bpm : null);

const SFX_FILES = {
  key1: 'assets/audio/sfx/key1.wav', key2: 'assets/audio/sfx/key2.wav', key3: 'assets/audio/sfx/key3.wav',
  error: 'assets/audio/sfx/error.wav', ding: 'assets/audio/sfx/ding.wav', click: 'assets/audio/sfx/click.wav',
  whoosh: 'assets/audio/sfx/whoosh.wav', pop: 'assets/audio/sfx/pop.wav',
  ding1: 'assets/audio/sfx/ding1.wav', ding2: 'assets/audio/sfx/ding2.wav', ding3: 'assets/audio/sfx/ding3.wav',
  ding4: 'assets/audio/sfx/ding4.wav', ding5: 'assets/audio/sfx/ding5.wav', ding6: 'assets/audio/sfx/ding6.wav',
};

/* 按顺序排出每段的开始时间 */
const SCENES = [];
{
  let t = 0;
  for (const d of SCENE_DEFS) { SCENES.push(Object.assign(d, { start: t })); t += d.dur; }
}
const DURATION = SCENES.reduce((s, d) => s + d.dur, 0);

let built = false, backdrop = null;
function buildAll() {
  const stage = document.getElementById('stage');
  stage.style.setProperty('--grain', `url(${grainDataUrl()})`);
  backdrop = new Backdrop(stage, new URLSearchParams(location.search).get('bg') || 'mac');
  for (const s of SCENES) {
    s.root = el('div', 'scene', stage);
    s.root.dataset.scene = s.id;
    s.build(s.root);
  }
  el('div', null, stage).id = 'fade';
  el('div', null, stage).id = 'safe';
  built = true;
}

window.renderAt = function renderAt(t) {
  if (!built) buildAll();
  t = clamp(t, 0, DURATION - 1e-6);
  const cur = SCENES.find(s => t >= s.start && t < s.start + s.dur);
  backdrop.render(t, cur && cur.bg ? cur.bg(t - cur.start) : 1);
  for (const s of SCENES) {
    const lt = t - s.start;
    const active = lt >= 0 && lt < s.dur;
    // 用 display 彻底移出画面：段内元素自己设置过 visibility:visible，只隐藏父元素会留下"残影"
    s.root.style.display = active ? '' : 'none';
    s.root.style.visibility = 'visible';
    if (!active) continue;
    const a = Math.min(s.fadeIn ? prog(lt, 0, s.fadeIn, p => p) : 1, s.fadeOut ? 1 - prog(lt, s.dur - s.fadeOut, s.dur, p => p) : 1);
    s.root.style.opacity = a;
    s.render(lt);
  }
  return t;
};

window.sceneAt = t => SCENES.find(s => t >= s.start && t < s.start + s.dur) || SCENES[SCENES.length - 1];

window.audioPlan = function audioPlan() {
  const sfx = [];
  for (const s of SCENES) s.sfx((lt, name, vol = 0.5) => sfx.push({ at: +(s.start + lt).toFixed(4), src: SFX_FILES[name], name, vol }));
  sfx.sort((a, b) => a.at - b.at);
  return { duration: DURATION, fps: FPS, music: MUSIC.clips, sfx };
};

window.VIDEO = { SCENES, DURATION, FPS, MUSIC };
