/* ============================================================
   設定画面（index.html の中に出る別画面。names §1）
   spec Spec-ui.md §2。上昇・下降・ピッチ・ヨーの 4 項目について
   下限・上限・初期値を PUT /api/settings で機器（カメラ部）に保存する。

   DOM に触れない部分は validateSettingsDraft() と settingsErrorText() に分ける
   （camera/web/tests/web.test.js から Node の組み込みの試験で確かめる）。
   保存できたときは `hve_settings_saved` をdocument に流す。スライダーの
   更新などは app.js を受ける側（app.js はこの画面を読み書きしない）。
   ============================================================ */

/* 設定できる項目。名前・単位・絶対的な範囲（protocol §3 の検証に対応） */
const SETTING_AXES = [
  {key: 'lift_up',   name: '上昇',   unit: '%',     abs_low: 0,   abs_high: 100, step: 1},
  {key: 'lift_down', name: '下降',   unit: '%',     abs_low: 0,   abs_high: 100, step: 1},
  {key: 'pitch',     name: 'ピッチ', unit: 'deg/s', abs_low: 0.1, abs_high: 60,  step: 0.5},
  {key: 'yaw',       name: 'ヨー',   unit: 'deg/s', abs_low: 0.1, abs_high: 60,  step: 0.5},
];

/* 画面に出している下書き。閉じる操作では元に戻さない（保存しない） */
let settings_draft = null;

/**
 * 設定の検証。`min ≦ init ≦ max` と絶対的な範囲を見る（protocol §3）。
 * @param {object} draft 4 項目の {min, max, init}
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
  module.exports = {SETTING_AXES, validateSettingsDraft, settingsErrorText};
}
