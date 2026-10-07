/* ============================================================
   昇降部の画面（docs/plan/detailed/DetailedDesign.md §4.2）
   カメラモジュールの画面（camera/web）から映像・ヨー・ピッチ・ズームを
   除いたもの（docs/plan/spec/Spec-ui.md §0.2）。素の JavaScript。
   ビルド無し・外部の読み込み無し。ブラウザと Node のどちらでも読める
   書き方にして、DOM に触れない部分を Node の組み込みの試験
   （firmware/lift/web/tests/web.test.js）で確かめる。

   ここで守る 3 つ
   1. **押している間だけ動く。**押した瞬間に `hold` を送り、
      `ui_hold_period_ms` ごとに送り続け、離したとき（`pointerup` /
      `pointercancel` / `lostpointercapture` / ページが隠れた /
      ページを閉じた）に `release` を送る。送らないと昇降部は
      `LIFT_CMD_TIMEOUT_MS` まで動き続ける。
   2. **`press` は押し始めごとに 1 増やす。**同じボタンを押し続けている
      あいだは増やさない。止まったあとに遅れて届いた古い `hold` で
      動き出さないため（DetailedDesign.md §3.3）。
   3. **速度スライダーの位置は保存しない。**画面を開くたびに設定の初期値から
      始める（spec §1）。昇降部は設定の下限〜上限に丸めないので、
      スライダーの範囲＝下限〜上限にする。
   ============================================================ */

/* --- 画面が使う値（DetailedDesign-names.md §5.2。仮値） --- */
const ui_hold_period_ms = 100;    // `hold` を送り続ける周期
const ui_state_timeout_ms = 1000; // `state` がこの時間届かないと「接続切れ」
const ui_tick_ms = 500;           // 画面が見直す周期
const lift_gauge_full_mm = 1800;  // 高さのゲージの満量

/* 設定できる項目。名前・単位・絶対的な範囲（protocol §3 の検証に対応） */
const SETTING_AXES = [
  {key: 'lift_up', name: '上昇', unit: '%', abs_low: 0, abs_high: 100, step: 1},
  {key: 'lift_down', name: '下降', unit: '%', abs_low: 0, abs_high: 100, step: 1},
];

/* 停止理由の文言（names §3。**文言は仮**） */
const REASON_TEXT = {
  CEILING_NEAR: ['ng', '天井が近いため上昇できません'],
  CEILING_STALE: ['ng', '天井の値が届かないため上昇できません'],
  HEIGHT_UNKNOWN: ['ng', '高さが読めないため上昇できません'],
  TOP: ['warn', '上端に達しました'],
  BOTTOM: ['warn', '下端です'],
  MAX_RUN: ['warn', '同じ方向へ動き続けた時間が上限を超えたため停止しました。反対方向へは動かせます'],
  CMD_TIMEOUT: ['warn', '操作が途絶えたため停止しました'],
  OWNER_GONE: ['warn', '操作していた画面が閉じられたため停止しました'],
};

/* --- DOM に触れない部分（Node の試験で見る） --- */

/** 送る `hold` の JSON（protocol §2.1）。画面（`/ws/ui`）の `hold` に天井は載せない */
function holdMessage(press, dir, duty) {
  return {t: 'hold', press: Number(press), dir: dir, duty: Number(duty)};
}

/** 送る `release` の JSON */
function releaseMessage(press) {
  return {t: 'release', press: Number(press)};
}

/**
 * 押し始めか。止まっている向きと違う向き・`up` と `down` の入れ替えのときだけ真。
 * 同じ向きを押し続けているあいだは偽（`press` を増やさない。§3.3）
 */
