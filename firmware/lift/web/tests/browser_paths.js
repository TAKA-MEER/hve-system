// 使い方: node firmware/lift/web/tests/browser_paths.js [http://127.0.0.1:18080/]
//   playwright が require できないときは PLAYWRIGHT_PATH=<.../node_modules/playwright> を付ける。
//   ブラウザは既定で /usr/bin/google-chrome（CHROME_PATH で変更）。
//   先に `.venv/bin/python tools/fake_lift_server.py --port 18080` を立てておく。
//
// ブラウザの「経路」を通す試験。純関数の試験（web.test.js）では下の経路は縛れない:
// 1. 上昇ボタンを押している間、偽の昇降部の向こう側から見て dir が up。離すとすぐ
//    release が届いて stop に戻る（release を送らなければ CMD_TIMEOUT まで動いたまま）
// 2. 押し始めごとに press が 1 増える（増やさなければ古い hold と区別できない）
// 3. 押したままページを閉じると、向こう側から見て止まる（OWNER_GONE）
// 4. 設定で不正値（下限 > 上限）を入れると PUT を出さず、サーバーの値も変わらない
// 5. 上部モジュールが名乗った busy_label が「持ち主」に出る・繋がっているものが一覧に並ぶ・
//    arm.out が true のときだけ「腕が出ています」が出る（render の呼び出し側を縛る）
// 6. ceiling.standalone の理由（通信なし・設定が切）が天井のバッジに出る（render の呼び出し側）。
//    設定画面のチェックを切って保存すると PUT に ceiling_standalone: false が載り、開き直すと
//    反映されている
// 7. 画面の上昇が、距離計を持つ上部モジュールの新しい近い値で CEILING_NEAR で止まり、
//    その上部モジュールが切れる（出どころ無し）と天井を使わずに上がる
// 9. 測定機構: 通信が切れる（arm が来なくなる）とダイアログが出て、通信が戻ると答えなくても消え、
//    2 回目の止めでは**いまの番号**を載せた arm_confirm が送られて解ける。原点の前の警告は
//    高さが読めず・通信できて・out が true の 3 つが揃ったときだけ出る（render の呼び出し側）
// 8. 設定画面の天井の閾値（ceiling_margin_mm）。149 は PUT を出さず、2500 を保存すると PUT に
//    ceiling_margin_mm: 2500 が載り、開き直すと反映され、2000 mm の天井で上昇が CEILING_NEAR で止まる
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');

const BASE = (process.argv[2] || 'http://127.0.0.1:18080/').replace(/\/$/, '');
const VIEWPORT = {width: 900, height: 500};

// 画面側と偽の昇降部の時間（app.js の ui_hold_period_ms と fake の LIFT_CMD_TIMEOUT_MS）。
// HOLD_MS だけ送らないと TIMEOUT_MS で止めるので、「押し続けた」ことの境目になる
const HOLD_MS = 100;
const TIMEOUT_MS = 600;
const STATE_MS = 100;

async function postFake(payload) {
  const response = await fetch(`${BASE}/api/fake`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`POST /api/fake が ${response.status}: ${await response.text()}`);
}

