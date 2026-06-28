// backfill_batch.js —— 逐条打开推文详情页、抓「完整正文」，把折叠长帖的截断文本补全。
//
// 为什么需要它：搜索/时间线只给 ~280 字符截断文本（长帖标「显示更多」）。要拿全文，必须逐条
// 打开 status 详情页。详情页端点与搜索端点是【两套独立限流】：搜索被限时，详情页照样能抓。
//
// 用法（配合 Playwright MCP）：
//   1) 先把待回填的 id 列表注入主页面（建议先 browser_navigate 到其中一条 status 作稳定基座，避免主页面被导航）：
//      mcp__playwright__browser_evaluate({ function:
//        "() => { window.__ids = ['id1','id2',...]; window.__cur = 0; window.__sf = {}; return window.__ids.length; }" })
//   2) 反复调用本脚本，每次处理 24 条（带 28s 冷却）：
//      mcp__playwright__browser_run_code_unsafe({ filename: ".../backfill_batch.js" })
//      —— 直到返回的 cur == 注入的 id 总数。每批 return {ok, err, cur, totalSf}。
//   3) 落盘全文映射：mcp__playwright__browser_evaluate({
//          function: "() => window.__sf", filename: "serenity_full.json" })   // { id: 全文string }
//
// ⚠️ 限流规律（实测）：详情页连抓 ~70-100 条后 fail 率飙升。解法 = 每批 24 条 + 批间冷却 28-30s +
//    单条间隔 4.5s。慢抓可稳过几百条、几乎 0 错。若某批 err 偏高，把 BATCH 调小、冷却调长，并在最后重试 miss。
//   - 主页面切勿在回填中途被 navigate：window.__ids/__cur/__sf 全靠主页面 window 跨调用持久。
//   - 用 page.context().newPage() 开新标签抓每条，抓完即 close，避免标签堆积。

async (page) => {
  const BATCH = 24;          // 每批条数（限流偏紧时调小）
  const COOLDOWN = 28000;    // 批间冷却 ms（首批也会先冷却一次，影响不大；如需首批免冷却可单独处理）
  const INTERVAL = 4500;     // 单条间隔 ms
  const HANDLE = 'aleabitoreddit';  // ← 改成目标账号（仅用于拼 URL）

  await page.waitForTimeout(COOLDOWN);
  const ctx = page.context();
  const ids = await page.evaluate(() => window.__ids);
  const cur = await page.evaluate(() => window.__cur);
  const end = Math.min(cur + BATCH, ids.length);
  const res = {};
  for (let i = cur; i < end; i++) {
    const id = ids[i];
    const np = await ctx.newPage();
    try {
      await np.goto('https://x.com/' + HANDLE + '/status/' + id, { timeout: 25000, waitUntil: 'domcontentloaded' });
      await np.waitForSelector('[data-testid="tweetText"]', { timeout: 12000 });
      res[id] = await np.evaluate(() => {
        const art = document.querySelector('article[data-testid="tweet"]');
        if (!art) return '';
        const t = art.querySelector('[data-testid="tweetText"]');
        return t ? t.innerText : '';
      });
    } catch (e) { res[id] = '__ERR__'; }
    await np.close();
    await page.waitForTimeout(INTERVAL);
  }
  await page.evaluate((r) => { Object.assign(window.__sf, r); window.__cur += Object.keys(r).length; }, res);
  return {
    ok: Object.values(res).filter(v => v && v !== '__ERR__').length,
    err: Object.values(res).filter(v => v === '__ERR__').length,
    cur: await page.evaluate(() => window.__cur),
    totalSf: await page.evaluate(() => Object.keys(window.__sf).length),
  };
}
