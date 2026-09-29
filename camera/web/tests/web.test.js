/* 画面（camera/web）の DOM に触れない部分を確かめる。Node の組み込みの試験だけを使う。
   使い方: node --test camera/web/tests/web.test.js
   ブラウザでの経路の試験は同じ置き場の browser_paths.js（Playwright）。 */
const {test} = require('node:test');
const assert = require('node:assert/strict');

const app = require('../app.js');
const settings = require('../settings.js');

test('送る hold は軸と速度だけを持つ（protocol §2.1）', () => {
  assert.deepEqual(app.holdMessage('lift_up', 40), {t: 'hold', axis: 'lift_up', speed: 40});
  assert.deepEqual(app.holdMessage('pitch_down', '12.5'), {t: 'hold', axis: 'pitch_down', speed: 12.5});
  assert.deepEqual(app.holdMessage('yaw_left', 0), {t: 'hold', axis: 'yaw_left', speed: 0});
});

test('release と zoom の JSON', () => {
  assert.deepEqual(app.releaseMessage(), {t: 'release'});
  assert.deepEqual(app.zoomMessage(1.5), {t: 'zoom', level: 1.5});
  assert.deepEqual(app.zoomMessage('2'), {t: 'zoom', level: 2});
});

test('「＋」「−」の 1 段は zoom_step の倍数で 1〜zoom_max に収まる', () => {
  assert.equal(app.zoomTarget(1, 0.5), 1.5);   // 開いた直後の「＋」は 1.5
  assert.equal(app.zoomTarget(1, -0.5), 1);    // 1 倍より小さくはならない
  assert.equal(app.zoomTarget(4, 0.5), 4);     // 上限以上にはならない
  assert.equal(app.zoomTarget(0.25, 0.5), 1);
  assert.equal(app.zoomTarget(2, 0), 2);        // 0 は「変わらない」
  assert.equal(app.zoomTarget(1.2, 0.5), 1.5);  // 段階にそろえる
  assert.equal(app.zoomTarget('x', 0.5), 1.5);  // 読めない値は 1 倍から数える
});

test('軸 → 送る速度。ピッチの上下とヨーの左右は同じ枠（spec §1）', () => {
  const speeds = {lift_up: 30, lift_down: 25, pitch: 10, yaw: 7};
  assert.equal(app.speedFor('lift_up', speeds), 30);
  assert.equal(app.speedFor('lift_down', speeds), 25);
  assert.equal(app.speedFor('pitch_up', speeds), 10);
  assert.equal(app.speedFor('pitch_down', speeds), 10);
  assert.equal(app.speedFor('yaw_left', speeds), 7);
  assert.equal(app.speedFor('yaw_right', speeds), 7);
  assert.equal(app.speedFor('unknown', speeds), 0);
  assert.equal(app.speedFor('lift_up', null), 0);
});

test('スライダーは範囲が下限〜上限、位置が初期値（spec §1）', () => {
  assert.deepEqual(app.sliderSpec({min: 10, max: 60, init: 30}, 1), {
    min: 10, max: 60, step: 1, value: 30,
  });
  assert.deepEqual(app.sliderSpec({min: 1, max: 30, init: 10}, 0.5), {
    min: 1, max: 30, step: 0.5, value: 10,
  });
  // 位置が上限（max）であってはいけない
  assert.equal(app.sliderSpec({min: 10, max: 60, init: 30}, 1).value, 30);
});

test('速度の数値の書式', () => {
  assert.equal(app.formatSpeed(30, '%'), '30 %');
  assert.equal(app.formatSpeed(29.6, '%'), '30 %');
  assert.equal(app.formatSpeed(10, 'deg/s'), '10.0 deg/s');
  assert.equal(app.formatSpeed(12.25, 'deg/s'), '12.3 deg/s');
});

test('映像の URL は宿主とポートだけから作る（倍率には依存しない）', () => {
  assert.equal(app.streamUrl('http:', 'hve-cam.local', 8080), 'http://hve-cam.local:8080/stream');
  // 拡大しても URL は変わらない（ブラウザ側で拡大しない。spec §1.5）
  assert.equal(app.streamUrl('http:', 'hve-cam.local', 8080), app.streamUrl('http:', 'hve-cam.local', 8080));
});

test('停止理由の文言（names §3 → 画面に出る日本語）', () => {
  assert.equal(app.reasonText('NONE'), null);
  assert.equal(app.reasonText('CMD_STOP'), null);
  assert.equal(app.reasonText(null), null);

  assert.deepEqual(app.reasonText('CEILING_NEAR'), {
    cls: 'ng', text: '天井が近いため上昇できません',
  });
  assert.equal(app.reasonText('CEILING_STALE').cls, 'ng');
  assert.equal(app.reasonText('HEIGHT_UNKNOWN').cls, 'ng');
  assert.equal(app.reasonText('LINK_LOST').cls, 'ng');
  assert.equal(app.reasonText('MAX_RUN').cls, 'warn');
  assert.equal(app.reasonText('TOP').text, '上端に達しました');
  assert.equal(app.reasonText('BOTTOM').text, '下端です');
  assert.equal(app.reasonText('HOLD_TIMEOUT').text, '操作が途絶えたため停止しました');
  assert.equal(app.reasonText('AXIS_LIMIT').text, '可動範囲の端です');
  // 昇降部が出す理由（CEILING・CMD_TIMEOUT）も文言を持つ
  assert.equal(app.reasonText('CEILING').cls, 'ng');
  assert.equal(app.reasonText('CMD_TIMEOUT').cls, 'warn');

  // 知らない理由は黙って隠さない（帯を出して名前も出す）
  const unknown = app.reasonText('SOMETHING_NEW');
  assert.equal(unknown.cls, 'warn');
  assert.match(unknown.text, /SOMETHING_NEW/);
});