/** 向こう側（/ws/module）として状態を観察し、送られた hold/release も拾う */
function observeModule() {
  const ws = new WebSocket(`ws://${new URL(BASE).host}/ws/module`);
  let latest = null;
  const sent = [];
  const opened = new Promise((resolve, reject) => {
    ws.addEventListener('open', () => {
      ws.send(JSON.stringify({t: 'hello', ceiling_sensor: false, name: 'watcher'}));
      resolve();
    });
    ws.addEventListener('error', reject);
  });
  ws.addEventListener('message', (event) => {
    const data = JSON.parse(event.data);
    if (data && data.t === 'state') latest = data;
  });
  return {
    opened,
    latest: () => latest,
    async waitFor(pred, ms, label) {
      const until = Date.now() + ms;
      while (Date.now() < until) {
        if (latest && pred(latest)) return latest;
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      throw new Error(`${label}: ${ms} ms 以内に条件を満たす state が来なかった（最新 ${JSON.stringify(latest)}）`);
    },
    close: () => ws.close(),
    sent,
  };
}

/** 画面を開く。state が来たのを待って、JavaScript のエラーも拾っておく */
async function openScreen(browser) {
  const page = await browser.newPage({viewport: VIEWPORT});
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(BASE + '/');
  await page.waitForFunction(
    () => document.getElementById('stHeight').textContent.trim() !== '—',
    null,
    {timeout: 5000}
  );
  const cleanup = async () => {
    await page.mouse.up().catch(() => {});
    await page.close().catch(() => {});
  };
  return {page, errors, cleanup};
}

/** 上昇のボタンで押す（pointerdown だけ。離さない） */
async function pressUp(page) {
  const box = await page.locator('[data-dir=up]').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
}

/** 画面の WS が送った生のメッセージを拾う（ページのスクリプトより先に包む） */
async function tapUiMessages(browser) {
  const page = await browser.newPage({viewport: VIEWPORT});
  await page.addInitScript(() => {
    window.__sent = [];
    const Orig = window.WebSocket;
    window.WebSocket = function (...args) {
      const ws = new Orig(...args);
      const origSend = ws.send.bind(ws);
      ws.send = (data) => { window.__sent.push(String(data)); return origSend(data); };
      return ws;
    };
    window.WebSocket.prototype = Orig.prototype;
    // app.js は WebSocket.OPEN を見るので定数も写す（無いと send が抑止される）
    for (const key of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) {
      window.WebSocket[key] = Orig[key];
    }
  });
  await page.goto(BASE + '/');
  await page.waitForFunction(
    () => document.getElementById('stHeight').textContent.trim() !== '—',
    null,
    {timeout: 5000}
  );
  return {
    page,
    async sent() {
      return page.evaluate(() => (window.__sent || []).map((s) => { try { return JSON.parse(s); } catch { return null; } }));
    },
    async cleanup() { await page.close().catch(() => {}); },
  };
}

const tests = [];
const test = (name, body) => tests.push({name, body});

/* --- 1. 押している間だけ動く ------------------------------------------------- */
test('1 上昇を押している間 up、離すと release で止まる', async (browser) => {
  await postFake({height_mm: 1200, bottom: false});
  const {page, errors, cleanup} = await openScreen(browser);
  const watcher = observeModule();
  await watcher.opened;
  try {
    await pressUp(page);
    await watcher.waitFor((s) => s.dir === 'up' && s.owner === 'ui', 1500, '押した直後に上昇');

    // 定期送信の確認。6 周期ぶん（600 ms）見て、指を離すまでずっと上昇のままであること
    const started = Date.now();
    const samples = [];
    while (Date.now() - started < 6 * HOLD_MS) {
      const state = watcher.latest();
      samples.push({at: Date.now() - started, dir: state.dir, owner: state.owner, reason: state.reason});
      await page.waitForTimeout(HOLD_MS / 2);
    }
    const held = samples.filter((s) => s.at >= 2 * HOLD_MS);
    assert.ok(held.length >= 4, `定期送信: サンプルが少ない（${JSON.stringify(samples)}）`);
    assert.deepEqual([...new Set(held.map((s) => s.dir))], ['up'],
      `押している間はすべて up（${JSON.stringify(samples)}）`);

    await page.mouse.up();
    // 離したらすぐ放す。CMD_TIMEOUT（600 ms）までにかからなければ release を送っている
    const stopped = await watcher.waitFor((s) => s.dir === 'stop', TIMEOUT_MS - 2 * STATE_MS, '離した直後に止まる');
    assert.notEqual(stopped.reason, 'CMD_TIMEOUT', '離したのに CMD_TIMEOUT で止まった（release を送っていない）');
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    watcher.close();
    await cleanup();
  }
});

/* --- 2. press が増える ------------------------------------------------------- */
test('2 押し始めごとに press が増える', async (browser) => {
  await postFake({height_mm: 1200, bottom: false});
  const tap = await tapUiMessages(browser);
  try {
    const up = tap.page.locator('[data-dir=up]');
    const box = await up.boundingBox();
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    await tap.page.mouse.move(cx, cy);
    await tap.page.mouse.down();
    await tap.page.waitForTimeout(3 * HOLD_MS);
    await tap.page.mouse.up();
    await tap.page.waitForTimeout(2 * HOLD_MS);
    await tap.page.mouse.move(cx, cy);
    await tap.page.mouse.down();
    await tap.page.waitForTimeout(3 * HOLD_MS);
    await tap.page.mouse.up();
    const sent = await tap.sent();
    const holds = sent.filter((m) => m && m.t === 'hold');
    const releases = sent.filter((m) => m && m.t === 'release');
    assert.ok(holds.length >= 2, `hold が 2 回以上送られていない（${JSON.stringify(sent)}）`);
    assert.ok(releases.length >= 1, `release が送られていない（${JSON.stringify(sent)}）`);
    const first = holds.map((m) => m.press);
    assert.ok(Math.max(...first) > Math.min(...first),
      `押し始めで press が増えていない（${JSON.stringify(first)}）`);
    // release は直前の hold と同じ press
    assert.equal(releases[0].press, holds[0].press, 'release の press が直前の hold と違う');
  } finally {
    await tap.cleanup();
  }
});

/* --- 3. 押したままページを閉じた ---------------------------------------------- */
test('3 押したままページを閉じると向こう側から見て止まる', async (browser) => {
  await postFake({height_mm: 1200, bottom: false});
  const {page} = await openScreen(browser);
  const watcher = observeModule();
  await watcher.opened;
  try {
    await pressUp(page);
    await watcher.waitFor((s) => s.dir === 'up', 1500, '押しているあいだ上昇');
    await page.close(); // 離さずに閉じる
    const stopped = await watcher.waitFor((s) => s.dir === 'stop', 1500, 'ページを閉じた後に止まる');
    // pagehide で release が送られれば CMD_STOP、間に合わず切断だけなら OWNER_GONE。
    // どちらでも止まること。release も close 処理も壊すと CMD_TIMEOUT になる
    assert.ok(stopped.reason === 'CMD_STOP' || stopped.reason === 'OWNER_GONE',
      `ページを閉じても止まっていない（reason=${stopped.reason}）`);
  } finally {
    watcher.close();
    await page.close().catch(() => {});
  }
});

/* --- 10. 目標の高さへで着いて送るのをやめる ---------------------------------- */
test('10 目標の高さへで着いて、送るのをやめる', async (browser) => {
  await postFake({height_mm: 800, bottom: false, height_ok: true, ceiling: null, arm: {out: null}});
  const tap = await tapUiMessages(browser);
  const watcher = observeModule();
  await watcher.opened;
  try {
    await tap.page.fill('#gHeight', '830');
    await tap.page.click('#btnTarget');
    // 着くまで動く（800 → 830。fake の速さは初期値 100 mm/s）
    await watcher.waitFor((s) => s.goto && s.goto.status === 'MOVING' && s.owner === 'ui', 2000, '移動する');
    const arrived = await watcher.waitFor((s) => s.goto && s.goto.status === 'ARRIVED', 4000, '着く');
    assert.equal(arrived.goto.target_mm, 830);
    // 着いたら送るのをやめる（しばらく待っても追加で送られない）
    const sentBefore = (await tap.sent()).filter((m) => m && m.t === 'goto').length;
    await tap.page.waitForTimeout(3 * HOLD_MS);
    const sentAfter = (await tap.sent()).filter((m) => m && m.t === 'goto').length;
    assert.equal(sentAfter, sentBefore, `着いたあとも goto を送り続けている（${sentBefore} → ${sentAfter}）`);
    // 画面にも「着きました」と出る
    await tap.page.waitForFunction(
      () => document.getElementById('gotoBar').textContent.includes('着きました'), null, {timeout: 3000});
  } finally {
    watcher.close();
    await tap.cleanup();
  }
});

/* --- 11. 下端へで下端に着いてやめる ------------------------------------------ */
test('11 下端へで下端に着いて、下降の送り続けをやめる', async (browser) => {
  await postFake({height_mm: 200, bottom: false, height_ok: true, ceiling: null, arm: {out: null}});
  const tap = await tapUiMessages(browser);
  const watcher = observeModule();
  await watcher.opened;
  try {
    await tap.page.click('#btnBottom');
    await watcher.waitFor((s) => s.owner === 'ui' && s.dir === 'down', 2500, '下降する');
    // 下端に着いたことを偽物の下端スイッチ（`bottom`）に知らせる
    await postFake({bottom: true});
    await watcher.waitFor((s) => s.bottom === true, 5000, '下端に着く');
    // 着いたら下降を送り続けていないこと
    const heldBefore = (await tap.sent()).filter((m) => m && m.t === 'hold' && m.dir === 'down').length;
    await tap.page.waitForTimeout(3 * HOLD_MS);
    const heldAfter = (await tap.sent()).filter((m) => m && m.t === 'hold' && m.dir === 'down').length;
    assert.equal(heldAfter, heldBefore, `下端に着いたあとも下降を送り続けている（${heldBefore} → ${heldAfter}）`);
    await tap.page.waitForFunction(
      () => document.getElementById('gotoBar').textContent.includes('着きました'), null, {timeout: 3000});
    // 別の押し始めで press が違うこと（下端スイッチを離してから）
    await postFake({bottom: false, height_mm: 300});
    await tap.page.fill('#gHeight', '100');
    await tap.page.click('#btnTarget');
    await watcher.waitFor((s) => s.goto && s.goto.status === 'MOVING', 2000, '目標へ動く');
    const gotos = (await tap.sent()).filter((m) => m && m.t === 'goto');
    assert.ok(gotos.length >= 1);
    const holds = (await tap.sent()).filter((m) => m && m.t === 'hold' && m.dir === 'down');
    assert.ok(holds.length >= 1);
    assert.notEqual(gotos[0].press, holds[0].press, '押し始めで press が増えていない');
  } finally {
    watcher.close();
    await tap.cleanup();
  }
});

/* --- 12. 途中で停止 ----------------------------------------------------------- */
test('12 途中で「停止」を押すと release で止まる', async (browser) => {
  await postFake({height_mm: 800, bottom: false, height_ok: true, ceiling: null, arm: {out: null}});
  const tap = await tapUiMessages(browser);
  const watcher = observeModule();
  await watcher.opened;
  try {
    await tap.page.fill('#gHeight', '1400');
    await tap.page.click('#btnTarget');
    await watcher.waitFor((s) => s.goto && s.goto.status === 'MOVING' && s.dir === 'up', 2000, '移動する');
    await tap.page.click('#btnStop');
    // release が送られてすぐ止まる（CMD_TIMEOUT まで動かない。変異 5 の縛り）
    const stopped = await watcher.waitFor((s) => s.dir === 'stop', TIMEOUT_MS - 2 * STATE_MS, '止まる');
    assert.notEqual(stopped.reason, 'CMD_TIMEOUT', '停止を押したのに CMD_TIMEOUT で止まった（release を送っていない）');
    const releases = (await tap.sent()).filter((m) => m && m.t === 'release');
    assert.ok(releases.length >= 1, '停止で release が送られていない');
  } finally {
    watcher.close();
    await tap.cleanup();
  }
});

/* --- 13. 高さが読めない間は目標の高さへを押せない ----------------------------- */
test('13 高さが読めない間は「目標の高さへ」を押せない', async (browser) => {
  await postFake({height_mm: 800, bottom: false, height_ok: false, ceiling: null, arm: {out: null}});
  const {page, cleanup} = await openScreen(browser);
  try {
    await page.waitForFunction(
      () => document.getElementById('btnTarget').disabled === true, null, {timeout: 3000});
    // 下端へは原点を取る前でも使える
    assert.equal(await page.locator('#btnBottom').isEnabled(), true, '高さが読めなくても下端へは押せる');
  } finally {
    await postFake({height_ok: true});
    await cleanup();
  }
});

/* --- 14. 目標が下向きなら下降のスライダーの速さで送る -------------------------- */
test('14 目標が下なら下降の速さで送る', async (browser) => {
  await postFake({height_mm: 800, bottom: false, height_ok: true, ceiling: null, arm: {out: null}});
  const tap = await tapUiMessages(browser);
  const watcher = observeModule();
  await watcher.opened;
  try {
    // 上昇・下降のスライダーの値を別々にし、向きの取り違えを検出できるようにする
    await tap.page.evaluate(() => {
      const set = (id, value) => {
        const el = document.getElementById(id);
        el.value = String(value);
        el.dispatchEvent(new Event('input', {bubbles: true}));
      };
      set('sUp', 40);
      set('sDown', 100);
    });
    await tap.page.fill('#gHeight', '600');
    await tap.page.click('#btnTarget');
    await watcher.waitFor((s) => s.goto && s.goto.status === 'MOVING' && s.dir === 'down', 2000, '下降で動く');
    const gotos = (await tap.sent()).filter((m) => m && m.t === 'goto');
    assert.ok(gotos.length >= 1);
    const last = gotos[gotos.length - 1];
    assert.equal(last.speed, 100, '目標が下なのに下降のスライダーの速さ（100）を送っていない');
  } finally {
    watcher.close();
    await tap.cleanup();
  }
});

/* --- 15. 下端へで止まったら止める（安全の判定） ---------------------------------- */
test('15 下端へで昇降部が止まったら、リトライを止めて「停止」ボタンが消える', async (browser) => {
  await postFake({height_mm: 1200, bottom: false, height_ok: true, stall: true, ceiling: null, arm: {out: null}});
  const tap = await tapUiMessages(browser);
  const watcher = observeModule();
  await watcher.opened;
  try {
    await tap.page.click('#btnBottom');
    // stall が真の間は ENCODER_STALL で止まる。猶予のあと stop を見て（2 回目）止まる
    await watcher.waitFor((s) => s.reason === 'ENCODER_STALL' && s.dir === 'stop', 4000, '止まる');
    await tap.page.waitForFunction(
      () => document.getElementById('btnStop').style.display === 'none' &&
            document.getElementById('gotoBar').textContent.includes('着かなかった'), null, {timeout: 4000});
  } finally {
    watcher.close();
    await postFake({stall: false});
    await tap.cleanup();
  }
});

/* --- 16. 下端へが取って代わられたら終わる -------------------------------------- */
test('16 下端へで上部モジュールが持ち主になったら移動が終わる', async (browser) => {
  await postFake({height_mm: 1200, bottom: false, height_ok: true, ceiling: null, arm: {out: null}});
  const tap = await tapUiMessages(browser);
  const watcher = observeModule();
  await watcher.opened;
  let module = null;
  try {
    await tap.page.click('#btnBottom');
    await watcher.waitFor((s) => s.owner === 'ui' && s.dir === 'down', 2500, '下降する');
    // 別の接続（上部モジュール）が新しい press で持ち主になる
    module = await new Promise((resolve, reject) => {
      const ws = new WebSocket(`ws://${new URL(BASE).host}/ws/module`);
      ws.addEventListener('error', reject);
      ws.addEventListener('open', () => {
        ws.send(JSON.stringify({t: 'hello', name: 'preempt', ceiling_sensor: false}));
        const hold = JSON.stringify({t: 'hold', press: 99, dir: 'down', speed: 40});
        ws.timer = setInterval(() => ws.send(hold), 100);
        resolve(ws);
      });
    });
    // 画面は移動を終わらせて（停止ボタンが消える。「取って代わられた」の理由を出す）
    await tap.page.waitForFunction(
      () => document.getElementById('btnStop').style.display === 'none', null, {timeout: 5000});
    const bar = await tap.page.evaluate(() => document.getElementById('gotoBar').textContent);
    assert.match(bar, /着かなかった|取って代わられた/, `取られた理由が出ていない（${bar}）`);
  } finally {
    if (module) { clearInterval(module.timer); module.close(); }
    watcher.close();
    await tap.cleanup();
  }
});

/* --- 17. WS が切れたら移動中をやめる（繋ぎ直しても送り直さない） ------------------ */
test('17 WS が切れたら移動中をやめる（繋ぎ直しても送り直さない）', async (browser) => {
  await postFake({height_mm: 800, bottom: false, height_ok: true, ceiling: null, arm: {out: null}});
  const page = await browser.newPage({viewport: VIEWPORT});
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    window.__sent = [];
    window.__socks = [];
    const Orig = window.WebSocket;
    window.WebSocket = function (...args) {
      const ws = new Orig(...args);
      window.__socks.push(ws);
      const origSend = ws.send.bind(ws);
      ws.send = (data) => { window.__sent.push(String(data)); return origSend(data); };
      return ws;
    };
    window.WebSocket.prototype = Orig.prototype;
    for (const key of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) window.WebSocket[key] = Orig[key];
  });
  await page.goto(BASE + '/');
  await page.waitForFunction(
    () => document.getElementById('stHeight').textContent.trim() !== '—', null, {timeout: 5000});
  try {
    await page.fill('#gHeight', '1400');
    await page.click('#btnTarget');
    await page.waitForTimeout(3 * HOLD_MS);
    const countAfterStart = await page.evaluate(() =>
      (window.__sent || []).filter((s) => { try { return JSON.parse(s).t === 'goto'; } catch { return false; } }).length);
    assert.ok(countAfterStart >= 2, 'goto が送り続けられていない');
    // 画面の WS を切る。移動中は消え、再接続しても送り直さない
    await page.evaluate(() => { const s = window.__socks[window.__socks.length - 1]; if (s) s.close(); });
    await page.waitForFunction(
      () => document.getElementById('btnStop').style.display === 'none', null, {timeout: 3000});
    await page.waitForTimeout(6 * HOLD_MS);
    const counts = await page.evaluate(() =>
      (window.__sent || []).filter((s) => { try { return JSON.parse(s).t === 'goto'; } catch { return false; } }).length);
    const reOpened = await page.evaluate(() => window.__socks.length);
    assert.ok(reOpened >= 2, 'WS が繋ぎ直されていない');
    // 繋ぎ直した新しい接続が送っているのは何も無い（送り直さない）
    const countLast = await page.evaluate(() =>
      (window.__sent || []).filter((s) => { try { return JSON.parse(s).t === 'goto'; } catch { return false; } }).length);
    assert.equal(countLast, countAfterStart, '切れたあと、新しい接続で goto を送り直している');
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    await page.close().catch(() => {});
  }
});

