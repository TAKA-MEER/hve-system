// 使い方: node camera/web/tests/check_noscroll_web.js <http://...>
//   例: node camera/web/tests/check_noscroll_web.js http://127.0.0.1:18000/
//   playwright が require できないときは PLAYWRIGHT_PATH=<.../node_modules/playwright> を付ける。
//   ブラウザは既定で /usr/bin/google-chrome（CHROME_PATH で変更）。
//
// docs/plan/spec/mockup/check_noscroll.js を、実装の画面（モックアップではない）向けに
// 作り直したもの。状態の作り方は URL 引数ではなく POST /api/fake で偽物を操作する。
// 17 の画面サイズ × 4 状態（通常・天井の値なし・設定画面・静止画の重ね表示）で、
// ページ・要素のスクロールと中身のはみ出し、操作部品の画面外・カード外・小さすぎを検出する。
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');

const target = process.argv[2] || 'http://127.0.0.1:18000/';
const base = target.replace(/\/$/, '');

const sizes = [
  [360, 640], [375, 667], [390, 844], [430, 932], [768, 1024], [810, 1080], [1024, 1366],
  [640, 360], [667, 375], [844, 390], [932, 430], [1024, 600], [1024, 768], [1280, 800],
  [1366, 768], [1920, 1080], [2560, 1440],
];

// 状態は偽物 API（POST /api/fake）で作る。実装の画面は URL 引数では状態を切り替えない
const states = [
  {name: '通常', fake: {ceiling: {status: 'MEASURED', mm: 2200}, height_mm: 1200}},
  {name: '天井の値なし', fake: {ceiling: {status: 'READ_ERROR'}, height_mm: 1200}},
  {name: '設定画面', fake: {ceiling: {status: 'MEASURED', mm: 2200}, height_mm: 1200}, settings: true},
  {name: '静止画の重ね表示', fake: {ceiling: {status: 'MEASURED', mm: 2200}, height_mm: 1200}, snapshot: true},
];

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
    // overflow:clip はスクロール領域にならない「意図した切り取り」（映像の枠）なので対象外
    if (cs.overflowX === 'clip' && cs.overflowY === 'clip') continue;
    if (!['INPUT', 'HTML', 'BODY', 'svg', 'g', 'rect', 'text', 'circle', 'SCRIPT', 'STYLE', 'HEAD'].includes(el.tagName)
        && el.clientHeight > 0
        && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) {
      bad.push(`overflow ${el.tagName}.${el.id || el.className} ${el.scrollWidth}x${el.scrollHeight}>${el.clientWidth}x${el.clientHeight}`);
    }
  }
  const must = [...document.querySelectorAll('.hold, input[type=range], #openSettings, .zoombtn, #snapBtn')];
  const overlay = document.querySelector('.overlay.show');
  if (overlay) must.push(...overlay.querySelectorAll('input, button'));
  for (const el of must) {
    const rc = el.getBoundingClientRect();
    const name = el.id || el.dataset.axis || el.dataset.f || el.textContent.trim().slice(0, 8);
    if (rc.top < -0.5 || rc.left < -0.5 || rc.bottom > innerHeight + 0.5 || rc.right > innerWidth + 0.5) {
      bad.push('offscreen ' + name);
    }
    const box = el.closest('.card, .sheet, .video');
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
      const page = await browser.newPage({viewport: {width: w, height: h}});
      await page.goto(base + '/');
      // state が来て、表示が落ち着くまで待つ
      await page.waitForFunction(() => document.getElementById('bClients').textContent.trim() !== '端末 —', null, {timeout: 5000});
      if (state.settings) {
        await page.click('#openSettings');
        await page.waitForSelector('#settings.show');
      }
      if (state.snapshot) {
        await page.click('#snapBtn');
        await page.waitForSelector('#snapshot.show');
      }
      await page.waitForTimeout(120);
      const bad = await page.evaluate(MEASURE);
      n += 1;
      if (bad.length) {
        fail += 1;
        console.log(`NG ${w}x${h} ${state.name}: ${[...new Set(bad)].join(', ')}`);
      }
      await page.close();
    }
  }
  console.log(`${n - fail}/${n} OK`);
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch((error) => {
  console.error('検査が例外で止まった', error);
  process.exit(1);
});