test('天井のバッジは距離・値なし・範囲外を出す（spec Spec-safety.md §2 #3b）', () => {
  assert.deepEqual(app.ceilingText({mm: 1450, reason: 'NONE', ok: true}), {cls: '', text: '天井 1450 mm'});
  assert.deepEqual(app.ceilingText({mm: null, reason: 'CEILING_STALE', ok: false}), {cls: 'ng', text: '天井 値なし'});
  assert.deepEqual(app.ceilingText({mm: 120, reason: 'CEILING_NEAR', ok: false}), {cls: 'warn', text: '天井 120 mm'});
  assert.deepEqual(app.ceilingText({mm: null, reason: 'OUT_OF_RANGE', ok: true}), {cls: '', text: '天井 範囲外'});
});

test('高さの OSD は読めない値と値なしを区別する', () => {
  assert.equal(app.heightText({height_mm: 102, height_ok: true}), '高さ 102 mm');
  assert.equal(app.heightText({height_mm: 102, height_ok: false}), '高さ 読めない');
  assert.equal(app.heightText({height_mm: null, height_ok: true}), '高さ —');
});

test('ボタンを薄くする条件（昇降部と切断・天井の ok が false は上昇／下端は下降）', () => {
  const ok = {
    lift: {link: 'ok', bottom: false},
    ceiling: {mm: 2200, ok: true, reason: 'NONE'},
  };
  assert.equal(app.holdBlocked(ok, 'lift_up'), false);
  assert.equal(app.holdBlocked(ok, 'lift_down'), false);
  assert.equal(app.holdBlocked(ok, 'pitch_up'), false);

  const lost = {lift: {link: 'lost', bottom: false}, ceiling: {mm: 2200, ok: true, reason: 'NONE'}};
  assert.equal(app.holdBlocked(lost, 'lift_up'), true);
  assert.equal(app.holdBlocked(lost, 'lift_down'), false);

  const stale = {lift: {link: 'ok', bottom: false}, ceiling: {mm: 2200, ok: false, reason: 'CEILING_STALE'}};
  assert.equal(app.holdBlocked(stale, 'lift_up'), true);

  const bottom = {lift: {link: 'ok', bottom: true}, ceiling: {mm: 2200, ok: true, reason: 'NONE'}};
  assert.equal(app.holdBlocked(bottom, 'lift_down'), true);
  assert.equal(app.holdBlocked(bottom, 'lift_up'), false);
});

test('state が ui_state_timeout_ms 届かないとき「接続切れ」', () => {
  assert.equal(app.stateStale(null, 5000, 1000), true);
  assert.equal(app.stateStale(0, 1000, 1000), false);
  assert.equal(app.stateStale(0, 1001, 1000), true);
});

/* --- 設定画面（settings.js） --- */

const GOOD = {
  lift_up: {min: 10, max: 60, init: 30},
  lift_down: {min: 10, max: 60, init: 30},
  pitch: {min: 1, max: 30, init: 10},
  yaw: {min: 1, max: 30, init: 10},
};

const withChange = (axis, field, value) => {
  const draft = JSON.parse(JSON.stringify(GOOD));
  draft[axis][field] = value;
  return draft;
};

test('設定の検証は 4 項目が Save できる形を通す（spec §2）', () => {
  const result = settings.validateSettingsDraft(GOOD);
  assert.equal(result.ok, true);
  assert.deepEqual(result.errors, []);
});

test('下限 > 上限は保存できない', () => {
  const result = settings.validateSettingsDraft(withChange('lift_up', 'min', 70));
  assert.equal(result.ok, false);
  assert.match(result.errors[0].text, /上昇/);
  assert.match(result.errors[0].text, /下限 ≦ 初期値 ≦ 上限/);
});

test('初期値が上限を超える / 下限より小さい 것도保存できない', () => {
  assert.equal(settings.validateSettingsDraft(withChange('yaw', 'init', 31)).ok, false);
  assert.equal(settings.validateSettingsDraft(withChange('pitch', 'init', 0.5)).ok, false);
});

test('絶対的な範囲を外すのも保存できない（protocol §3）', () => {
  assert.equal(settings.validateSettingsDraft(withChange('lift_up', 'max', 101)).ok, false);
  assert.equal(settings.validateSettingsDraft(withChange('lift_up', 'min', -1)).ok, false);
  assert.equal(settings.validateSettingsDraft(withChange('pitch', 'max', 61)).ok, false);
  assert.equal(settings.validateSettingsDraft(withChange('pitch', 'init', 0)).ok, false);
});

test('数値でない・空欄は保存できない', () => {
  assert.equal(settings.validateSettingsDraft(withChange('yaw', 'min', '')).ok, false);
  assert.equal(settings.validateSettingsDraft(withChange('yaw', 'min', 'abc')).ok, false);
  const missing = JSON.parse(JSON.stringify(GOOD));
  delete missing.yaw.init;
  assert.equal(settings.validateSettingsDraft(missing).ok, false);
});

test('理由の文言は 1 行にまとめる（画面からはみ出さない）', () => {
  assert.equal(settings.settingsErrorText([]), '');
  const many = settings.validateSettingsDraft({
    lift_up: {min: 70, max: 60, init: 30},
    lift_down: {min: 10, max: 5, init: 30},
    pitch: {min: 1, max: 30, init: 10},
    yaw: {min: 1, max: 30, init: 10},
  });
  assert.equal(many.errors.length, 2);
  assert.equal(settings.settingsErrorText(many.errors), '上昇: 下限 ≦ 初期値 ≦ 上限 にしてください（ほか 1 件）');
});