/* --- 4. 不正値は保存しない ---------------------------------------------------- */
test('4 下限 > 上限は保存されない（PUT を出さない・サーバーの値も変わらない）', async (browser) => {
  const before = await (await fetch(`${BASE}/api/settings`)).json();
  const {page, cleanup} = await openScreen(browser);
  try {
    // 速度スライダーの範囲は設定どおり、**位置は初期値**（上限ではない）
    const slider = await page.evaluate(() => {
      const input = document.getElementById('sUp');
      return {value: input.value, min: input.min, max: input.max};
    });
    assert.equal(slider.min, String(before.settings.lift_up.min), 'スライダーの下限');
    assert.equal(slider.max, String(before.settings.lift_up.max), 'スライダーの上限');
    assert.equal(slider.value, String(before.settings.lift_up.init), 'スライダーの位置は初期値');

    const puts = [];
    page.on('request', (request) => {
      if (request.method() === 'PUT') puts.push(request.url());
    });

    await page.click('#openSettings');
    await page.waitForSelector('#settings.show');
    await page.fill('#stBody tr[data-key="lift_up"] input[data-f="min"]', '70');
    assert.equal(await page.locator('#stSave').isDisabled(), true, '保存ボタンが無効');
    assert.match(await page.locator('#stErr').innerText(), /下限 ≦ 初期値 ≦ 上限/, '理由が出ている');
    await page.locator('#stSave').click({force: true}).catch(() => {}); // 無効でも押してみる
    await page.waitForTimeout(200);
    assert.deepEqual(puts, [], `PUT を送っていない（${JSON.stringify(puts)}）`);

    const after = await (await fetch(`${BASE}/api/settings`)).json();
    assert.deepEqual(after.settings, before.settings, 'サーバーの設定が変わっていない');
  } finally {
    await cleanup();
  }

  // サーバーの検証は最後の砦。強制で送ると 400 で理由を返す
  const forced = await fetch(`${BASE}/api/settings`, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({...before.settings, lift_up: {min: 70, max: 60, init: 30}}),
  });
  assert.equal(forced.status, 400, 'サーバーが 400 を返す');
});

