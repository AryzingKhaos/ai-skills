// Batch-save every file in a manifest into a Baidu netdisk folder. Resumable.
// Usage: node run-all.js <manifest.json> <progress.json> [targetFolder]
// - Connects ONCE, reuses the connection for all files.
// - Navigates the pantools list into each file's folder (by path) before saving.
// - Writes progress.json after every file; re-running skips already-saved fsIds.
const fs = require('fs');
const { connect, listPage, sleep } = require('./lib');
const { processOne } = require('./process-file');

function load(p, def) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return def; } }

async function gotoBreadcrumb(page, name) {
  await page.evaluate((nm) => {
    const items = Array.from(document.querySelectorAll('.breadcrumb-item'));
    let t = null;
    for (const it of items) if (it.innerText.trim().replace(/\s+/g, '') === nm.replace(/\s+/g, '')) t = it;
    if (!t && nm === '首页') t = items[0];
    if (t) t.click();
  }, name);
  await sleep(2000);
}
async function paginationNext(page) {
  return page.evaluate(() => {
    const info = document.querySelector('.pagination-info');
    const m = info ? info.innerText.trim().match(/\((\d+)-(\d+)\/(\d+)\)/) : null;
    if (!m) return false;
    return +m[2] < +m[3];
  });
}
async function enterFolder(page, name) {
  for (let g = 0; g < 200; g++) {
    const sel = `.file-item[data-file-name="${name.replace(/"/g, '\\"')}"]`;
    if (await page.$(sel)) { await page.click(sel); await sleep(2200); return true; }
    if (!(await paginationNext(page))) return false;
    await page.locator('button', { hasText: '下一页' }).first().click().catch(() => {});
    await sleep(1100);
  }
  return false;
}
// Navigate the list so that the folder at pathArr is the current view.
async function navToPath(page, pathArr) {
  await gotoBreadcrumb(page, '首页');
  for (const seg of pathArr) {
    const ok = await enterFolder(page, seg);
    if (!ok) return false;
  }
  return true;
}

(async () => {
  const manifestPath = process.argv[2];
  const progressPath = process.argv[3];
  const target = process.argv[4] || 'AI视频保存';
  const manifest = load(manifestPath, null);
  if (!manifest) { console.error('cannot read manifest', manifestPath); process.exit(1); }
  const progress = load(progressPath, { done: {}, stats: {} });

  const { context } = await connect();
  const page = listPage(context);
  if (!page) { console.error('no pantools tab'); process.exit(1); }

  // group files by folder path to minimize navigation
  const byPath = {};
  for (const f of manifest.files) { (byPath[f.path] = byPath[f.path] || []).push(f); }

  let saved = 0, skipped = 0, failed = 0, idx = 0;
  const total = manifest.files.length;
  const t0 = Date.now();

  for (const pathKey of Object.keys(byPath)) {
    const pathArr = pathKey ? pathKey.split('/') : [];
    const filesHere = byPath[pathKey];
    const pending = filesHere.filter((f) => !(progress.done[f.fsId] && progress.done[f.fsId].status === 'saved'));
    if (pending.length === 0) { idx += filesHere.length; skipped += filesHere.length; continue; }

    const ok = await navToPath(page, pathArr);
    if (!ok) { console.log(`NAV-FAIL ${pathKey}`); for (const f of filesHere) { progress.done[f.fsId] = { status: 'navfail', name: f.name, path: pathKey }; failed++; idx++; } fs.writeFileSync(progressPath, JSON.stringify(progress, null, 2)); continue; }

    for (const f of filesHere) {
      idx++;
      if (progress.done[f.fsId] && progress.done[f.fsId].status === 'saved') { skipped++; continue; }
      let r;
      try { r = await processOne(context, f.fsId, target); }
      catch (e) { r = { status: 'error', note: e.message }; }
      progress.done[f.fsId] = { status: r.status, name: f.name, path: pathKey, note: r.note };
      if (r.status === 'saved') saved++; else failed++;
      const el = Math.round((Date.now() - t0) / 1000);
      console.log(`[${idx}/${total}] ${r.status} | ${pathKey}/${(f.name || '').slice(0, 40)} | saved=${saved} fail=${failed} skip=${skipped} | ${el}s`);
      fs.writeFileSync(progressPath, JSON.stringify(progress, null, 2));
      // gentle pacing to avoid baidu rate limits
      await sleep(1200);
      if (r.status === 'ratelimited') { console.log('RATE LIMITED — pausing 60s'); await sleep(60000); }
      // re-navigate to the folder (saving closed the baidu tab; list tab kept state but be safe)
      const stillThere = await page.$(`.file-item[data-fs-id="${f.fsId}"]`);
      if (!stillThere) await navToPath(page, pathArr);
    }
  }
  progress.stats = { total, saved, failed, skipped, finishedAt: new Date(Date.now()).toISOString?.() || '' };
  fs.writeFileSync(progressPath, JSON.stringify(progress, null, 2));
  console.log(`DONE total=${total} saved=${saved} failed=${failed} skipped=${skipped}`);
  process.exit(0);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
