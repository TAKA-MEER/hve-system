/* ============================================================
   操作画面（docs/plan/spec/Spec-ui.md）
   素の JavaScript。ビルド無し・外部の読み込み無し。ブラウザと Node の
   どちらでも読める書き方にして、DOM に触れない部分を Node の組み込みの
   試験（camera/web/tests/web.test.js）で確かめる。

   ここで守る 4 つ
   1. **押している間だけ動く。**押した瞬間に `hold` を送り、`ui_hold_period_ms`
      ごとに送り続け、離したとき（`pointerup` / `pointercancel` /
      `lostpointercapture` / ページが隠れた / ページを閉じた）に `release` を送る。
   2. **映像はブラウザ側で拡大しない。**ズームは `{"t":"zoom"}` を送るだけで、
      見えている倍率は `state.zoom`（camera/hve_camera/control.py が持つ）だけを使う
      （spec §1.5・protocol §2.1）。
   3. **速度スライダーの位置は保存しない。**画面を開くたびに設定の初期値から
      始める（spec §1）。
   4. **スライダーの値は保存し、ヨーの角度は出さない**（spec §1.4・§2）。
   ============================================================ */

/* --- 画面が使う値（names §5。仮値） --- */
const ui_hold_period_ms = 100;    // `hold` を送り続ける周期
const ui_state_timeout_ms = 1000; // `state` がこの時間届かないと「接続切れ」
const ui_tick_ms = 500;           // 画面が見直す周期
const video_probe_ms = 1500;      // 映像：`load` も `error` も来ないまでの待ち時間
const zoom_max = 4;               // デジタルズームの上限
const zoom_step = 0.5;            // 「＋」「−」の 1 段
const lift_gauge_full_mm = 1800;  // 高さのゲージの満量（上端が未設定のあいだ）

/* 軸 → 速度の設定の項目。`pitch_up` と `pitch_down` は同じ枠（spec §1） */
const AXIS_SETTING = {
  lift_up: 'lift_up', lift_down: 'lift_down',
  pitch_up: 'pitch', pitch_down: 'pitch',
  yaw_left: 'yaw', yaw_right: 'yaw',
};

/* 停止理由の文言（names §3。モックアップの REASON を元にした。**文言は仮**） */
const REASON_TEXT = {
  CEILING_NEAR: ['ng', '天井が近いため上昇できません'],
  CEILING: ['ng', '天井の許可が得られないため上昇できません'],
  CEILING_STALE: ['ng', '天井センサの値が届かないため上昇できません'],
  HEIGHT_UNKNOWN: ['ng', '高さセンサが読めないため上昇できません'],
  LINK_LOST: ['ng', '昇降部と繋がっていません。昇降できません'],
  MAX_RUN: ['warn', '同じ方向へ動き続けた時間が上限（10 秒）を超えたため停止しました。反対方向へは動かせます'],
  TOP: ['warn', '上端に達しました'],
  BOTTOM: ['warn', '下端です'],
  HOLD_TIMEOUT: ['warn', '操作が途絶えたため停止しました'],
  CMD_TIMEOUT: ['warn', '昇降部への操作が途絶えたため停止しました'],
  AXIS_LIMIT: ['warn', '可動範囲の端です'],
};

/* --- DOM に触れない部分（Node の試験で見る） --- */

/** 送る `hold` の JSON（protocol §2.1） */
function holdMessage(axis, speed) {
  return {t: 'hold', axis: axis, speed: Number(speed)};
}

/** 送る `release` の JSON */
function releaseMessage() {
  return {t: 'release'};
}

/** 送る `zoom` の JSON。**ブラウザ側は拡大しない**（spec §1.5） */
function zoomMessage(level) {
  return {t: 'zoom', level: Number(level)};
}

/** 「＋」「−」で次に送る倍率。1〜`zoom_max` に丸め `zoom_step` の倍数にそろえる */
function zoomTarget(current, delta) {
  const from = isFinite(Number(current)) && Number(current) > 0 ? Number(current) : 1;
  const step = Number(delta) > 0 ? Math.abs(Number(delta)) : -Math.abs(Number(delta) || 0);
  const snapped = Math.round((from + step) / zoom_step) * zoom_step;
  return Math.min(zoom_max, Math.max(1, Math.round(snapped * 1000) / 1000));
}

