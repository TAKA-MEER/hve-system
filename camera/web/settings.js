/* ============================================================
   設定画面（index.html の中に出る別画面。names §1）
   spec Spec-ui.md §2。上昇・下降・ピッチ・ヨーの 4 項目について
   下限・上限・初期値と、モータへの出力の上限（昇降部へ中継。自分では保存しない）を
   PUT /api/settings で機器（カメラ部）に保存する。

   DOM に触れない部分は validateSettingsDraft() と settingsErrorText() に分ける
   （camera/web/tests/web.test.js から Node の組み込みの試験で確かめる）。
   保存できたときは `hve_settings_saved` を document に流す。スライダーの
   更新などは app.js が見る側（app.js はこの画面を読み書きしない）。
   ============================================================ */

/* 設定できる項目。名前・単位・絶対的な範囲（protocol §3 の検証に対応） */
const SETTING_AXES = [
  {key: 'lift_up',   name: '上昇',   unit: 'mm/s',  abs_low: 0,   abs_high: 100, step: 1},
  {key: 'lift_down', name: '下降',   unit: 'mm/s',  abs_low: 0,   abs_high: 100, step: 1},
  {key: 'pitch',     name: 'ピッチ', unit: 'deg/s', abs_low: 0.1, abs_high: 60,  step: 0.5},
  {key: 'yaw',       name: 'ヨー',   unit: 'deg/s', abs_low: 0.1, abs_high: 60,  step: 0.5},
];

/* モータへの出力の上限 [%]（spec Spec-ui.md §2.1）。速度の 3 値ではなく 1 値 */
const SETTING_MOTOR = {
  key: 'motor_max_pct', name: 'モータへの出力の上限', unit: '%',
  abs_low: 10, abs_high: 100, step: 1,
};
/* 単体の操作でも天井で止めるか（spec Spec-ui.md §2.2。真偽値。昇降部が持つ）。
   昇降部が対応していない（GET に無い）ときは欄を無効にし、PUT にも載せない */
const SETTING_CEILING = {key: 'ceiling_standalone', name: '単体の操作でも天井で止める'};
/* 天井の閾値 [mm]（spec Spec-ui.md §2.3。150〜3000 の整数。昇降部が持つ）。
   上部モジュールの距離計から天井までこの距離以下なら上昇しない。
   昇降部が対応していない（GET に無い）ときは欄を無効にし、PUT にも載せない */
const SETTING_CEILING_MARGIN = {
  key: 'ceiling_margin_mm', name: '天井の閾値', unit: 'mm', abs_low: 150, abs_high: 3000, step: 1,
};
const CEILING_UNSUPPORTED_TEXT = '昇降部が対応していません';
/* 設定画面の添え書き（spec §2.1 の「モータの定格電圧 ÷ 電源電圧」） */
const MOTOR_NOTE = 'モータの定格電圧 ÷ 電源電圧（12 V のモータを 24 V で回すなら 50）';

/* 画面に出している下書き。閉じる操作では元に戻さない（保存しない） */
let settings_draft = null;

