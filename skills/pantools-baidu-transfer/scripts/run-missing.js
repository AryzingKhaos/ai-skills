// Retry only the missing files. CRITICAL: never full-reload the list page
// (reload re-locks it and re-submitting the password risks a 1-hour lockout).
// Unlock ONCE at start if needed, then navigate purely via SPA clicks
// (breadcrumb 首页 -> drill down by folder name). Within a folder we only click
// share buttons (opens/closes Baidu tabs) — the list page never navigates.
// Usage: node run-missing.js <missing.json> <progress.json> <password> [targetFolder]
const fs = require('fs');
const { connect, listPage, closeStrayBaiduTabs, sleep } = require('./lib');

function load(p, def) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return def; } }

async function isLocked(list) {
  return list.evaluate(() => !!(document.querySelector('input.password-input') && document.querySelector('input.password-input').offsetParent !== null)).catch(() => false);
}
// One careful unlock WITHOUT reloading. Returns {ok, msg}.
async function unlockOnce(list, pass) {
  const s = await list.$('input.search-input'); if (s) await s.fill('');
  const pw = await list.$('input.password-input');
  if (!pw) return { ok: true, msg: 'already unlocked' };
  await pw.click({ clickCount: 3 }).catch(() => {});
  await pw.fill('');
  await pw.type(pass, { delay: 100 });
  await sleep(400);
  if (await list.$('#passwordConfirm')) await list.click('#passwordConfirm'); else await pw.press('Enter');
  await sleep(4500);
  const s2 = await list.$('input.search-input'); if (s2) { await s2.fill(''); await sleep(700); }
  const locked = await isLocked(list);
  const msg = await list.evaluate(() => { for (const e of document.querySelectorAll('*')) { const t = (e.innerText || '').trim(); if (/密码错误|请重试|再试/.test(t) && t.length < 40) return t; } return ''; }).catch(() => '');
  return { ok: !locked, msg };
}

async function hideModals(list) {
  await list.evaluate(() => {
    for (const id of ['share-modal', 'fileModal']) { const m = document.getElementById(id); if (m) m.style.display = 'none'; }
  }).catch(() => {});
}
async function waitItems(list, min) {
  for (let i = 0; i < 25; i++) { const n = await list.evaluate(() => document.querySelectorAll('.file-item').length).catch(() => 0); if (n >= (min || 1)) return n; await sleep(700); }
  return 0;
}
async function gotoHome(list) {
  await list.evaluate(() => { const items = document.querySelectorAll('.breadcrumb-item'); if (items[0]) items[0].click(); }).catch(() => {});
  await sleep(2300);
}
async function clickFolderByName(list, name) {
  const sel = `.file-item[data-file-name="${name.replace(/"/g, '\\"')}"]`;
  for (let g = 0; g < 30; g++) {
    await hideModals(list);
    if (await list.$(sel)) { await list.click(sel).catch(async () => { await hideModals(list); await list.click(sel).catch(() => {}); }); await sleep(2400); return true; }
    const more = await list.evaluate(() => { const info = document.querySelector('.pagination-info'); const m = info ? info.innerText.trim().match(/\((\d+)-(\d+)\/(\d+)\)/) : null; return m ? (+m[2] < +m[3]) : false; }).catch(() => false);
    if (!more) return false;
    await list.locator('button', { hasText: '下一页' }).first().click().catch(() => {});
    await sleep(1100);
  }
  return false;
}
// SPA navigation only — NEVER reload.
async function navToFolder(list, segments) {
  await hideModals(list);
  await gotoHome(list);
  if (!(await waitItems(list, 1))) return 0;
  for (const seg of segments) { if (!(await clickFolderByName(list, seg))) { console.log('  drilldown miss at', seg); return 0; } }
  return await waitItems(list, 1);
}