/** 軸 → 送る速度。`pitch_*` は `pitch`・`yaw_*` は `yaw` を見る */
function speedFor(axis, speeds) {
  const key = AXIS_SETTING[axis];
  const value = key ? Number((speeds || {})[key]) : NaN;
  return isFinite(value) ? value : 0;
}

/**
 * スライダーの範囲と初期位置（spec §1）。
 * **範囲は設定の下限〜上限、位置は設定の初期値。**画面を開くたびに初期値から始める
 */
function sliderSpec(setting, step) {
  const values = setting || {};
  return {
    min: Number(values.min),
    max: Number(values.max),
    step: Number(step),
    value: Number(values.init),
  };
}

/** 速度の数値の書式（`%` は整数・`deg/s` は小数 1 桁） */
function formatSpeed(value, unit) {
  const number = Number(value);
  const text = unit === '%' ? String(Math.round(number)) : number.toFixed(1);
  return `${text} ${unit}`;
}

/** 映像（`hve_video` の `/stream`）の URL。**宿主は画面と同じ**（protocol §2.4） */
function streamUrl(protocol, hostname, port) {
  return `${protocol}//${hostname}:${port}/stream`;
}

/** 停止理由 → `{色, 文言}`。帯を出さないものは `null` */
function reasonText(reason) {
  if (!reason || reason === 'NONE' || reason === 'CMD_STOP') return null;
  const known = REASON_TEXT[reason];
  if (known) return {cls: known[0], text: known[1]};
  return {cls: 'warn', text: `止まりました（理由が分かりません: ${reason}）`};
}

/** 天井のバッジの `{色, 文言}`。距離・値なし・範囲外（spec Spec-safety.md §2 #3b） */
function ceilingText(ceiling) {
  const value = ceiling || {};
  if (value.reason === 'OUT_OF_RANGE') return {cls: '', text: '天井 範囲外'};
  if (value.reason === 'CEILING_STALE' || value.mm === null || value.mm === undefined) {
    return {cls: 'ng', text: '天井 値なし'};
  }
  if (value.reason === 'CEILING_NEAR') return {cls: 'warn', text: `天井 ${value.mm} mm`};
  return {cls: '', text: `天井 ${value.mm} mm`};
}

/** 高さの OSD の文言。読めない値と値なしを区別する */
function heightText(lift) {
  const value = lift || {};
  if (value.height_ok === false) return '高さ 読めない';
  if (value.height_mm === null || value.height_mm === undefined) return '高さ —';
  return `高さ ${Math.round(value.height_mm)} mm`;
}

/**
 * その軸のボタンを薄くするか。
 * 昇降部と切れている・天井の `ok` が `false` のときは上昇、下端のときは下降
 */
function holdBlocked(state, axis) {
  const value = state || {};
  const lift = value.lift || {};
  const ceiling = value.ceiling || {};
  if (axis === 'lift_up') return lift.link !== 'ok' || ceiling.ok !== true;
  if (axis === 'lift_down') return lift.bottom === true;
  return false;
}

/** `state` が `ui_state_timeout_ms` 届かないか（「接続切れ」を出す） */
function stateStale(last_at_ms, now_ms, timeout_ms) {
  if (last_at_ms === null || last_at_ms === undefined) return true;
  return now_ms - last_at_ms > Number(timeout_ms);
}

/* --- 画面の状態 --- */

let socket = null;          // カメラ部との WS
let last_state = null;      // 最後に受けた `state`
let last_state_at_ms = null;// 受けた時刻（performance.now()）
let current_settings = null;// GET /api/settings の結果
let current_speeds = null;  // 軸ごとのスライダーの値
let hold_timer = null;      // `hold` を送り続けるタイマー
let hold_axis = null;       // いま押している軸
let video_port = null;      // 映像のポート（`state` から）
let video_probe_timer = null;
let sliders = null;
let outs = null;

const $ = (id) => document.getElementById(id);

function nowMs() {
  return performance.now();
}

/* --- カメラ部とのやりとり --- */

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function connect() {
  if (socket) return;
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  try {
    socket = new WebSocket(`${scheme}//${location.host}/ws`);
  } catch (error) {
    socket = null;
    return;
  }
  socket.addEventListener('message', (event) => {
    let data = null;
    try {
      data = JSON.parse(event.data);
    } catch (error) {
      return;
    }
    if (!data || data.t !== 'state') return;
    last_state = data;
    last_state_at_ms = nowMs();
    render();
  });
  // 切れても繋ぎ直す（切るのは正常なので待たない。protocol §1）
  socket.addEventListener('close', () => {
    socket = null;
  });
}

