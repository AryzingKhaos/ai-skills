// Recursively walk a pantools share-list folder tree and emit a manifest of all files.
// Usage: node enumerate.js <startFolder> <outManifest.json>
//   <startFolder>  : top folder name to descend into (e.g. "2026"); "" = current root.
// Handles pagination (50/page) and nested subfolders. Folders are entered by name;
// after recursing, navigation returns to the parent via the breadcrumb.
const fs = require('fs');
const { connect, listPage, sleep } = require('./lib');

async function listCurrentPageItems(page) {
  return page.evaluate(() => Array.from(document.querySelectorAll('.file-item')).map((el) => ({
    name: el.getAttribute('data-file-name'),
    isDir: el.getAttribute('data-is-dir') === '1',
    fsId: el.getAttribute('data-fs-id'),
  })));
}

async function paginationState(page) {
  return page.evaluate(() => {
    const info = document.querySelector('.pagination-info');
    const t = info ? info.innerText.trim() : '';
    const m = t.match(/\((\d+)-(\d+)\/(\d+)\)/);
    const nextBtn = Array.from(document.querySelectorAll('button')).find((b) => b.innerText.trim() === '下一页');
    const nextDisabled = nextBtn ? (nextBtn.disabled || nextBtn.classList.contains('disabled')) : true;
    return m ? { from: +m[1], to: +m[2], total: +m[3], nextDisabled } : { from: 1, to: 0, total: 0, nextDisabled: true };
  });
}

// Collect ALL items in the current folder across all pages.
async function collectAllItems(page) {
  const all = [];
  const seen = new Set();
  // ensure on page 1: click 上一页 repeatedly is unreliable; assume entry resets to page 1
  for (let guard = 0; guard < 200; guard++) {
    const items = await listCurrentPageItems(page);
    for (const it of items) { if (!seen.has(it.fsId)) { seen.add(it.fsId); all.push(it); } }
    const pg = await paginationState(page);
    if (pg.nextDisabled || pg.to >= pg.total || pg.total === 0) break;
    const nextBtn = page.locator('button', { hasText: '下一页' }).first();
    await nextBtn.click().catch(() => {});
    await sleep(1500);
  }
  return all;
}

async function enterFolder(page, name) {
  // the folder may be on a later page; paginate to find it
  for (let guard = 0; guard < 200; guard++) {
    const sel = `.file-item[data-file-name="${name.replace(/"/g, '\\"')}"]`;
    if (await page.$(sel)) { await page.click(sel); await sleep(2500); return true; }
    const pg = await paginationState(page);
    if (pg.nextDisabled || pg.to >= pg.total) return false;
    await page.locator('button', { hasText: '下一页' }).first().click().catch(() => {});
    await sleep(1200);
  }
  return false;
}

async function gotoBreadcrumb(page, name) {
  // click the breadcrumb item whose text matches `name` (or 首页 for root)
  const ok = await page.evaluate((nm) => {
    const items = Array.from(document.querySelectorAll('.breadcrumb-item'));
    let target = null;
    for (const it of items) { if (it.innerText.trim().replace(/\s+/g, '') === nm.replace(/\s+/g, '')) target = it; }
    if (!target && nm === '首页') target = items[0];
    if (target) { target.click(); return true; }
    return false;
  }, name);
  await sleep(2200);
  return ok;
}

const files = [];
let dirCount = 0;

async function dfs(page, folderName, pathArr) {
  const items = await collectAllItems(page);
  const dirs = items.filter((i) => i.isDir);
  const fileItems = items.filter((i) => !i.isDir);
  for (const f of fileItems) files.push({ path: pathArr.join('/'), folder: folderName, fsId: f.fsId, name: f.name });
  for (const d of dirs) {
    dirCount++;
    const entered = await enterFolder(page, d.name);
    if (!entered) { console.error('WARN could not enter', pathArr.join('/'), '/', d.name); continue; }
    await dfs(page, d.name, [...pathArr, d.name]);
    await gotoBreadcrumb(page, folderName); // return to this folder
  }
}

(async () => {
  const start = process.argv[2] || '';
  const out = process.argv[3] || './manifest.json';
  const { context } = await connect();
  const page = listPage(context);
  if (!page) { console.error('no pantools tab'); process.exit(1); }

  // go to share root (首页), then optionally into <start>
  await gotoBreadcrumb(page, '首页');
  let rootName = '首页';
  if (start) { const ok = await enterFolder(page, start); if (!ok) { console.error('cannot enter start folder', start); process.exit(1); } rootName = start; }

  await dfs(page, rootName, start ? [start] : []);

  fs.writeFileSync(out, JSON.stringify({ start, count: files.length, dirCount, files }, null, 2));
  console.log(JSON.stringify({ start, totalFiles: files.length, totalDirs: dirCount, out }));
  process.exit(0);
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