async function saveOne(context, list, fsId, target) {
  const WANT = `我的网盘/${target}`;
  const out = { fsId, status: 'unknown', note: '' };
  await closeStrayBaiduTabs(context);
  await list.evaluate(() => { const m = document.getElementById('share-modal'); if (m) m.style.display = 'none'; }).catch(() => {});
  const itemSel = `.file-item[data-fs-id="${fsId}"]`;
  // waitForSelector (not list.$) rides out the transient "context destroyed"
  // churn the list page does right after each save.
  let found = false;
  for (let a = 0; a < 3 && !found; a++) {
    try { await list.waitForSelector(itemSel, { timeout: 10000 }); found = true; } catch (e) { await sleep(1500); }
  }
  if (!found) { out.status = 'notfound'; return out; }
  await list.click(`${itemSel} .file-actions button`).catch(async () => { await sleep(1000); await list.click(`${itemSel} .file-actions button`).catch(() => {}); });
  await list.waitForSelector('#share-modal', { timeout: 8000, state: 'attached' }).catch(() => {});
  await sleep(500);
  let baidu;
  try {
    const [np] = await Promise.all([context.waitForEvent('page', { timeout: 25000 }), list.locator('#share-modal button', { hasText: '打开链接' }).click()]);
    baidu = np;
  } catch (e) { out.status = 'noopen'; out.note = e.message.slice(0, 50); return out; }
  await baidu.waitForLoadState('domcontentloaded').catch(() => {});
  await sleep(2200);
  await list.evaluate(() => { const m = document.getElementById('share-modal'); if (m) m.style.display = 'none'; }).catch(() => {});
  if (/share\/init|surl=/.test(baidu.url())) {
    const ci = await baidu.$('input[type="text"]').catch(() => null); if (ci) { const v = await ci.inputValue().catch(() => ''); if (!v) await ci.fill('8888').catch(() => {}); }
    const ex = baidu.locator('text=提取文件').first();
    if (await ex.count().catch(() => 0)) await ex.click().catch(() => {});
    await baidu.waitForURL(/pan\.baidu\.com\/s\//, { timeout: 20000 }).catch(() => {});
    await sleep(3000);
  }
  let savePath = null;
  for (let i = 0; i < 12; i++) { savePath = await baidu.evaluate(() => { const e = document.querySelector('.save-path'); return e ? e.innerText.trim() : null; }).catch(() => null); if (savePath) break; await sleep(800); }
  if (!savePath) { out.status = 'nofilepage'; await baidu.close().catch(() => {}); return out; }
  if (savePath !== WANT) {
    await baidu.click('.bottom-save-path-icon').catch(() => {});
    await sleep(1800);
    const node = baidu.locator('.dialog-fileTreeDialog').getByText(target, { exact: true }).first();
    await node.scrollIntoViewIfNeeded().catch(() => {});
    await node.click().catch(() => {});
    await sleep(700);
    await baidu.locator('.dialog-fileTreeDialog a.g-button-blue-large, .dialog-fileTreeDialog a:has-text("确定")').first().click().catch(() => {});
    await sleep(1500);
    savePath = await baidu.evaluate(() => { const e = document.querySelector('.save-path'); return e ? e.innerText.trim() : null; }).catch(() => null);
  }
  if (savePath !== WANT) { out.status = 'pathfail'; out.note = String(savePath); await baidu.close().catch(() => {}); return out; }
  await baidu.evaluate(() => { const a = document.querySelector('a[node-type="bottomShareSave"], a.bottom_save_btn'); if (a) a.click(); }).catch(() => {});
  let verdict = '';
  for (let i = 0; i < 12; i++) {
    await sleep(900);
    verdict = await baidu.evaluate(() => { const d = document.getElementById('emptyDialogId'); const t1 = d && d.offsetParent !== null ? d.innerText : ''; return (t1 + ' ' + (document.body ? document.body.innerText : '')).replace(/\s+/g, ' '); }).catch(() => '');
    if (/保存成功|已保存至|已经保存过|已保存到/.test(verdict)) { out.status = 'saved'; break; }
    if (/验证|请稍后|频繁|风险|操作过快|超过限制/.test(verdict)) { out.status = 'ratelimited'; break; }
  }
  if (out.status === 'unknown') out.status = 'maybe';
  await baidu.close().catch(() => {});
  return out;
}

(async () => {
  const missingPath = process.argv[2], progressPath = process.argv[3], pass = process.argv[4] || '9999', target = process.argv[5] || 'AI视频保存';
  const missing = load(missingPath, null);
  if (!missing) { console.error('cannot read', missingPath); process.exit(1); }
  const progress = load(progressPath, { done: {}, stats: {} });
  const { context } = await connect();
  const list = listPage(context);
  if (!list) { console.error('no pantools tab'); process.exit(1); }

  if (await isLocked(list)) {
    const u = await unlockOnce(list, pass);
    if (!u.ok) { console.error('LOCKED — cannot unlock:', u.msg || '(still locked)'); process.exit(2); }
    console.log('unlocked');
  }

  const byPath = {}; for (const f of missing) (byPath[f.path] = byPath[f.path] || []).push(f);
  let saved = 0, fail = 0, idx = 0; const total = missing.length; const t0 = Date.now();
  for (const pathKey of Object.keys(byPath).sort()) {
    const n = await navToFolder(list, pathKey.split('/'));
    console.log(`FOLDER ${pathKey} -> ${n} items`);
    if (!n) { for (const f of byPath[pathKey]) { progress.done[f.fsId] = { status: 'navfail', name: f.name, path: pathKey }; fail++; idx++; } fs.writeFileSync(progressPath, JSON.stringify(progress, null, 2)); continue; }
    for (const f of byPath[pathKey]) {
      idx++;
      let r = { status: 'error', note: '' };
      for (let attempt = 0; attempt < 3; attempt++) {
        try { r = await saveOne(context, list, f.fsId, target); } catch (e) { r = { status: 'error', note: e.message.slice(0, 50) }; }
        if (r.status === 'saved' || r.status === 'ratelimited') break;
        // recover before retry: dismiss modal, re-enter folder if the item is gone
        await hideModals(list);
        const here = await list.$(`.file-item[data-fs-id="${f.fsId}"]`).catch(() => null);
        if (!here) await navToFolder(list, pathKey.split('/'));
        await sleep(1500);
      }
      progress.done[f.fsId] = { status: r.status, name: f.name, path: pathKey, note: r.note };
      if (r.status === 'saved') saved++; else fail++;
      console.log(`[${idx}/${total}] ${r.status} | ${pathKey}/${(f.name || '').slice(0, 34)} | ok=${saved} fail=${fail} | ${Math.round((Date.now() - t0) / 1000)}s`);
      fs.writeFileSync(progressPath, JSON.stringify(progress, null, 2));
      await sleep(2500);
      if (r.status === 'ratelimited') { console.log('RATE LIMITED — pausing 90s'); await sleep(90000); }
    }
  }
  progress.stats = { lastMissingRun: { total, saved, fail } };
  fs.writeFileSync(progressPath, JSON.stringify(progress, null, 2));
  console.log(`DONE-MISSING total=${total} saved=${saved} fail=${fail}`);
  process.exit(0);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
