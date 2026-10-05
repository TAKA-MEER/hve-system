// 使い方: node check_noscroll.js <index.html のパス or http://...>
//   playwright が require できないときは PLAYWRIGHT_PATH=<.../node_modules/playwright> を付ける。
//   ブラウザは既定で /usr/bin/google-chrome（CHROME_PATH で変更）。
//   17 の画面サイズ × 4 状態で、ページ・要素のスクロールと中身のはみ出し、操作部品の画面外・カード外・小さすぎを検出する。
// モックアップが、どの画面サイズ・状態でもスクロールせず、主要部品がはみ出さないかを検査する
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const target = process.argv[2];
const url = /^https?:/.test(target) ? target : 'file://' + require('path').resolve(target);
const sizes = [[360,640],[375,667],[390,844],[430,932],[768,1024],[810,1080],[1024,1366],
               [640,360],[667,375],[844,390],[932,430],[1024,600],[1024,768],[1280,800],[1366,768],[1920,1080],[2560,1440]];
const states = ['', '?h=1100&ceil=stale&hold=lift_up&zoom=4', '?settings=1', '?mock=1'];
(async () => {
  const b = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome'});
  let fail = 0, n = 0;
  for (const [w,h] of sizes) for (const st of states) {
    const p = await b.newPage({viewport:{width:w,height:h}});
    await p.goto(url + st); await p.waitForTimeout(150);
    const r = await p.evaluate(() => {
      const bad = [];
      const de = document.documentElement;
      if (de.scrollHeight > innerHeight + 1 || de.scrollWidth > innerWidth + 1) bad.push(`page ${de.scrollWidth}x${de.scrollHeight}`);
      // スクロールしうる要素・中身がはみ出している要素
      for (const el of document.querySelectorAll('*')) {
        const cs = getComputedStyle(el);
        if (cs.display === 'none') continue;
        if (/(auto|scroll)/.test(cs.overflowY + cs.overflowX) && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) bad.push('scrollable ' + (el.id || el.className));
        // 中身が枠からはみ出している（重なって見えなくなる）。入力欄・SVG・html/body は除く
        // overflow:clip はスクロール領域にならない「意図した切り取り」（ズームした映像の枠）なので対象外
        if (cs.overflowX === 'clip' && cs.overflowY === 'clip') continue;
        if (!['INPUT','HTML','BODY','svg','g','rect','text','circle','SCRIPT','STYLE','HEAD'].includes(el.tagName) && el.clientHeight > 0 &&
            (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) bad.push(`overflow ${el.tagName}.${el.id || el.className} ${el.scrollWidth}x${el.scrollHeight}>${el.clientWidth}x${el.clientHeight}`);
      }
      // 見えているべき部品が、表示領域・自分のカードの中に収まっているか（大きさの下限つき）
      const must = [...document.querySelectorAll('.hold, input[type=range], #openSettings, .zoombtn')];
      const ov = document.querySelector('.overlay.show');
      if (ov) must.push(...ov.querySelectorAll('input, button'));
      const pop = document.querySelector('.mockpop.show'); if (pop) must.push(...pop.querySelectorAll('button'));
      for (const el of must) {
        const rc = el.getBoundingClientRect();
        const name = el.id || el.dataset.axis || el.dataset.mock || el.dataset.f || el.textContent.trim().slice(0,8);
        if (rc.top < -0.5 || rc.left < -0.5 || rc.bottom > innerHeight + 0.5 || rc.right > innerWidth + 0.5) bad.push('offscreen ' + name);
        const box = el.closest('.card, .sheet, .mockpop, .video');
        if (box) { const bc = box.getBoundingClientRect(); if (rc.bottom > bc.bottom + 0.5 || rc.right > bc.right + 0.5) bad.push('clipped ' + name); }
        if (el.classList.contains('hold') && (rc.height < 32 || rc.width < 32)) bad.push(`small ${name} ${rc.width|0}x${rc.height|0}`);
      }
      return bad;
    });
    n++;
    if (r.length) { fail++; console.log(`NG ${w}x${h} ${st||'(通常)'}: ${[...new Set(r)].join(', ')}`); }
    await p.close();
  }
  console.log(`${n - fail}/${n} OK`);
  await b.close();
  process.exit(fail ? 1 : 0);
})();