/* --- 5. 名乗り・一覧・腕の表示 ------------------------------------------------ */
test('5 持ち主に busy_label・一覧に繋いだ全部・腕は true のときだけ', async (browser) => {
  // 前の検査（スクロール検査など）の上書きを消してから、arm.out が null の状態にする
  await postFake({height_mm: 1200, bottom: false, height_ok: true, arm: null});
  await postFake({arm: {out: null}});
  const open = (hello, hold) => new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://${new URL(BASE).host}/ws/module`);
    ws.addEventListener('error', reject);
    ws.addEventListener('open', () => {
      ws.send(JSON.stringify(hello));
      if (hold) {
        const msg = JSON.stringify({t: 'hold', press: 1, dir: 'stop', speed: 40});
        ws.send(msg);
        ws.timer = setInterval(() => ws.send(msg), 100);
      }
      resolve(ws);
    });
  });
  const sockets = [
    await open({t: 'hello', name: 'hve-cam', role: 'control', operator: 'human', ceiling_sensor: true}),
    await open({t: 'hello', name: 'ss', role: 'control', operator: 'auto', ceiling_sensor: false,
      busy_label: '画像認識で操作中'}, true),
    await open({t: 'hello', name: 'viewer', role: 'observe', operator: 'auto', ceiling_sensor: false}),
  ];
  const {page, errors, cleanup} = await openScreen(browser);
  const text = (id) => page.evaluate((i) => document.getElementById(i).textContent, id);
  try {
    await page.waitForFunction(() => document.getElementById('stOwner').textContent === '画像認識で操作中',
      null, {timeout: 3000});
    const list = await text('stModule');
    for (const want of ['hve-cam', 'ss', 'viewer', '127.0.0.1', '自動', '人', '状態のみ']) {
      assert.ok(list.includes(want), `一覧に ${want} が無い（${list}）`);
    }
    assert.match(await text('bClients'), /上部 3/, '上の帯の上部モジュールの数');
    // arm.out が null の間は出ない。false でも出ない。true で出る
    assert.equal(await page.locator('#armNote.show').count(), 0, 'null で腕が出ています');
    await postFake({arm: {out: false}});
    await page.waitForTimeout(3 * STATE_MS);
    assert.equal(await page.locator('#armNote.show').count(), 0, 'false で腕が出ています');
    await postFake({arm: {out: true}});
    await page.waitForFunction(() => document.getElementById('armNote').textContent === '腕が出ています',
      null, {timeout: 3000});
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    for (const ws of sockets) { clearInterval(ws.timer); ws.close(); }
    await postFake({arm: {out: null}});
    await cleanup();
  }
});