function isNewPress(prevDir, newDir) {
  if (newDir !== 'up' && newDir !== 'down') return false;
  return prevDir !== newDir;
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

/** 速度の数値の書式（昇降は PWM デューティ比 [%] の整数） */
function formatSpeed(value) {
  return `${Math.round(Number(value))} %`;
}

/** 停止理由 → `{色, 文言}`。帯を出さないものは `null` */
function reasonText(reason) {
  if (!reason || reason === 'NONE' || reason === 'CMD_STOP') return null;
  const known = REASON_TEXT[reason];
  if (known) return {cls: known[0], text: known[1]};
  return {cls: 'warn', text: `止まりました（理由が分かりません: ${reason}）`};
}

/** 天井のバッジの `{色, 文言}`。持ち主がいないとき（`null`）は値なしではなく「—」 */
function ceilingText(ceiling) {
  if (ceiling === null || ceiling === undefined) return {cls: '', text: '天井 —'};
  const value = ceiling || {};
  if (value.reason === 'OUT_OF_RANGE') return {cls: '', text: '天井 範囲外'};
  if (value.reason === 'CEILING_STALE' || value.status === 'MISSING'
      || value.mm === null || value.mm === undefined) {
    return {cls: 'ng', text: '天井 値なし'};
  }
  if (value.reason === 'CEILING_NEAR') return {cls: 'warn', text: `天井 ${value.mm} mm`};
  return {cls: '', text: `天井 ${value.mm} mm`};
}

/** 高さの文言。読めない値と値なしを区別する */
function heightText(height_mm, height_ok) {
  if (height_ok === false) return '高さ 読めない';
  if (height_mm === null || height_mm === undefined) return '高さ —';
  return `高さ ${Math.round(height_mm)} mm`;
}

/** 持ち主の文言（spec §1.6。最後の操作が勝つことを気づけるように出す） */
function ownerText(owner) {
  if (owner === 'ui') return 'この画面';
  if (owner === 'module') return '上部モジュール';
  return 'なし';
}

/** `state` が `ui_state_timeout_ms` 届かないか（「接続切れ」を出す） */
function stateStale(last_at_ms, now_ms, timeout_ms) {
  if (last_at_ms === null || last_at_ms === undefined) return true;
  return now_ms - last_at_ms > Number(timeout_ms);
}

/**
 * 設定の下書きの検証。`min ≦ init ≦ max` と絶対的な範囲を見る（protocol §3）。
 * @param {object} draft 2 項目の {min, max, init}
 * @returns {{ok: boolean, errors: Array<{key: string, text: string}>}}
 */
function validateSettingsDraft(draft) {
  const errors = [];
  const fields = ['min', 'max', 'init'];

  for (const axis of SETTING_AXES) {
    const values = draft[axis.key] || {};
    const numbers = {};
    let bad = false;

    for (const field of fields) {
      const raw = values[field];
      const value = Number(raw);
      // 空欄や数値でないものは「数値を入れてください」で落とす
      if (raw === '' || raw === null || raw === undefined || !isFinite(value)) {
        errors.push({key: axis.key, text: `${axis.name}: 数値を入れてください`});
        bad = true;
        break;
      }
      numbers[field] = value;
    }
    if (bad) continue;

    if (!(numbers.min <= numbers.init && numbers.init <= numbers.max)) {
      errors.push({key: axis.key, text: `${axis.name}: 下限 ≦ 初期値 ≦ 上限 にしてください`});
      continue;
    }
    const outside = fields.some(
      (field) => numbers[field] < axis.abs_low || numbers[field] > axis.abs_high
    );
    if (outside) {
      errors.push({
        key: axis.key,
        text: `${axis.name}: ${axis.abs_low}〜${axis.abs_high} ${axis.unit} の範囲にしてください`,
      });
    }
  }
  return {ok: errors.length === 0, errors};
}

/**
 * 理由の一覧を 1 行の文言にする。行を増やしても画面からはみ出さないようにまとめる。
 * @param {Array<{key: string, text: string}>} errors
 * @returns {string}
 */
function settingsErrorText(errors) {
  if (!errors || !errors.length) return '';
  const first = errors[0].text;
  return errors.length > 1 ? `${first}（ほか ${errors.length - 1} 件）` : first;
}

/* --- 画面の状態 --- */

let socket = null;          // 昇降部との WS（/ws/ui）
let last_state = null;      // 最後に受けた `state`
let last_state_at_ms = null;// 受けた時刻（performance.now()）
let current_settings = null;// GET /api/settings の結果
let current_speeds = null;  // 上昇・下降のスライダーの値
let hold_timer = null;      // `hold` を送り続けるタイマー
let hold_dir = null;        // いま押している向き
let hold_press = 0;         // いま押している `press`
let press_seq = 0;          // 次の押し始めの `press`
let last_dir = 'stop';      // 直前に押していた向き（`press` を増やすかの判断用）
let sliders = null;
let outs = null;

/* 画面の設定の下書き。閉じる操作では元に戻さない（保存しない） */
let settings_draft = null;

const $ = (id) => document.getElementById(id);

function nowMs() {
  return performance.now();
}

/* --- 昇降部とのやりとり --- */

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function connect() {
  if (socket) return;
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  try {
    socket = new WebSocket(`${scheme}//${location.host}/ws/ui`);
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
    outs[axis.key].textContent = formatSpeed(spec.value);
  }
}

/* --- 押している間だけ動く --- */

function speedOf(dir) {
  const key = dir === 'up' ? 'lift_up' : 'lift_down';
  const value = Number((current_speeds || {})[key]);
  return isFinite(value) ? value : 0;
}

function sendHold() {
  if (!hold_dir) return;
  send(holdMessage(hold_press, hold_dir, speedOf(hold_dir)));
}

/** 押した。押し始めなら `press` を増やし、`hold` を送り続ける */
function startHold(dir) {
  if (hold_dir === dir) return;
  endHold();
  if (isNewPress(last_dir, dir)) press_seq += 1;
  last_dir = dir;
  hold_dir = dir;
  hold_press = press_seq;
  sendHold();
  hold_timer = setInterval(sendHold, ui_hold_period_ms);
}

/** 離した。`release` を送る（送らないと昇降部は `LIFT_CMD_TIMEOUT_MS` まで動き続ける） */
function endHold() {
  if (hold_timer !== null) {
    clearInterval(hold_timer);
    hold_timer = null;
  }
  if (!hold_dir) return;
  const press = hold_press;
  hold_dir = null;
  last_dir = 'stop';
  send(releaseMessage(press));
}

function bindHoldButtons() {
  document.querySelectorAll('.hold').forEach((button) => {
    const dir = button.dataset.dir;
    button.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      if (button.setPointerCapture) button.setPointerCapture(event.pointerId);
      button.classList.add('on');
      startHold(dir);
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

  const ceiling = ceilingText(state.ceiling === undefined ? null : state.ceiling);
  setBadge($('bCeil'), ceiling.cls, ceiling.text);
  const module = state.module || {};
  const clientsText = module.connected
    ? `端末 ${state.ui_clients}・上部あり`
    : `端末 ${state.ui_clients}`;
  $('bClients').lastElementChild.textContent = clientsText;
  $('bProv').style.display = state.provisional && state.provisional.length ? '' : 'none';
  // 上端の検知が有効になったら「一時無効」の帯は消す（W-1 の間は常に出す）
  $('bTop').style.display = state.top_detect ? 'none' : '';

  $('stHeight').textContent = heightText(state.height_mm, state.height_ok);
  $('stBottom').textContent = state.bottom ? '押されている' : '離れている';
  $('stOwner').textContent = ownerText(state.owner);
  $('stModule').textContent = module.connected
    ? `接続中（距離計${module.ceiling_sensor ? 'あり' : 'なし'}）` : '未接続';
  $('stReason').textContent = state.reason || 'NONE';

  const reason = reasonText(state.reason);
  const bar = $('stopbar');
  if (reason) {
    bar.className = `stopbar show${reason.cls === 'ng' ? ' ng' : ''}`;
    bar.textContent = reason.text;
  } else {
    bar.className = 'stopbar';
    bar.textContent = '';
  }

  if (state.dir === 'up') $('liftState').textContent = `上昇中 ${state.duty}%`;
  else if (state.dir === 'down') $('liftState').textContent = `下降中 ${state.duty}%`;
  else $('liftState').textContent = state.bottom ? '停止中・下端' : '停止中';

  // 高さのゲージ
  const height = state.height_ok === false ? 0 : Number(state.height_mm) || 0;
  $('gFill').style.height = `${Math.min(100, Math.max(0, (height / lift_gauge_full_mm) * 100))}%`;
}

/** `ui_tick_ms` ごとに見直す。「接続切れ」と再接続だけ */
function tick() {
  const stale = stateStale(last_state_at_ms, nowMs(), ui_state_timeout_ms);
  $('bWs').style.display = stale ? '' : 'none';
  if (current_settings === null) loadSettings();
  if (!socket) connect();
}

/* --- 設定画面 --- */

/* 検証の結果を入力欄・理由・保存ボタンに反映する。保存できるかを返す。 */
function validateSettingsInputs() {
  const {errors} = validateSettingsDraft(settings_draft);
  const badKeys = {};
  for (const error of errors) badKeys[error.key] = true;

  for (const axis of SETTING_AXES) {
    const row = document.querySelector(`#stBody tr[data-key="${axis.key}"]`);
    if (!row) continue;
    const bad = !!badKeys[axis.key];
    row.querySelectorAll('input').forEach((input) => {
      input.classList.toggle('bad', bad);
    });
  }
  document.getElementById('stErr').textContent = settingsErrorText(errors);
  document.getElementById('stSave').disabled = errors.length > 0;
  return errors.length === 0;
}

/* 仮値で動作中のパラメータ（state.provisional）の説明文。読み取り専用。 */
function provisionalText(names) {
  const body = names && names.length ? names.join(' / ') : 'なし';
  return `仮値で動作中（実測で確定するまで。ここでは変えられない）: ${body}`;
}

/* 設定画面を開く。現在の設定を下書きにしてから出す。 */
function openSettingsOverlay(settings, provisional) {
  settings_draft = {};
  for (const axis of SETTING_AXES) {
    const current = settings[axis.key] || {};
    settings_draft[axis.key] = {
      min: current.min === undefined ? '' : current.min,
      max: current.max === undefined ? '' : current.max,
      init: current.init === undefined ? '' : current.init,
    };
  }

  const body = document.getElementById('stBody');
  body.innerHTML = SETTING_AXES.map((axis) => {
    const cells = ['min', 'max', 'init']
      .map(
        (field) =>
          `<td><input type="number" inputmode="decimal" data-axis="${axis.key}"` +
          ` data-f="${field}" value="${settings_draft[axis.key][field]}"></td>`
      )
      .join('');
    return `<tr data-key="${axis.key}"><td>${axis.name}</td>${cells}<td>${axis.unit}</td></tr>`;
  }).join('');

  body.querySelectorAll('input').forEach((input) => {
    input.addEventListener('input', () => {
      settings_draft[input.dataset.axis][input.dataset.f] = input.value;
      validateSettingsInputs();
    });
  });

  document.getElementById('stProv').textContent = provisionalText(provisional);
  // 仮値で動いているものが無ければ説明の帯は出さない
  document.getElementById('stProv').style.display = provisional && provisional.length ? '' : 'none';
  validateSettingsInputs();
  document.getElementById('settings').classList.add('show');
}

function closeSettingsOverlay() {
  document.getElementById('settings').classList.remove('show');
}

/* 保存する。検証に通らなければ PUT を送らない。400 のときはサーバーの理由をそのまま出す。 */
async function saveSettingsOverlay() {
  if (!validateSettingsInputs()) return;

  const payload = {};
  for (const axis of SETTING_AXES) {
    const values = settings_draft[axis.key];
    payload[axis.key] = {
      min: Number(values.min),
      max: Number(values.max),
      init: Number(values.init),
    };
  }

  const save = document.getElementById('stSave');
  save.disabled = true;
  try {
    const response = await fetch('/api/settings', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (response.status === 400) {
      // サーバーの検証（protocol §3）の理由をそのまま出す。**保存しない**
      const body = await response.json().catch(() => ({}));
      const reasons = Array.isArray(body.errors) ? body.errors : [];
      document.getElementById('stErr').textContent = reasons.length
        ? reasons.join(' / ')
        : '保存できません（理由が分かりませんでした）';
      return;
    }
    if (!response.ok) {
      document.getElementById('stErr').textContent = '保存できません（サーバーのエラー）';
      return;
    }

    closeSettingsOverlay();
    // スライダーの更新などは同じ画面が見る
    document.dispatchEvent(new CustomEvent('hve_settings_saved', {detail: payload}));
    const toast = document.getElementById('toast');
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 1500);
  } finally {
    save.disabled = false;
  }
}

/* --- 組み立て --- */

function initApp() {
  sliders = {lift_up: $('sUp'), lift_down: $('sDown')};
  outs = {lift_up: $('oUp'), lift_down: $('oDown')};

  for (const key of Object.keys(sliders)) {
    sliders[key].addEventListener('input', (event) => {
      if (!current_speeds) current_speeds = {};
      current_speeds[key] = Number(event.target.value);
      const axis = SETTING_AXES.find((item) => item.key === key);
      outs[key].textContent = formatSpeed(event.target.value);
      void axis;
    });
  }
  bindHoldButtons();

  $('openSettings').addEventListener('click', () => {
    openSettingsOverlay(current_settings || {}, (last_state && last_state.provisional) || []);
  });
  // 設定画面が保存できたときにスライダーを初期値から作り直す（spec §1）
  document.addEventListener('hve_settings_saved', (event) => {
    current_settings = event.detail;
    applySettings(event.detail);
  });
  document.getElementById('stCancel').addEventListener('click', closeSettingsOverlay);
  document.getElementById('stSave').addEventListener('click', saveSettingsOverlay);

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
    holdMessage, releaseMessage, isNewPress, sliderSpec,
    formatSpeed, reasonText, ceilingText, heightText, ownerText, stateStale,
    validateSettingsDraft, settingsErrorText, SETTING_AXES,
  };
}
