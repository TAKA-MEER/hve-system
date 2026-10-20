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