/** 距離計を持つ上部モジュール（出どころ）として繋ぎ、天井の値を送り続ける */
function openSource(ceiling) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://${new URL(BASE).host}/ws/module`);
    ws.addEventListener('error', reject);
    ws.addEventListener('open', () => {
      ws.send(JSON.stringify({t: 'hello', name: 'hve-cam', role: 'control', operator: 'human',
        ceiling_sensor: true}));
      const msg = JSON.stringify({t: 'ceiling', ...ceiling});
      ws.send(msg);
      ws.timer = setInterval(() => ws.send(msg), 100);
      resolve(ws);
    });
  });
}

/* --- 6. 単体の操作の天井の表示と設定 ------------------------------------------ */
test('6 天井のバッジに使わない理由が出る・設定の切り替えが PUT に載る', async (browser) => {
  await postFake({height_mm: 1200, bottom: false, ceiling: null, arm: {out: null}});
  const {page, errors, cleanup} = await openScreen(browser);
  const badge = () => page.evaluate(() => document.getElementById('bCeil').textContent.trim());
  const waitBadge = (want) => page.waitForFunction(
    (w) => document.getElementById('bCeil').textContent.includes(w), want, {timeout: 3000});
  let source = null;
  try {
    await waitBadge('天井: 使わない（通信なし）');
    source = await openSource({status: 'MEASURED', mm: 2000, age_ms: 0});
    await waitBadge('天井 2000 mm');

    const puts = [];
    page.on('request', (request) => {
      if (request.method() === 'PUT') puts.push(request.postData());
    });
    await page.click('#openSettings');
    await page.waitForSelector('#settings.show');
    const box = page.locator('#stBody input[data-axis="ceiling_standalone"]');
    assert.equal(await box.isChecked(), true, '既定は入');
    await box.uncheck();
    await page.click('#stSave');
    await waitBadge('天井: 使わない（設定が切）');
    assert.equal(puts.length, 1, `PUT が 1 回（${JSON.stringify(puts)}）`);
    assert.equal(JSON.parse(puts[0]).ceiling_standalone, false, 'PUT に ceiling_standalone: false が載る');
    const saved = await (await fetch(`${BASE}/api/settings`)).json();
    assert.equal(saved.settings.ceiling_standalone, false, 'サーバーに保存された');

    await page.click('#openSettings');
    await page.waitForSelector('#settings.show');
    assert.equal(await box.isChecked(), false, '開き直すと切のまま');
    await box.check();
    await page.click('#stSave');
    await waitBadge('天井 2000 mm');
    assert.equal(JSON.parse(puts[1]).ceiling_standalone, true, '入に戻す PUT');
    assert.ok((await badge()).length > 0);
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    if (source) { clearInterval(source.timer); source.close(); }
    const now = await (await fetch(`${BASE}/api/settings`)).json();
    await fetch(`${BASE}/api/settings`, {method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...now.settings, ceiling_standalone: true})});
    await cleanup();
  }
});

