// Process ONE pantools file end-to-end into a Baidu netdisk folder.
// Flow: click .file-actions(share) -> 打开链接 -> 提取文件 -> set save path -> 保存到网盘.
// Exports processOne(context, fsId, target) for the batch runner.
// Standalone:  node process-file.js <fsId> [targetFolder]
const { connect, listPage, baiduSharePage, closeStrayBaiduTabs, sleep } = require('./lib');

async function processOne(context, fsId, target) {
  const WANT = `我的网盘/${target}`;
  const result = { fsId, name: null, status: 'unknown', savedTo: null, note: '' };

  await closeStrayBaiduTabs(context);
  const list = listPage(context);
  if (!list) { result.status = 'error'; result.note = 'no pantools tab'; return result; }

  // hide any leftover share modal
  await list.evaluate(() => { const m = document.getElementById('share-modal'); if (m) m.style.display = 'none'; }).catch(() => {});

  const itemSel = `.file-item[data-fs-id="${fsId}"]`;
  try {
    await list.waitForSelector(itemSel, { timeout: 15000 });
  } catch (e) { result.status = 'error'; result.note = 'file not visible in current folder'; return result; }
  result.name = await list.getAttribute(itemSel, 'data-file-name');

  await list.click(`${itemSel} .file-actions button`);
  await list.waitForSelector('#share-modal', { timeout: 10000, state: 'attached' }).catch(() => {});
  await sleep(500);

  // click 打开链接 -> new baidu tab
  let baidu;
  try {
    const openBtn = list.locator('#share-modal button', { hasText: '打开链接' });
    const [np] = await Promise.all([
      context.waitForEvent('page', { timeout: 30000 }),
      openBtn.click(),
    ]);
    baidu = np;
  } catch (e) { result.status = 'error'; result.note = 'no new tab: ' + e.message; return result; }
  await baidu.waitForLoadState('domcontentloaded').catch(() => {});
  await sleep(2200);
  await list.evaluate(() => { const m = document.getElementById('share-modal'); if (m) m.style.display = 'none'; }).catch(() => {});

  // 提取文件 if on share/init
  if (/share\/init|surl=/.test(baidu.url())) {
    const ci = await baidu.$('input[type="text"]');
    if (ci) { const v = await ci.inputValue().catch(() => ''); if (!v) await ci.fill('8888'); }
    const ex = baidu.locator('text=提取文件').first();
    if (await ex.count()) await ex.click().catch(() => {});
    await baidu.waitForURL(/pan\.baidu\.com\/s\//, { timeout: 20000 }).catch(() => {});
    await sleep(3000);
  }

  // set save path if needed
  let savePath = await baidu.evaluate(() => { const e = document.querySelector('.save-path'); return e ? e.innerText.trim() : null; }).catch(() => null);
  if (savePath !== WANT) {
    await baidu.click('.bottom-save-path-icon').catch(() => {});
    await sleep(1800);
    const node = baidu.locator('.dialog-fileTreeDialog').getByText(target, { exact: true }).first();
    await node.scrollIntoViewIfNeeded().catch(() => {});
    await node.click().catch(() => {});
    await sleep(700);
    await baidu.locator('.dialog-fileTreeDialog a.g-button-blue-large, .dialog-fileTreeDialog a:has-text("确定")').first().click().catch(() => {});
    await sleep(1600);
    savePath = await baidu.evaluate(() => { const e = document.querySelector('.save-path'); return e ? e.innerText.trim() : null; }).catch(() => null);
  }
  result.savedTo = savePath;
  if (savePath !== WANT) { result.status = 'error'; result.note = 'path not set: ' + savePath; await baidu.close().catch(() => {}); return result; }

  // 保存到网盘 (in-page click to bypass canvas overlay)
  await baidu.evaluate(() => { const a = document.querySelector('a[node-type="bottomShareSave"], a.bottom_save_btn'); if (a) a.click(); }).catch(() => {});
  await sleep(3200);
  const dlg = await baidu.evaluate(() => {
    const d = document.getElementById('emptyDialogId');
    return d ? d.innerText.replace(/\s+/g, ' ').trim().slice(0, 220) : '';
  }).catch(() => '');
  if (/保存成功|已保存至|已经保存|已保存/.test(dlg)) {
    result.status = 'saved';
    const m = dlg.match(/已保存至\s*【([^】]+)】/);
    result.note = m ? m[1] : 'ok';
  } else if (/请稍后重试|频繁|风险|验证/.test(dlg)) {
    result.status = 'ratelimited'; result.note = dlg.slice(0, 100);
  } else {
    result.status = 'maybe'; result.note = 'no success text: ' + dlg.slice(0, 100);
  }
  await baidu.close().catch(() => {});
  return result;
}

module.exports = { processOne };

if (require.main === module) {
  (async () => {
    const fsId = process.argv[2];
    const target = process.argv[3] || 'AI视频保存';
    const { context } = await connect();
    const r = await processOne(context, fsId, target);
    console.log(JSON.stringify(r));
    process.exit(0);
  })().catch((e) => { console.log(JSON.stringify({ status: 'error', note: e.message })); process.exit(1); });
}
