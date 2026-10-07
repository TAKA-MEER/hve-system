// 使い方: node camera/web/tests/browser_paths.js [http://127.0.0.1:18000/]
//   playwright が require できないときは PLAYWRIGHT_PATH=<.../node_modules/playwright> を付ける。
//   ブラウザは既定で /usr/bin/google-chrome（CHROME_PATH で変更）。
//   先に `python -m hve_camera --fake --port 18000` を立てておく。
//
// ブラウザの「経路」を通す試験（brief WP-UI-01 §2 試験）。
// 1. 上昇ボタンを押している間 state.lift.dir が up、離すと stop に戻る
// 2. 上昇ボタンを押したままページを閉じると、別の画面から見て昇降が止まる
// 3. 設定で不正値（下限 > 上限）を入れると保存されない
// 4. ズームの「＋」で state.zoom が 1.5 になり、映像の <img> の大きさは変わらない
// 5. 映像が無いときと途絶えたとき「映像がありません」が見え、壊れた画像（アイコン・alt 文字）が見えない
//    ※5 は `hve_video` を立てて**いない**ときに始める（試験の中で立てたり止めたりする）
// 純関数の試験（web.test.js）ではこの 4 本の経路は縛れないので、ブラウザで通す。
// 6〜9: 上端の検知の表示・IO_LOST の文言・静止画（WP-UI-02）。
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');

const BASE = (process.argv[2] || 'http://127.0.0.1:18000/').replace(/\/$/, '');
const PORT = Number(new URL(BASE).port || 80);
const VIEWPORT = {width: 900, height: 500};

// 画面側とカメラ部の時間（names §5 と camera/config/params.toml）。
// HOLD_MS だけ送らないとカメラ部が TIMEOUT_MS で止めるので、「押し続けた」ことの境目になる
const HOLD_MS = 100;
const TIMEOUT_MS = 400;
const STATE_MS = 100;

async function postFake(payload) {
  const response = await fetch(`${BASE}/api/fake`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`POST /api/fake が ${response.status}: ${await response.text()}`);
}

/** 上昇できる状態へそろえる（天井の ok が false のあいだは上昇しない） */
async function allowUp(height = 1200) {
  await postFake({ceiling: {status: 'MEASURED', mm: 2600}, height_mm: height, bottom: false});
}

/**
 * 偽物の天井は `POST` した値のあともう測らないので、`ceiling_stale_ms`（600 ms）を
 * 越えると「値なし」になる。実物のセンサーが続けて値を返すのと同じ状況を作って、
 * 試験しているあいだは上昇の許可を保つ。
 */
function keepCeiling() {
  const timer = setInterval(() => {
    postFake({ceiling: {status: 'MEASURED', mm: 2600}}).catch(() => {});
  }, 150);
  timer.unref();
  return () => clearInterval(timer);
}