/* --- 7. 画面の上昇が天井で止まる -------------------------------------------- */
test('7 画面の上昇は近い天井で止まり、出どころが切れると天井を使わずに上がる', async (browser) => {
  await postFake({height_mm: 1200, bottom: false, ceiling: null, arm: {out: null}});
  const {page, errors, cleanup} = await openScreen(browser);
  const watcher = observeModule();
  await watcher.opened;
  let source = await openSource({status: 'MEASURED', mm: 100, age_ms: 0});
  try {
    await page.waitForFunction(() => document.getElementById('bCeil').textContent.includes('100 mm'),
      null, {timeout: 3000});
    await pressUp(page);
    const stopped = await watcher.waitFor((s) => s.owner === 'ui' && s.reason === 'CEILING_NEAR',
      1500, '近い天井で止まる');
    assert.equal(stopped.dir, 'stop');
    await page.mouse.up();
    // 出どころが切れた（通信なし）→ 天井を使わずに上がる。CEILING_STALE で止めない
    clearInterval(source.timer); source.close(); source = null;
    await page.waitForFunction(
      () => document.getElementById('bCeil').textContent.includes('通信なし'), null, {timeout: 3000});
    await pressUp(page);
    await watcher.waitFor((s) => s.owner === 'ui' && s.dir === 'up', 1500, '出どころが無いと上がる');
    await page.mouse.up();
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    if (source) { clearInterval(source.timer); source.close(); }
    watcher.close();
    await cleanup();
  }
});

