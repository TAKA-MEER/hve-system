/* 昇降部の画面（firmware/lift/web）の DOM に触れない部分を確かめる。
   Node の組み込みの試験だけを使う。
   使い方: node firmware/lift/web/tests/web.test.js
   ブラウザでの経路の試験は同じ置き場の browser_paths.js（Playwright）。 */
const {test} = require('node:test');
const assert = require('node:assert/strict');

const app = require('../app.js');

test('送る hold は press・向き・デューティだけを持つ（protocol §2.1）', () => {
  assert.deepEqual(app.holdMessage(3, 'up', 40), {t: 'hold', press: 3, dir: 'up', duty: 40});
  assert.deepEqual(app.holdMessage('4', 'down', '25'), {t: 'hold', press: 4, dir: 'down', duty: 25});
  // 画面（/ws/ui）の hold に天井は載せない（使うのは距離計を持つ上部モジュールだけ。§3.1）
  assert.ok(!('ceiling' in app.holdMessage(1, 'up', 30)));
});

test('送る release は press だけを持つ（protocol §2.1）', () => {
  assert.deepEqual(app.releaseMessage(3), {t: 'release', press: 3});
  assert.deepEqual(app.releaseMessage('4'), {t: 'release', press: 4});
});

test('press は押し始め（違う向き・up と down の入れ替え）だけ増やす（§3.3）', () => {
  assert.equal(app.isNewPress('stop', 'up'), true);    // 押し始め
  assert.equal(app.isNewPress('stop', 'down'), true);  // 押し始め
  assert.equal(app.isNewPress('up', 'down'), true);    // 入れ替え
  assert.equal(app.isNewPress('down', 'up'), true);    // 入れ替え
  assert.equal(app.isNewPress('up', 'up'), false);     // 押し続けは増やさない
  assert.equal(app.isNewPress('down', 'down'), false); // 押し続けは増やさない
  assert.equal(app.isNewPress('up', 'stop'), false);    // 離すときは増やさない
});

test('スライダーは範囲が下限〜上限、位置が初期値（spec §1）', () => {
  assert.deepEqual(app.sliderSpec({min: 10, max: 60, init: 30}, 1), {
    min: 10, max: 60, step: 1, value: 30,
  });
  // 位置が上限（max）であってはいけない
  assert.equal(app.sliderSpec({min: 10, max: 60, init: 30}, 1).value, 30);
});

test('速度の数値の書式（昇降は PWM デューティ比 [%] の整数）', () => {
  assert.equal(app.formatSpeed(30), '30 %');
  assert.equal(app.formatSpeed(29.6), '30 %');
  assert.equal(app.formatSpeed('25'), '25 %');
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
  assert.equal(app.reasonText('BOTTOM').text, '下端です');
  assert.equal(app.reasonText('MAX_RUN').cls, 'warn');
  assert.equal(app.reasonText('TOP').text, '上端に達しました');
  assert.equal(app.reasonText('CMD_TIMEOUT').text, '操作が途絶えたため停止しました');
  assert.equal(app.reasonText('OWNER_GONE').cls, 'warn');

  // 知らない理由は黙って隠さない（帯を出して名前も出す）
  const unknown = app.reasonText('SOMETHING_NEW');
  assert.equal(unknown.cls, 'warn');
  assert.match(unknown.text, /SOMETHING_NEW/);
});

test('天井のバッジは距離・値なし・範囲外を出す（#3b）', () => {
  assert.deepEqual(app.ceilingText({mm: 1450, reason: 'NONE', ok: true}), {cls: '', text: '天井 1450 mm'});
  assert.deepEqual(app.ceilingText({mm: null, reason: 'CEILING_STALE', ok: false}), {cls: 'ng', text: '天井 値なし'});
  assert.deepEqual(app.ceilingText({status: 'MISSING', reason: 'CEILING_STALE', ok: false}), {cls: 'ng', text: '天井 値なし'});
  assert.deepEqual(app.ceilingText({mm: 120, reason: 'CEILING_NEAR', ok: false}), {cls: 'warn', text: '天井 120 mm'});
  assert.deepEqual(app.ceilingText({mm: null, reason: 'OUT_OF_RANGE', ok: true}), {cls: '', text: '天井 範囲外'});
  // 持ち主がいないとき（ceiling が null）は「—」（値なしの異常と区別する）
  assert.deepEqual(app.ceilingText(null), {cls: '', text: '天井 —'});
});

test('高さは読めない値と値なしを区別する', () => {
  assert.equal(app.heightText(102, true), '高さ 102 mm');
  assert.equal(app.heightText(102, false), '高さ 読めない');
  assert.equal(app.heightText(null, true), '高さ —');
});

test('持ち主の文言（最後の操作が勝つことを気づけるように出す。§1.6）', () => {
  assert.equal(app.ownerText('ui'), 'この画面');
  assert.equal(app.ownerText('module'), '上部モジュール');
  assert.equal(app.ownerText(null), 'なし');
});

test('state が ui_state_timeout_ms 届かないとき「接続切れ」', () => {
  assert.equal(app.stateStale(null, 5000, 1000), true);
  assert.equal(app.stateStale(0, 1000, 1000), false);
  assert.equal(app.stateStale(0, 1001, 1000), true);
});

/* --- 設定画面 --- */

const GOOD = {
  lift_up: {min: 10, max: 60, init: 30},
  lift_down: {min: 10, max: 60, init: 30},
};

const withChange = (axis, field, value) => {
  const draft = JSON.parse(JSON.stringify(GOOD));
  draft[axis][field] = value;
  return draft;
};

test('設定の検証は 2 項目が Save できる形を通す（spec §2）', () => {
  const result = app.validateSettingsDraft(GOOD);
  assert.equal(result.ok, true);
  assert.deepEqual(result.errors, []);
});

test('下限 > 上限は保存できない', () => {
  const result = app.validateSettingsDraft(withChange('lift_up', 'min', 70));
  assert.equal(result.ok, false);
  assert.match(result.errors[0].text, /上昇/);
  assert.match(result.errors[0].text, /下限 ≦ 初期値 ≦ 上限/);
});

test('初期値が上限を超える / 下限より小さいものも保存できない', () => {
  assert.equal(app.validateSettingsDraft(withChange('lift_down', 'init', 61)).ok, false);
  assert.equal(app.validateSettingsDraft(withChange('lift_up', 'init', 9)).ok, false);
});

test('絶対的な範囲を外すのも保存できない（protocol §3）', () => {
  assert.equal(app.validateSettingsDraft(withChange('lift_up', 'max', 101)).ok, false);
  assert.equal(app.validateSettingsDraft(withChange('lift_up', 'min', -1)).ok, false);
});

test('数値でない・空欄は保存できない', () => {
  assert.equal(app.validateSettingsDraft(withChange('lift_down', 'min', '')).ok, false);
  assert.equal(app.validateSettingsDraft(withChange('lift_down', 'min', 'abc')).ok, false);
  const missing = JSON.parse(JSON.stringify(GOOD));
  delete missing.lift_down.init;
  assert.equal(app.validateSettingsDraft(missing).ok, false);
});

test('理由の文言は 1 行にまとめる（画面からはみ出さない）', () => {
  assert.equal(app.settingsErrorText([]), '');
  const many = app.validateSettingsDraft({
    lift_up: {min: 70, max: 60, init: 30},
    lift_down: {min: 10, max: 5, init: 30},
  });
  assert.equal(many.errors.length, 2);
  assert.equal(app.settingsErrorText(many.errors), '上昇: 下限 ≦ 初期値 ≦ 上限 にしてください（ほか 1 件）');
});