function loadSettings() {
  fetch('/api/settings')
    .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
    .then((body) => {
      current_settings = body.settings;
      applySettings(body.settings);
    })
    .catch(() => {
      /* 読めなかったときは HTML に書いた既定のまま動かし、次の見直しのときに読み直す */
    });
}

/* --- 速度スライダー --- */

function unitOf(key) {
  const axis = SETTING_AXES.find((item) => item.key === key);
  return axis ? axis.unit : '';
}

/** 範囲＝下限〜上限、位置＝初期値にしてスライダーを作り直す */
function applySettings(settings) {
  if (!sliders) return;
  current_speeds = {};
  for (const axis of SETTING_AXES) {
    const slider = sliders[axis.key];
    if (!slider) continue;
    const spec = sliderSpec((settings || {})[axis.key], axis.step);
    slider.min = String(spec.min);
    slider.max = String(spec.max);
    slider.step = String(spec.step);
    slider.value = String(spec.value);
    current_speeds[axis.key] = spec.value;
    outs[axis.key].textContent = formatSpeed(spec.value, axis.unit);
  }
}

/* --- 押している間だけ動く --- */

function sendHold() {
  if (!hold_axis) return;
  send(holdMessage(hold_axis, speedFor(hold_axis, current_speeds || {})));
}

/** 押した。`hold` を送り続けて、区切りごとの `hold_timeout_ms` を超えても動かしたままにする */
function startHold(axis) {
  if (hold_axis === axis) return;
  endHold();
  hold_axis = axis;
  sendHold();
  hold_timer = setInterval(sendHold, ui_hold_period_ms);
}

/** 離した。`release` を送る（送らないとカメラ部は `hold_timeout_ms` まで動き続ける） */
function endHold() {
  if (hold_timer !== null) {
    clearInterval(hold_timer);
    hold_timer = null;
  }
  if (!hold_axis) return;
  hold_axis = null;
  send(releaseMessage());
}

function bindHoldButtons() {
  document.querySelectorAll('.hold').forEach((button) => {
    const axis = button.dataset.axis;
    button.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      if (button.setPointerCapture) button.setPointerCapture(event.pointerId);
      button.classList.add('on');
      startHold(axis);
    });
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach((name) => {
      button.addEventListener(name, () => {
        button.classList.remove('on');
        endHold();
      });
    });
    button.addEventListener('contextmenu', (event) => event.preventDefault());
  });
}

/* --- 描画 --- */

function setBadge(element, cls, text) {
  element.className = `badge${cls ? ' ' + cls : ''}`;
  element.lastElementChild.textContent = text;
}