/**
 * 設定の検証。`min ≦ init ≦ max` と絶対的な範囲を見る（protocol §3）。
 * 昇降の 2 軸は整数 [mm/s]（刻み 1）でないと落とす。
 * モータへの出力の上限は 10〜100 の整数で、欠けても通さない（protocol §3）。
 * @param {object} draft 4 項目の {min, max, init} とモータへの出力の上限の 1 値
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
    // 昇降の 2 軸は整数 [mm/s]（刻み 1）。ピッチ・ヨーは小数も許す
    if ((axis.key === 'lift_up' || axis.key === 'lift_down')
        && fields.some((field) => !Number.isInteger(numbers[field]))) {
      errors.push({key: axis.key, text: `${axis.name}: 整数を入れてください`});
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

  // モータへの出力の上限（1 値。protocol §3: 10〜100 の整数。欠けても通さない）
  const motorRaw = draft[SETTING_MOTOR.key];
  const motorValue = Number(motorRaw);
  if (motorRaw === '' || motorRaw === null || motorRaw === undefined || !isFinite(motorValue)) {
    errors.push({key: SETTING_MOTOR.key, text: `${SETTING_MOTOR.name}: 数値を入れてください`});
  } else if (!Number.isInteger(motorValue)) {
    errors.push({key: SETTING_MOTOR.key, text: `${SETTING_MOTOR.name}: 整数にしてください`});
  } else if (motorValue < SETTING_MOTOR.abs_low || motorValue > SETTING_MOTOR.abs_high) {
    errors.push({
      key: SETTING_MOTOR.key,
      text: `${SETTING_MOTOR.name}: ${SETTING_MOTOR.abs_low}〜${SETTING_MOTOR.abs_high} ${SETTING_MOTOR.unit} の整数にしてください`,
    });
  }
  // 単体の操作でも天井で止めるか（無くてもよい。あれば真偽値）
  const standalone = draft[SETTING_CEILING.key];
  if (standalone !== undefined && typeof standalone !== 'boolean') {
    errors.push({key: SETTING_CEILING.key, text: `${SETTING_CEILING.name}: 入り切りにしてください`});
  }
  // 天井の閾値（無くてもよい。あれば 150〜3000 の整数。小数・文字列・真偽値は不可）
  const margin = draft[SETTING_CEILING_MARGIN.key];
  if (margin !== undefined) {
    const marginValue = Number(margin);
    const m = SETTING_CEILING_MARGIN;
    if (margin === '' || margin === null || typeof margin === 'boolean' || !isFinite(marginValue)) {
      errors.push({key: m.key, text: `${m.name}: 数値を入れてください`});
    } else if (!Number.isInteger(marginValue)) {
      errors.push({key: m.key, text: `${m.name}: 整数にしてください`});
    } else if (marginValue < m.abs_low || marginValue > m.abs_high) {
      errors.push({key: m.key, text: `${m.name}: ${m.abs_low}〜${m.abs_high} ${m.unit} の整数にしてください`});
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

/* --- DOM --------------------------------------------------------------- */

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

/* 「単体の操作でも天井で止める」の行。対応していない昇降部では無効にして理由を出す */
function standaloneRowHtml() {
  const supported = typeof settings_draft[SETTING_CEILING.key] === 'boolean';
  const state = supported
    ? (settings_draft[SETTING_CEILING.key] ? ' checked' : '')
    : ' disabled';
  const note = supported ? '' : `（${CEILING_UNSUPPORTED_TEXT}）`;
  return `<tr data-key="${SETTING_CEILING.key}"><td colspan="5" class="wrapcell stwide">` +
    `<label><input type="checkbox" data-axis="${SETTING_CEILING.key}"${state}>` +
    `${SETTING_CEILING.name}${note}</label></td></tr>`;
}

/* 「天井の閾値」の行。対応していない昇降部では無効にして理由を出す（1 行に収める） */
function ceilingMarginRowHtml() {
  const m = SETTING_CEILING_MARGIN;
  const value = settings_draft[m.key];
  const supported = value !== undefined;
  const attrs = supported
    ? ` value="${value}" min="${m.abs_low}" max="${m.abs_high}" step="${m.step}"`
    : ` disabled placeholder="${CEILING_UNSUPPORTED_TEXT}"`;
  return `<tr data-key="${m.key}"><td class="wrapcell">${m.name}</td>` +
    `<td colspan="3"><input type="number" inputmode="numeric" data-axis="${m.key}"${attrs}></td>` +
    `<td>${m.unit}</td></tr>`;
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
  const motorCurrent = (settings || {})[SETTING_MOTOR.key];
  settings_draft[SETTING_MOTOR.key] = motorCurrent === undefined ? '' : motorCurrent;
  // 昇降部が対応していなければ `undefined` のまま（欄を無効にし、PUT に載せない）
  const standaloneCurrent = (settings || {})[SETTING_CEILING.key];
  if (typeof standaloneCurrent === 'boolean') settings_draft[SETTING_CEILING.key] = standaloneCurrent;
  const marginCurrent = (settings || {})[SETTING_CEILING_MARGIN.key];
  if (typeof marginCurrent === 'number') settings_draft[SETTING_CEILING_MARGIN.key] = marginCurrent;

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
  }).join('') +
    `<tr data-key="${SETTING_MOTOR.key}"><td class="wrapcell">${SETTING_MOTOR.name}</td>` +
    `<td colspan="3"><input type="number" inputmode="decimal" data-axis="${SETTING_MOTOR.key}"` +
    ` value="${settings_draft[SETTING_MOTOR.key]}"></td><td>${SETTING_MOTOR.unit}</td></tr>` +
    standaloneRowHtml() +
    ceilingMarginRowHtml();

  body.querySelectorAll('input').forEach((input) => {
    if (input.dataset.axis === SETTING_CEILING.key) {
      input.addEventListener('change', () => {
        settings_draft[SETTING_CEILING.key] = input.checked;
        validateSettingsInputs();
      });
      return;
    }
    input.addEventListener('input', () => {
      if (input.dataset.axis === SETTING_MOTOR.key) {
        settings_draft[SETTING_MOTOR.key] = input.value;
      } else if (input.dataset.axis === SETTING_CEILING_MARGIN.key) {
        settings_draft[SETTING_CEILING_MARGIN.key] = input.value;
      } else {
        settings_draft[input.dataset.axis][input.dataset.f] = input.value;
      }
      validateSettingsInputs();
    });
  });

  document.getElementById('stProv').textContent = provisionalText(provisional);
  // 仮値で動いているものが無ければ説明の帯は出さない（spec §2）
  document.getElementById('stProv').style.display = provisional && provisional.length ? '' : 'none';
  validateSettingsInputs();
  document.getElementById('settings').classList.add('show');
}