/* --- 8. 天井の閾値の設定 ------------------------------------------------------ */
test('8 天井の閾値は範囲の外で PUT を出さず、保存すると PUT に載って上昇の判定に効く', async (browser) => {
  await postFake({height_mm: 1200, bottom: false, ceiling: null, arm: {out: null}});
  const {page, errors, cleanup} = await openScreen(browser);
  const watcher = observeModule();
  await watcher.opened;
  let source = null;
  const putMargin = (mm) => fetch(`${BASE}/api/settings`).then((r) => r.json()).then((now) =>
    fetch(`${BASE}/api/settings`, {method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...now.settings, ceiling_margin_mm: mm})}));
  try {
    const puts = [];
    page.on('request', (request) => {
      if (request.method() === 'PUT') puts.push(request.postData());
    });
    await page.click('#openSettings');
    await page.waitForSelector('#settings.show');
    const input = page.locator('#stBody input[data-axis="ceiling_margin_mm"]');
    assert.equal(await input.inputValue(), '500', '既定は 500');
    await input.fill('149');
    assert.equal(await page.locator('#stSave').isDisabled(), true, '149 は保存できない');
    await page.locator('#stSave').click({force: true}).catch(() => {});
    assert.equal(puts.length, 0, `範囲の外では PUT を出さない（${JSON.stringify(puts)}）`);
    await input.fill('2500');
    await page.click('#stSave');
    await page.waitForSelector('#settings.show', {state: 'hidden'});
    assert.equal(puts.length, 1, `PUT が 1 回（${JSON.stringify(puts)}）`);
    assert.equal(JSON.parse(puts[0]).ceiling_margin_mm, 2500, 'PUT に ceiling_margin_mm: 2500 が載る');
    const saved = await (await fetch(`${BASE}/api/settings`)).json();
    assert.equal(saved.settings.ceiling_margin_mm, 2500, 'サーバーに保存された');

    await page.click('#openSettings');
    await page.waitForSelector('#settings.show');
    assert.equal(await input.inputValue(), '2500', '開き直すと反映されている');
    await page.click('#stCancel').catch(async () => { await page.keyboard.press('Escape'); });

    // 2000 mm の天井は、閾値 2500 なら近い（既定 500 なら通る）
    source = await openSource({status: 'MEASURED', mm: 2000, age_ms: 0});
    await page.waitForFunction(() => document.getElementById('bCeil').textContent.includes('2000 mm'),
      null, {timeout: 3000});
    await pressUp(page);
    const stopped = await watcher.waitFor((s) => s.owner === 'ui' && s.reason === 'CEILING_NEAR',
      1500, '閾値 2500 で 2000 mm の天井に止まる');
    assert.equal(stopped.dir, 'stop');
    await page.mouse.up();
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    if (source) { clearInterval(source.timer); source.close(); }
    watcher.close();
    await putMargin(500);
    await cleanup();
  }
});

/** アームの制御器（role: arm）として繋ぎ、`arm` を 100 ms ごとに送る（stop() で止める） */
async function openArm(out) {
  const ws = await new Promise((resolve, reject) => {
    const socket = new WebSocket(`ws://${new URL(BASE).host}/ws/module`);
    socket.addEventListener('error', reject);
    socket.addEventListener('open', () => {
      socket.send(JSON.stringify({t: 'hello', name: 'fk-arm', role: 'arm', operator: 'auto', ceiling_sensor: false}));
      resolve(socket);
    });
  });
  const send = () => ws.send(JSON.stringify({t: 'arm', out}));
  send();
  ws.timer = setInterval(send, 100);
  ws.stop = () => { clearInterval(ws.timer); ws.timer = null; };
  ws.resume = () => { if (!ws.timer) { send(); ws.timer = setInterval(send, 100); } };
  return ws;
}

