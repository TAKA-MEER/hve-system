// 使い方: node firmware/lift/web/tests/check_noscroll_lift.js <http://...>
//   例: node firmware/lift/web/tests/check_noscroll_lift.js http://127.0.0.1:18080/
//   playwright が require できないときは PLAYWRIGHT_PATH=<.../node_modules/playwright> を付ける。
//   ブラウザは既定で /usr/bin/google-chrome（CHROME_PATH で変更）。
//
// camera/web/tests/check_noscroll_web.js を昇降部の画面向けに作り直したもの。
// 状態の作り方は POST /api/fake で偽物を操作する。
// 17 の画面サイズ × 4 状態（通常・下端・設定画面・上部モジュール 3 つと腕）で、
// ページ・要素のスクロールと中身のはみ出し、操作部品の画面外・カード外・小さすぎを検出する。
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');

const target = process.argv[2] || 'http://127.0.0.1:18080/';
const base = target.replace(/\/$/, '');

const sizes = [
  [360, 640], [375, 667], [390, 844], [430, 932], [768, 1024], [810, 1080], [1024, 1366],
  [640, 360], [667, 375], [844, 390], [932, 430], [1024, 600], [1024, 768], [1280, 800],
  [1366, 768], [1920, 1080], [2560, 1440],
];

// 状態は偽物 API（POST /api/fake）で作る。実装の画面は URL 引数では状態を切り替えない
const states = [
  {name: '通常', fake: {height_mm: 1200, bottom: false, ceiling: {status: 'MEASURED', mm: 2200}}},
  {name: '下端', fake: {height_mm: 0, bottom: true, ceiling: {status: 'MEASURED', mm: 2200}}},
  {name: '設定画面', fake: {height_mm: 1200, bottom: false, ceiling: {status: 'MEASURED', mm: 2200}}, settings: true},
  // 上部モジュールを 3 つ繋ぎ（一覧が長くなる）、1 つが持ち主として長い busy_label を出し、腕が出ている
  {name: '上部3つ・腕', fake: {height_mm: 1200, bottom: false, ceiling: {status: 'MEASURED', mm: 2200}, arm: {out: true}},
    modules: [
      {hello: {t: 'hello', name: 'hve-cam', role: 'control', operator: 'human', ceiling_sensor: true}},
      {hello: {t: 'hello', name: 'viewer', role: 'observe', operator: 'auto', ceiling_sensor: false}},
      {hello: {t: 'hello', name: 'ss', role: 'control', operator: 'auto', ceiling_sensor: false,
        busy_label: '画像認識で操作中の長い表示'}, owner: true},
    ]},
];

// 上部モジュールとして繋ぐ（画面の持ち主・一覧の表示を出す）。owner は stop の hold を送り続けて持ち主になる
async function connectModules(modules) {
  const sockets = [];
  for (const m of modules) {
    const ws = new WebSocket(`ws://${new URL(base).host}/ws/module`);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve);
      ws.addEventListener('error', () => reject(new Error('/ws/module に繋げない')));
    });
    ws.send(JSON.stringify(m.hello));
    if (m.owner) {
      const hold = JSON.stringify({t: 'hold', press: 1, dir: 'stop', speed: 40});
      ws.send(hold);
      const timer = setInterval(() => ws.send(hold), 100);
      ws.addEventListener('close', () => clearInterval(timer));
    }
    sockets.push(ws);
  }
  return sockets;
}

async function setFake(fake) {
  const response = await fetch(`${base}/api/fake`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(fake),
  });
  if (!response.ok) throw new Error(`POST /api/fake が ${response.status}（偽物を作れない）`);
}

// モックアップの検査と同じ判定
const MEASURE = () => {
  const bad = [];
  const de = document.documentElement;
  if (de.scrollHeight > innerHeight + 1 || de.scrollWidth > innerWidth + 1) {
    bad.push(`page ${de.scrollWidth}x${de.scrollHeight}`);
  }
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none') continue;
    if (/(auto|scroll)/.test(cs.overflowY + cs.overflowX)
        && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) {
      bad.push('scrollable ' + (el.id || el.className));
    }
    // overflow:clip はスクロール領域にならない「意図した切り取り」なので対象外
    if (cs.overflowX === 'clip' && cs.overflowY === 'clip') continue;
    if (!['INPUT', 'HTML', 'BODY', 'SCRIPT', 'STYLE', 'HEAD'].includes(el.tagName)
        && el.clientHeight > 0
        && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) {
      bad.push(`overflow ${el.tagName}.${el.id || el.className} ${el.scrollWidth}x${el.scrollHeight}>${el.clientWidth}x${el.clientHeight}`);
    }
  }
  const must = [...document.querySelectorAll('.hold, input[type=range], #openSettings')];
  const overlay = document.querySelector('.overlay.show');
  if (overlay) must.push(...overlay.querySelectorAll('input, button'));
  for (const el of must) {
    const rc = el.getBoundingClientRect();
    const name = el.id || el.dataset.dir || el.dataset.f || el.textContent.trim().slice(0, 8);
    if (rc.top < -0.5 || rc.left < -0.5 || rc.bottom > innerHeight + 0.5 || rc.right > innerWidth + 0.5) {
      bad.push('offscreen ' + name);
    }
    const box = el.closest('.card, .sheet');
    if (box) {
      const bc = box.getBoundingClientRect();
      if (rc.bottom > bc.bottom + 0.5 || rc.right > bc.right + 0.5) bad.push('clipped ' + name);
    }
    if (el.classList.contains('hold') && (rc.height < 32 || rc.width < 32)) {
      bad.push(`small ${name} ${rc.width | 0}x${rc.height | 0}`);
    }
  }
  return bad;
};

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome'});
  let fail = 0;
  let n = 0;
  for (const [w, h] of sizes) {
    for (const state of states) {
      await setFake(state.fake);
      const sockets = await connectModules(state.modules || []);
      const page = await browser.newPage({viewport: {width: w, height: h}});
      await page.goto(base + '/');
      // state が来て、表示が落ち着くまで待つ
      await page.waitForFunction(() => document.getElementById('stHeight').textContent.trim() !== '—', null, {timeout: 5000});
      if (state.settings) {
        await page.click('#openSettings');
        await page.waitForSelector('#settings.show');
      }
      await page.waitForTimeout(120);
      const bad = await page.evaluate(MEASURE);
      n += 1;
      if (bad.length) {
        fail += 1;
        console.log(`NG ${w}x${h} ${state.name}: ${[...new Set(bad)].join(', ')}`);
      }
      await page.close();
      for (const ws of sockets) ws.close();
    }
  }
  console.log(`${n - fail}/${n} OK`);
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch((error) => {
  console.error('検査が例外で止まった', error);
  process.exit(1);
});