function closeSettingsOverlay() {
  document.getElementById('settings').classList.remove('show');
}

/**
 * 下書きから `PUT /api/settings` の本文を作る。
 * `ceiling_standalone` は昇降部が対応している（下書きが真偽値を持つ）ときだけ載せる
 * （対応していない昇降部へ送らない。昇降部は無ければ今の値を保つ）。
 * `ceiling_margin_mm` も同じく、下書きに値があるときだけ載せる。
 */
function buildSettingsPayload(draft) {
  const payload = {};
  for (const axis of SETTING_AXES) {
    const values = draft[axis.key];
    payload[axis.key] = {
      min: Number(values.min),
      max: Number(values.max),
      init: Number(values.init),
    };
  }
  payload[SETTING_MOTOR.key] = Number(draft[SETTING_MOTOR.key]);
  if (typeof draft[SETTING_CEILING.key] === 'boolean') {
    payload[SETTING_CEILING.key] = draft[SETTING_CEILING.key];
  }
  if (draft[SETTING_CEILING_MARGIN.key] !== undefined) {
    payload[SETTING_CEILING_MARGIN.key] = Number(draft[SETTING_CEILING_MARGIN.key]);
  }
  return payload;
}

/* 保存する。検証に通らなければ PUT を送らない。400 のときはサーバーの理由をそのまま出す。 */
async function saveSettingsOverlay() {
  if (!validateSettingsInputs()) return;

  const payload = buildSettingsPayload(settings_draft);

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
    if (response.status === 503) {
      // 昇降の設定は昇降部へ中継する。繋がらないときはカメラ部も保存しない（protocol §4）
      document.getElementById('stErr').textContent = '昇降部に繋がらないため保存できません（何も保存していません）';
      return;
    }
    if (!response.ok) {
      document.getElementById('stErr').textContent = '保存できません（サーバーのエラー）';
      return;
    }

    closeSettingsOverlay();
    // スライダーの更新などは app.js が見る（この画面は app.js の内部 state を知らない）
    document.dispatchEvent(new CustomEvent('hve_settings_saved', {detail: payload}));
    const toast = document.getElementById('toast');
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 1500);
  } finally {
    save.disabled = false;
  }
}

if (typeof document !== 'undefined') {
  document.getElementById('stCancel').addEventListener('click', closeSettingsOverlay);
  document.getElementById('stSave').addEventListener('click', saveSettingsOverlay);
}

if (typeof module === 'object' && module !== null) {
  module.exports = {SETTING_AXES, SETTING_MOTOR, SETTING_CEILING, SETTING_CEILING_MARGIN, MOTOR_NOTE, buildSettingsPayload, validateSettingsDraft, settingsErrorText};
}