test('9 測定機構の確認ダイアログ・原点の前の腕の警告（render の呼び出し側）', async (browser) => {
  await postFake({height_mm: 1200, bottom: false, height_ok: true, ceiling: null, arm: null});
  const arm = await openArm(true);
  const tap = await tapUiMessages(browser);
  const {page} = tap;
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const dialogShown = () => page.locator('#armConfirm.show').count();
  const waitDialog = (shown, label) => page.waitForFunction(
    (want) => document.getElementById('armConfirm').classList.contains('show') === want,
    shown, {timeout: 4000}).catch(() => { throw new Error(`${label}: ダイアログが ${shown ? '出ない' : '消えない'}`); });
  const confirms = async () => (await tap.sent()).filter((m) => m && m.t === 'arm_confirm');
  const watcher = observeModule();
  await watcher.opened;
  try {
    // 通信できているあいだは出ない。測定機構の表示は通信中・腕が出ている
    await page.waitForFunction(() => document.getElementById('stArm').textContent.includes('腕が出ている'),
      null, {timeout: 3000});
    assert.equal(await dialogShown(), 0, '通信できているのにダイアログが出ている');

    // 通信が切れる（arm が来なくなる）。ダイアログが出て、止めた理由の帯も出る
    arm.stop();
    await waitDialog(true, '1 回目の #4e');
    const first = (await watcher.waitFor((s) => s.reason === 'ARM_LINK', 3000, '#4e')).arm.confirm;
    assert.ok(Number.isInteger(first));
    assert.match(await page.evaluate(() => document.getElementById('stopbar').textContent), /測定機構と通信できない/);

    // 通信が戻ると、答えなくてもダイアログが消える（confirm が null のとき残さない）
    arm.resume();
    await waitDialog(false, '通信が戻ったあと');

    // 2 回目の止め: 番号が増え、答えは**いまの番号**を載せる（前の番号では昇降部が捨てる）
    arm.stop();
    await waitDialog(true, '2 回目の #4e');
    const second = (await watcher.waitFor((s) => s.reason === 'ARM_LINK' && s.arm.confirm !== first,
      3000, '2 回目の番号')).arm.confirm;
    assert.ok(second > first, `番号が増えていない（${first} → ${second}）`);
    await page.click('#acOk');
    await waitDialog(false, '答えたあと');
    const sent = await confirms();
    assert.deepEqual(sent, [{t: 'arm_confirm', stop: second}], '答えが載せた番号');
    await watcher.waitFor((s) => s.reason !== 'ARM_LINK' && s.arm.confirm === null, 3000, '答えたあとの昇降部');

    // 原点の前の警告: 高さが読めず・通信できて・腕が出ている、の 3 つが揃ったときだけ
    arm.resume();
    await watcher.waitFor((s) => s.arm.link === true && s.arm.out === true, 3000, 'arm が戻る');
    const note = () => page.evaluate(() => document.getElementById('armNote').textContent);
    await postFake({height_ok: true});
    await page.waitForFunction(() => document.getElementById('armNote').textContent === '腕が出ています', null, {timeout: 3000});
    await postFake({height_ok: false});
    await page.waitForFunction(() => document.getElementById('armNote').textContent.includes('下げる前'), null, {timeout: 3000});
    // 通信できない（link false）・腕が分からない（out null）では原点の前の警告にしない
    await postFake({arm: {link: false}});
    await page.waitForFunction(() => !document.getElementById('armNote').textContent.includes('下げる前'), null, {timeout: 3000});
    await postFake({arm: {link: true, out: null}});
    await page.waitForFunction(() => document.getElementById('armNote').textContent === '', null, {timeout: 3000});
    assert.equal(await note(), '', 'out が null で警告が出ている');
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    // 後の検査に #4e を残さない: 格納位置と知らせて覚えを下ろしてから閉じる
    arm.stop();
    if (arm.readyState === WebSocket.OPEN) arm.send(JSON.stringify({t: 'arm', out: false}));
    await new Promise((resolve) => setTimeout(resolve, 3 * STATE_MS));
    arm.close();
    watcher.close();
    await postFake({height_ok: true, arm: null});
    await tap.cleanup();
  }
});

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome',
    args: ['--no-sandbox'],
  });
  let failed = 0;
  for (const {name, body} of tests) {
    try {
      await body(browser);
      console.log(`OK ${name}`);
    } catch (error) {
      failed += 1;
      console.log(`NG ${name}`);
      console.log(`   ${String(error.message).split('\n').join('\n   ')}`);
    }
  }
  await browser.close();
  console.log(`${tests.length - failed}/${tests.length} OK`);
  process.exit(failed ? 1 : 0);
})();