function render() {
  const state = last_state;
  if (!state) return;

  // 映像。**カメラ部が切り出して送る**ので、ブラウザ側は大きさを変えないだけ
  if (state.video_port !== undefined && state.video_port !== video_port) {
    video_port = state.video_port;
    $('videoImage').src = streamUrl(location.protocol, location.hostname, video_port);
    $('nosig').classList.add('show');
    if (video_probe_timer !== null) clearTimeout(video_probe_timer);
    video_probe_timer = setTimeout(() => $('nosig').classList.remove('show'), video_probe_ms);
  }

  const lift = state.lift || {};
  const ceiling = ceilingText(state.ceiling);

  setBadge($('bLink'), lift.link === 'ok' ? '' : 'ng', lift.link === 'ok' ? '昇降部' : '昇降部 切断');
  setBadge($('bCeil'), ceiling.cls, ceiling.text);
  $('bClients').lastElementChild.textContent = `端末 ${state.clients}`;
  $('bProv').style.display = state.provisional && state.provisional.length ? '' : 'none';
  $('bTop').style.display = lift.top_mm === null ? '' : 'none';
  $('bFake').style.display = state.fake ? '' : 'none';

  $('osdH').textContent = heightText(lift);
  $('osdP').textContent = `ピッチ ${Number(state.pitch_deg).toFixed(1)}°`;
  $('cP').textContent = `↕ ${Math.round(Number(state.pitch_deg))}°`;

  const reason = reasonText(state.reason);
  const bar = $('stopbar');
  if (reason) {
    bar.className = `stopbar show${reason.cls === 'ng' ? ' ng' : ''}`;
    bar.textContent = reason.text;
  } else {
    bar.className = 'stopbar';
  }

  if (lift.dir === 'up') $('liftState').textContent = `上昇中 ${lift.duty}%`;
  else if (lift.dir === 'down') $('liftState').textContent = `下降中 ${lift.duty}%`;
  else $('liftState').textContent = lift.bottom ? '停止中・下端' : '停止中';

  document.querySelector('[data-axis=lift_up]').classList.toggle('blocked', holdBlocked(state, 'lift_up'));
  document.querySelector('[data-axis=lift_down]').classList.toggle('blocked', holdBlocked(state, 'lift_down'));

  // 高さのゲージ。上端が設定されたら上端の線と満量に使う
  const full = lift.top_mm === null ? lift_gauge_full_mm : lift.top_mm;
  const height = lift.height_ok === false ? 0 : Number(lift.height_mm) || 0;
  $('gFill').style.height = `${Math.min(100, Math.max(0, (height / full) * 100))}%`;
  $('gTop').style.display = lift.top_mm === null ? 'none' : '';
  $('gTop').style.bottom = `${Math.min(100, Math.max(0, (lift.top_mm / full) * 100))}%`;

  // 倍率は `state.zoom` からだけ取る（画面が覚えない。spec §1.5）
  $('zLvl').textContent = `${Number(state.zoom).toFixed(1)}×`;
  $('zIn').disabled = Number(state.zoom) >= zoom_max;
  $('zOut').disabled = Number(state.zoom) <= 1;
}

/** `ui_tick_ms` ごとに見直す。「接続切れ」と再接続だけ */
function tick() {
  const stale = stateStale(last_state_at_ms, nowMs(), ui_state_timeout_ms);
  $('bWs').style.display = stale ? '' : 'none';
  if (current_settings === null) loadSettings();
  if (!socket) connect();
}

/* --- 組み立て --- */

function initApp() {
  sliders = {lift_up: $('sUp'), lift_down: $('sDown'), pitch: $('sPitch'), yaw: $('sYaw')};
  outs = {lift_up: $('oUp'), lift_down: $('oDown'), pitch: $('oPitch'), yaw: $('oYaw')};

  for (const key of Object.keys(sliders)) {
    sliders[key].addEventListener('input', (event) => {
      if (!current_speeds) current_speeds = {};
      current_speeds[key] = Number(event.target.value);
      outs[key].textContent = formatSpeed(event.target.value, unitOf(key));
    });
  }
  bindHoldButtons();

  const zoom = (delta) => {
    const current = last_state ? Number(last_state.zoom) : 1;
    send(zoomMessage(delta === 0 ? 1 : zoomTarget(current, delta)));
  };
  $('zIn').addEventListener('click', () => zoom(zoom_step));
  $('zOut').addEventListener('click', () => zoom(-zoom_step));
  $('zLvl').addEventListener('click', () => zoom(0));

  $('toggleGuide').addEventListener('click', () => {
    const guide = $('guide');
    guide.style.display = guide.style.display === 'none' ? '' : 'none';
  });

  $('openSettings').addEventListener('click', () => {
    openSettingsOverlay(current_settings || {}, (last_state && last_state.provisional) || []);
  });
  // 設定画面が保存できたときにスライダーを初期値から作り直す（spec §1）
  document.addEventListener('hve_settings_saved', (event) => {
    current_settings = event.detail;
    applySettings(event.detail);
  });

  const image = $('videoImage');
  image.addEventListener('error', () => $('nosig').classList.add('show'));
  image.addEventListener('load', () => $('nosig').classList.remove('show'));

  // ページが隠れたとき（タブを切り替えた）と、ページを閉じたときに離す
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) endHold();
  });
  addEventListener('pagehide', endHold);

  loadSettings();
  connect();
  setInterval(tick, ui_tick_ms);
}

if (typeof document !== 'undefined') initApp();

if (typeof module === 'object' && module !== null) {
  module.exports = {
    holdMessage, releaseMessage, zoomMessage, zoomTarget, speedFor, sliderSpec,
    formatSpeed, streamUrl, reasonText, ceilingText, heightText, holdBlocked, stateStale,
  };
}
