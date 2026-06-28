// collect_window.js —— 在「当前已打开的 X 搜索结果页」上滚动并采集 @某账号 的推文。
//
// 用法（配合 Playwright MCP）：
//   1) 先 mcp__playwright__browser_navigate 打开日期窗搜索 URL：
//      https://x.com/search?q=from%3A<handle>%20since%3AYYYY-MM-DD%20until%3AYYYY-MM-DD&f=live
//   2) mcp__playwright__browser_run_code_unsafe({ filename: ".../collect_window.js" })
//      —— 它会滚动到底、把推文存进 page 的 window.__t，并 return 一个汇总(总数/折叠数/按日分布)。
//   3) 落盘：mcp__playwright__browser_evaluate({
//          function: "() => Object.values(window.__t)", filename: "serenity_win_X.json" })
//
// ⚠️ 关键点：
//   - HANDLE 必须改成目标账号（小写，不带 @）。搜索 from: 结果会混入他回复的别人原帖，必须按 handle 过滤。
//   - FOLD 是多语言折叠按钮文案：X 的 UI 语言会随机切到中/日/韩，若只认 'Show more' 会漏掉全部折叠帖
//     （folded 恒为 0），导致后续无法识别需要回填全文的长帖。这是最容易踩的坑。
//   - run_code_unsafe 沙箱：禁止 require/import/fs/setTimeout；用 page.waitForTimeout；
//     globalThis 不跨调用持久，但「主页面的 window」在不导航时跨调用持久（本脚本即依赖这一点）。
//   - 时间线正文截断在 ~280 字符：折叠的长帖此处只拿到截断文本，folded=true 标记它，留待 backfill_batch.js 取全文。

async (page) => {
  const HANDLE = 'aleabitoreddit';                                   // ← 改成目标账号（小写、无 @）
  const FOLD = ['Show more', '显示更多', '顯示更多', 'もっと見る', '더 보기']; // 多语言「显示更多」
  const re = new RegExp('@' + HANDLE + '\\b', 'i');

  await page.evaluate(() => { window.__t = {}; });
  let rounds = 0, lastH = 0, stagnant = 0;
  while (rounds < 60) {
    const got = await page.evaluate((args) => {
      const [foldList, handleRe] = args;
      const test = new RegExp(handleRe, 'i');
      const out = [];
      for (const a of document.querySelectorAll('article[data-testid="tweet"]')) {
        const userEl = a.querySelector('[data-testid="User-Name"]');
        if (!test.test(userEl ? userEl.innerText : '')) continue;          // 只要目标账号自己的帖
        const timeEl = a.querySelector('time[datetime]');
        const dt = timeEl ? timeEl.getAttribute('datetime') : '';
        const linkEl = a.querySelector('a[href*="/status/"]');
        const m = (linkEl ? linkEl.getAttribute('href') : '').match(/status\/(\d+)/);
        const id = m ? m[1] : '';
        if (!id) continue;
        const txtEl = a.querySelector('[data-testid="tweetText"]');
        const txt = txtEl ? txtEl.innerText : '';
        let folded = false;
        for (const el of a.querySelectorAll('span,div,button')) {
          if (foldList.includes((el.innerText || '').trim())) { folded = true; break; }
        }
        out.push({ id, dt, txt, folded, len: txt.length });
      }
      return out;
    }, [FOLD, '@' + HANDLE + '\\b']);
    await page.evaluate((items) => { for (const it of items) if (!window.__t[it.id]) window.__t[it.id] = it; }, got);
    await page.evaluate(() => window.scrollBy(0, 2400));
    await page.waitForTimeout(1100);
    const h = await page.evaluate(() => document.body.scrollHeight);
    if (h === lastH) stagnant++; else { stagnant = 0; lastH = h; }
    if (stagnant >= 5) break;            // 连续 5 轮高度不变 = 到底
    rounds++;
  }

  const all = await page.evaluate(() => Object.values(window.__t));
  const dates = {};
  for (const x of all) { const d = (x.dt || '').slice(0, 10); dates[d] = (dates[d] || 0) + 1; }
  return {
    total: all.length,
    folded: all.filter(x => x.folded).length,
    hv: all.filter(x => x.folded || x.len >= 260).length,   // 口径B 高价值（折叠 或 正文≥260）
    rounds, dates,
  };
}