/** 画面の向こう側として状態を観察する WS */
function observe() {
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/ws`);
  let latest = null;
  const opened = new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve);
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
      throw new Error(
        `${label}: ${ms} ms 以内に条件を満たす state が来なかった（最新 ${JSON.stringify(latest && {
          clients: latest.clients, active_axis: latest.active_axis,
          dir: latest.lift && latest.lift.dir, reason: latest.reason, zoom: latest.zoom,
        })}）`
      );
    },
    close: () => ws.close(),
  };
}

/** 画面を開く。state が来たのを待って、JavaScript のエラーも拾っておく */
async function openScreen(browser) {
  const page = await browser.newPage({viewport: VIEWPORT});
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(BASE + '/');
  await page.waitForFunction(
    () => document.getElementById('bClients').textContent.trim() !== '端末 —',
    null,
    {timeout: 5000}
  );
  // 失敗しても「押したままの画面」を残さないようにする
  const cleanup = async () => {
    await page.mouse.up().catch(() => {});
    await page.close().catch(() => {});
  };
  return {page, errors, cleanup};
}

/** 上昇のボタンで押す（pointerdown だけ。離さない） */
async function pressUp(page) {
  const box = await page.locator('[data-axis=lift_up]').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
}

// 映像（`hve_video`）は別プロセス。試験の中で出し入れして「来た / 途絶えた」を作る
const {spawn} = require('node:child_process');
const net = require('node:net');
const path = require('node:path');
const PYTHON = process.env.PYTHON || path.resolve(__dirname, '../../../.venv/bin/python');
const VIDEO_MS = 20000;

function waitPort(port, timeout_ms) {
  /** ポートが返事を始めるまで待つ（`hve_video` の準備を待つ）。 */
  const deadline = Date.now() + timeout_ms;
  return new Promise((resolve, reject) => {
    const probe = () => {
      const socket = net.connect(port, '127.0.0.1');
      socket.once('connect', () => {socket.destroy(); resolve();});
      socket.once('error', () => {
        socket.destroy();
        if (Date.now() > deadline) {
          reject(new Error(`ポート ${port} が ${timeout_ms} ms 内に開かなかった`));
          return;
        }
        setTimeout(probe, 100);
      });
    };
    probe();
  });
}

async function startVideo(port) {
  /** 偽の映像を配信し始める。 */
  // `hve_video` は入れていない（`pyproject.toml` §packages）ので `camera/` で起動する
  const child = spawn(PYTHON, ['-m', 'hve_video', '--fake', '--port', String(port)], {
    cwd: path.resolve(__dirname, '../../../camera'),
    stdio: 'ignore',
  });
  try {
    await waitPort(port, VIDEO_MS);
  } catch (err) {
    child.kill();
    throw err;
  }
  return child;
}

async function stopVideo(child) {
  /** 配信を止める（途絶えた状態を作る）。 */
  const gone = new Promise((resolve) => child.once('exit', resolve));
  child.kill();
  await gone;
}

const tests = [];
const test = (name, body) => tests.push({name, body});

/* --- 1. 押している間だけ動く ------------------------------------------------- */
test('1 上昇を押している間 up、離すと stop に戻る', async (browser) => {
  await allowUp();
  const stopCeiling = keepCeiling();
  const {page, errors, cleanup} = await openScreen(browser);
  const watcher = observe();
  await watcher.opened;
  try {
    await pressUp(page);
    await watcher.waitFor((s) => s.lift.dir === 'up' && s.active_axis === 'lift_up', 1000, '押した直後に上昇');

    // 定期送信の確認。1 回しか送らないとカメラ部が hold_timeout_ms で止めるので、
    // 6 周期ぶん（600 ms）見て、指を離すまでずっと上昇のままであることを確かめる
    const started = Date.now();
    const samples = [];
    while (Date.now() - started < 6 * HOLD_MS) {
      const state = watcher.latest();
      samples.push({
        at: Date.now() - started,
        dir: state.lift.dir,
        active: state.active_axis,
        reason: state.reason,
      });
      await page.waitForTimeout(HOLD_MS / 2);
    }
    const held = samples.filter((s) => s.at >= 2 * HOLD_MS);
    assert.ok(held.length >= 4, `定期送信: サンプルが少ない（${JSON.stringify(samples)}）`);
    assert.deepEqual([...new Set(held.map((s) => s.dir))], ['up'],
      `押している間はすべて up（${JSON.stringify(samples)}）`);
    assert.deepEqual([...new Set(held.map((s) => s.active))], ['lift_up']);

    await page.mouse.up();
    // 離したらすぐ放す。hold_timeout_ms（400 ms）までに放せなければ release を送っていない
    await watcher.waitFor((s) => s.active_axis === null, TIMEOUT_MS - 2 * STATE_MS, '離した直後に active_axis が消える');
    const stopped = await watcher.waitFor((s) => s.lift.dir === 'stop', TIMEOUT_MS, '離した直後に止まる');
    assert.notEqual(stopped.reason, 'HOLD_TIMEOUT', '離したのに HOLD_TIMEOUT で止まった');
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    watcher.close();
    stopCeiling();
    await cleanup();
  }
});

/* --- 2. 押したままページを閉じた ---------------------------------------------- */
test('2 押したままページを閉じると別の画面から見て止まる', async (browser) => {
  await allowUp();
  const stopCeiling = keepCeiling();
  const {page, cleanup} = await openScreen(browser);
  const watcher = observe();
  await watcher.opened;
  try {
    await pressUp(page);
    await watcher.waitFor((s) => s.lift.dir === 'up', 1000, '押しているあいだ上昇');
    await page.close(); // 離さずに閉じる

    await watcher.waitFor((s) => s.active_axis === null, 1500, 'ページを閉じた後に active_axis が消える');
    const stopped = await watcher.waitFor((s) => s.lift.dir === 'stop', 1500, 'ページを閉じた後に止まる');
    assert.notEqual(stopped.active_axis, 'lift_up');
    assert.equal(watcher.latest().clients, 1, '閉じた画面の WS だけが切れている');
  } finally {
    watcher.close();
    stopCeiling();
    await page.close().catch(() => {});
  }
});

/* --- 3. 不正値は保存しない ---------------------------------------------------- */
test('3 下限 > 上限は保存されない（PUT を出さない・サーバーの値も変わらない）', async (browser) => {
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

/* --- 4. ズームはカメラ部だけ -------------------------------------------------- */
test('4 ズームの「＋」で state.zoom が 1.5、<img> の大きさは変わらない', async (browser) => {
  const {page, errors, cleanup} = await openScreen(browser);
  const watcher = observe();
  try {
    // 新しい WS がつながると倍率は 1 に戻るので、「＋」の前に別の画面でつないでから押す
    await watcher.opened;
    await watcher.waitFor((s) => s.zoom === 1, 2000, '最初は 1 倍');

    const image = page.locator('#videoImage');
    assert.match(await image.getAttribute('src'), /\/stream$/, '映像は /stream を向いている');
    // フレームが来ないあいだ `<img>` は隠れているので、DOM の矩形で測る
    const imageRect = () => page.evaluate(() => {
      const rect = document.getElementById('videoImage').getBoundingClientRect();
      return {width: rect.width, height: rect.height};
    });
    const before = await imageRect();

    await page.click('#zIn');
    const zoomed = await watcher.waitFor((s) => s.zoom === 1.5, 2000, '＋ で 1.5 倍');
    assert.equal(zoomed.zoom, 1.5);
    assert.equal((await page.locator('#zLvl').innerText()).trim(), '1.5×', '画面に出る倍率は state と同じ');

    const after = await imageRect();
    assert.equal(Math.round(after.width), Math.round(before.width),
      `<img> の幅が変わった（${before.width}→${after.width}）`);
    assert.equal(Math.round(after.height), Math.round(before.height),
      `<img> の高さが変わった（${before.height}→${after.height}）`);
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    watcher.close();
    await cleanup();
  }
});

/* --- 5. 映像が無いとき / 途絶えたとき --------------------------------------- */
test('5 映像が無いときと途絶えたとき「映像がありません」が見え、壊れた画像が見えない',
  async (browser) => {
    const {page, errors, cleanup} = await openScreen(browser);
    let video = null;
    const nosig = page.locator('#nosig');
    const image = page.locator('#videoImage');
    try {
      // (1) 映像が無いとき: 壊れた画像のアイコンと alt の文字を見せない
      await nosig.waitFor({state: 'visible', timeout: 5000});
      assert.equal((await nosig.innerText()).trim(), '映像がありません');
      assert.equal(await image.getAttribute('alt'), 'カメラ映像', 'alt は読み上げのために残す');
      assert.equal(await image.isVisible(), false, '壊れた <img> が見える');
      assert.equal(
        await page.evaluate(() => getComputedStyle(document.getElementById('videoImage')).visibility),
        'hidden');
      // 隠すのは `<img>` だけ（高さ・ピッチの OSD は出す）
      assert.equal(await page.locator('#osdH').isVisible(), true, '高さの OSD が見えない');
      assert.equal(await page.locator('#osdP').isVisible(), true, 'ピッチの OSD が見えない');

      // (2) 映像を配り始めたら出す（`<img>` だけ）。案内は消す
      const port = Number(new URL(await image.getAttribute('src')).port);
      video = await startVideo(port);
      await image.waitFor({state: 'visible', timeout: VIDEO_MS});
      assert.equal(await nosig.isVisible(), false, '映像が来ても案内が残る');

      // (3) 途絶えたら戻す（取り直しは `video_probe_ms` ごと）
      await stopVideo(video);
      video = null;
      await nosig.waitFor({state: 'visible', timeout: VIDEO_MS});
      assert.equal(await image.isVisible(), false, '途絶えても壊れた <img> が見える');
      assert.deepEqual(errors, [], '画面に JavaScript のエラー');
    } finally {
      if (video !== null) await stopVideo(video);
      await cleanup();
    }
  });

/* --- 6. 上端の検知・IO_LOST・静止画 ------------------------------------------ */
test('6 top_detect が false のあいだ「上端の検知: 一時無効」が見える（偽の昇降部は false）', async (browser) => {
  const {page, errors, cleanup} = await openScreen(browser);
  try {
    const badge = page.locator('#bTop');
    await badge.waitFor({state: 'visible', timeout: 3000});
    assert.equal((await badge.innerText()).trim(), '上端の検知: 一時無効');
    // 持ち主・昇降部の画面の数も state から出ている（偽の昇降部は持ち主なし・0 台）
    assert.match(await page.locator('#bOwner').innerText(), /^持ち主 /);
    assert.equal((await page.locator('#bLiftUi').innerText()).trim(), '昇降部の画面 0');
    // 偽の昇降部は IP を持たないのでリンクは出さない
    assert.equal(await page.locator('#bLiftLink').isVisible(), false);
    assert.deepEqual(errors, []);
  } finally {
    await cleanup();
  }
});

test('7 IO_LOST のとき停止の帯に文言が出る', async (browser) => {
  await postFake({io_lost: true});
  const {page, cleanup} = await openScreen(browser);
  try {
    const bar = page.locator('#stopbar');
    await bar.waitFor({state: 'visible', timeout: 3000});
    assert.equal((await bar.innerText()).trim(),
      'Arduino から応答がありません。ヨー・ピッチ・天井の測定が使えません');
  } finally {
    await postFake({io_lost: false});
    await cleanup();
  }
});

// 1×1 の PNG（静止画の代わりに返す）
const PNG_1PX = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64');

test('8 静止画のボタンで :8080/snapshot を取り寄せ、重ねて出し、閉じると戻る', async (browser) => {
  const {page, errors, cleanup} = await openScreen(browser);
  const requests = [];
  try {
    const port = await page.evaluate(() => Number(new URL(document.getElementById('videoImage').src).port));
    await page.route('**/snapshot', (route) => {
      requests.push(route.request().url());
      route.fulfill({status: 200, contentType: 'image/png', body: PNG_1PX});
    });
    assert.equal(await page.locator('#snapshot').isVisible(), false, '押す前は出ていない');
    await page.click('#snapBtn');
    await page.locator('#snapshot').waitFor({state: 'visible', timeout: 3000});
    await page.waitForFunction(() => document.getElementById('snapImage').complete
      && document.getElementById('snapImage').naturalWidth > 0, null, {timeout: 3000});
    assert.equal(requests.length, 1, `/snapshot を 1 回取り寄せた（${JSON.stringify(requests)}）`);
    const url = new URL(requests[0]);
    assert.equal(url.port, '8080', 'ポートは 8080');
    assert.equal(Number(url.port), port, '映像と同じポート');
    assert.equal(url.pathname, '/snapshot');
    assert.equal(url.hostname, new URL(BASE).hostname, '宿主は画面と同じ');
    assert.equal(await page.locator('#snapImage').isVisible(), true, '静止画が見える');
    // 映像の枠の中に重なっている
    const inside = await page.evaluate(() => document.getElementById('video').contains(document.getElementById('snapshot')));
    assert.equal(inside, true, '映像の上に重ねている');
    // 表示中も操作できる（昇降のボタンが映像の外で押せる）
    assert.equal(await page.locator('[data-axis=lift_up]').isVisible(), true);

    await page.click('#snapClose');
    await page.locator('#snapshot').waitFor({state: 'hidden', timeout: 3000});
    assert.deepEqual(errors, [], '画面に JavaScript のエラー');
  } finally {
    await cleanup();
  }
});

test('9 静止画が取れないときは案内を出す（壊れた画像を見せない）', async (browser) => {
  const {page, cleanup} = await openScreen(browser);
  try {
    await page.route('**/snapshot', (route) => route.fulfill({status: 503, body: ''}));
    await page.click('#snapBtn');
    await page.locator('#snapMsg').waitFor({state: 'visible', timeout: 3000});
    assert.equal((await page.locator('#snapMsg').innerText()).trim(), '静止画を取得できません');
    assert.equal(await page.locator('#snapImage').isVisible(), false);
  } finally {
    await cleanup();
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
