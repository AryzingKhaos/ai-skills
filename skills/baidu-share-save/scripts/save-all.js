// Step 3: open each share link in the isolated browser and save it into the target dir, one by one.
// Usage: node save-all.js <links.json> <progress.json> <目标网盘路径>
// - Resumable: progress.json is written after every link; re-running skips FINAL statuses.
// - Never clicks save while the page's save path != target. Ground truth = target dir listing
//   via netdisk API before/after (UI text alone is not trusted).
// - Lines starting with "WAIT-USER" mean the script is polling for the user to act in the browser.
// - Transient throttling ("请求超时/请稍后/频繁") backs off 1→2→4→8→10 min and retries the same link;
//   only a real captcha waits for the user.
// Exit: 0 done, 2 not logged in / target unusable, 3 path not settable, 4 blocked (captcha timeout or
// throttled 5× in a row), 5 quota full.
const fs = require('fs');
const { connect, diskPage, normPath, listDir, sleep } = require('./lib');

const FINAL = new Set(['saved', 'exists', 'dead']);
const RETRY = new Set(['error', 'nofilepage']);
const EXIT = { pathfail: 3, blocked: 4, ratelimited: 4, quota: 5, disconnected: 6 };
// CDP link dropped (Mac sleep, Chrome hang): reconnect instead of marching through every link as error
const CLOSED_RE = /has been closed|Target closed|Browser closed|disconnected|ECONNREFUSED/i;
const BACKOFF_MIN = [1, 2, 4, 8, 10];
const USER_WAIT_MS = 15 * 60 * 1000;

const DEAD_RE = /链接不存在|已被取消|已经被删除|你来晚了|已过期|已失效|涉及侵权|无法访问|被锁定|页面不存在/;
const BADCODE_RE = /提取码错误|提取码有误|访问码错误/;
const OK_RE = /保存成功|已保存至|成功保存/;
const EXISTS_RE = /已存在|已经保存过|重名/;
const LIMIT_RE = /超出.{0,10}上限|超过.{0,10}限制|数量上限/;
const QUOTA_RE = /容量不足|空间不足/;
const CAPTCHA_RE = /验证码|安全验证|滑动|拖动/;
const SLOW_RE = /请求超时|请稍后|频繁|操作过快|繁忙|风险/;

function load(p, def) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return def; } }
const clip = (s, n = 80) => String(s || '').slice(0, n);
function around(text, re) {
  const m = text.match(re);
  return m ? text.slice(Math.max(0, m.index - 20), m.index + 50) : clip(text);
}
async function bodyText(page) {
  return page.evaluate(() => (document.body ? document.body.innerText : '').replace(/\s+/g, ' ')).catch(() => '');
}
// .save-path shows "我的网盘/a/b"; long paths may be ellipsized, then title carries the full one.
async function readSavePath(page) {
  return page.evaluate(() => {
    const e = document.querySelector('.save-path');
    if (!e) return null;
    const t = e.innerText.trim(), ti = e.getAttribute('title');
    return /…|\.\.\./.test(t) && ti ? ti : t;
  }).catch(() => null);
}
// Top-level names in the share: locals.file_list (server_filename) on both layouts, list DOM as fallback.
async function shareNames(page) {
  for (let i = 0; i < 5; i++) {
    const names = await page.evaluate(() => {
      try {
        const fl = window.locals && (window.locals.get ? window.locals.get('file_list') : window.locals.file_list);
        if (Array.isArray(fl) && fl.length) return fl.map((f) => f.server_filename).filter(Boolean);
      } catch (e) { /* fall through to DOM */ }
      return [...document.querySelectorAll('.module-list-view a.filename[title]')].map((a) => a.title);
    }).catch(() => []);
    if (names.length) return names;
    await sleep(800);
  }
  return [];
}
async function added(disk, dir, seen) {
  const r = await listDir(disk, dir);
  return r.names.filter((n) => !seen.has(n));
}

// Poll fn() until truthy or timeout; announce once so the operator (Claude) can relay it.
async function waitForUser(page, msg, fn, ms = USER_WAIT_MS) {
  console.log(`WAIT-USER ${msg}`);
  await page.bringToFront().catch(() => {});
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await fn().catch(() => false)) return true;
    await sleep(2000);
  }
  return false;
}

// Walk the dir-picker tree segment by segment (click = select + lazy-expand), then 确定.
// Cancels and returns false if any segment is missing or the last one is not verifiably selected,
// so nothing lands in a wrong dir. Every failure prints "PICK-FAIL <why>" for diagnosis.
// DOM (2026-11 实测): .wp-modal__panel.share-save-dialog > … ul.treeview-content > li > div.treeview-node
// (> span.treeview-node-handler > span.treeview-txt), children in li > ul; selected node has .treeview-node-on;
// 确定/取消 are button.wp-base-button in .wp-modal__footer. The older .dialog-fileTreeDialog (top-button
// layout) has the same tree classes, a.g-button 确定/取消, and node-path="/a/b" on each .treeview-txt.
async function pickInTree(page, segs) {
  const dlg = page.locator('.share-save-dialog, .dialog-fileTreeDialog').first();
  try { await dlg.waitFor({ state: 'visible', timeout: 8000 }); } catch (e) {
    console.log('PICK-FAIL 选择框没有弹出');
    return false;
  }
  const cancel = async (why) => {
    console.log(`PICK-FAIL ${why}`);
    await dlg.locator('.wp-modal__footer button:has-text("取消"), a:has-text("取消")').first().click().catch(() => {});
    return false;
  };
  // "文件支持转存到同步空间哦 / 我知道了" onboarding tip sits over the tree and can slide in late
  await sleep(1000);
  await dlg.locator('button.isNotSyncTips-container-content-btn').click({ timeout: 3000 }).catch(() => {});
  const find = (scope, seg) => scope.locator('.treeview-txt').getByText(seg, { exact: true }).first();
  let scope = dlg;
  let node = null;
  let parent = null;
  for (const seg of segs) {
    node = find(scope, seg);
    if (!(await node.waitFor({ state: 'visible', timeout: 8000 }).then(() => true, () => false)) && parent) {
      // a folder created moments ago can be missing from the parent's first listing: collapse + re-expand once
      const arrow = parent.locator('xpath=ancestor::div[contains(@class,"treeview-node")][1]//em[contains(@class,"icon-operate")]').first();
      await arrow.click().catch(() => {}); await sleep(800);
      await arrow.click().catch(() => {}); await sleep(2000);
      node = find(scope, seg);
    }
    if (!(await node.isVisible().catch(() => false))) return cancel(`目录树里找不到「${seg}」`);
    await node.scrollIntoViewIfNeeded().catch(() => {});
    if (!(await node.click({ timeout: 8000 }).then(() => true, () => false))) return cancel(`点不了「${seg}」（可能被提示层挡住）`);
    await sleep(1200);
    // next segment must be searched among this node's children, not siblings of the same name
    const li = node.locator('xpath=ancestor::li[1]');
    const kids = li.locator('xpath=./ul');
    scope = (await kids.count()) ? kids : ((await li.count()) ? li : dlg);
    parent = node;
  }
  const on = await node.locator('xpath=ancestor::div[contains(@class,"treeview-node")][1]')
    .evaluate((e) => e.classList.contains('treeview-node-on')).catch(() => null);
  if (on !== true) return cancel(`「${segs[segs.length - 1]}」没有处于选中状态（${on === null ? '读不到节点' : '未选中'}）`);
  const ok = await dlg.locator('.wp-modal__footer button.wp-base-button--primary, a.g-button-blue-large, a:has-text("确定")')
    .first().click({ timeout: 8000 }).then(() => true, () => false);
  if (!ok) return cancel('点不了「确定」');
  await sleep(1500);
  return true;
}

async function saveOne(context, disk, link, want) {
  const dir = '/' + want;
  const page = await context.newPage();
  try {
    await page.goto(link.url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(2000);
    let body = await bodyText(page);
    if (DEAD_RE.test(body)) return { status: 'dead', note: around(body, DEAD_RE) };

    // 提取码页（URL 带 pwd 时多半已预填，甚至已自动跳过）
    if (/share\/init/.test(page.url())) {
      const input = page.locator('#accessCode, input[type="text"]').first();
      if (await input.count()) {
        const v = await input.inputValue().catch(() => '');
        if (!v) {
          if (!link.pwd) return { status: 'needcode', note: '需要提取码但文本里没找到' };
          await input.fill(link.pwd);
        }
      }
      await page.locator('text=提取文件').first().click().catch(() => {});
      await page.waitForURL(/pan\.baidu\.com\/s\//, { timeout: 20000 }).catch(() => {});
      await sleep(2500);
    }

    // 同一链接会随机出现两种布局（2026-11 实测）：
    //   bottom：底部栏有「最近保存路径」.save-path，选路径后点 button.bottom_save_btn；
    //   top：没有底部栏，只有右上角 a[node-type=shareSave]「保存到网盘」，点了弹旧版选择框，选好目录点确定即转存。
    // 底部栏渲染得比顶部按钮晚，所以先等几轮 .save-path，等不到才认定是 top。
    let layout = null;
    for (let i = 0; i < 15 && !layout; i++) {
      layout = await page.evaluate((late) => {
        if (document.querySelector('.save-path')) return 'bottom';
        const top = [...document.querySelectorAll('a[node-type="shareSave"]')].some((a) => a.offsetWidth);
        return late && top ? 'top' : null;
      }, i >= 5).catch(() => null);
      if (!layout) { body = await bodyText(page); if (DEAD_RE.test(body) || BADCODE_RE.test(body)) break; await sleep(800); }
    }
    if (!layout) {
      body = await bodyText(page);
      if (BADCODE_RE.test(body)) return { status: 'badcode', note: around(body, BADCODE_RE) };
      if (DEAD_RE.test(body)) return { status: 'dead', note: around(body, DEAD_RE) };
      return { status: 'nofilepage', note: clip(body) };
    }

    const before = await listDir(disk, dir);
    if (before.errno !== 0) return { status: 'pathfail', note: `目标目录不可读 errno=${before.errno}`, fatal: true };
    const seen = new Set(before.names);

    // 分享里的每一项在目标目录都已有完全同名的，就不再转存（百度遇到重名会加后缀，存成第二份）
    const names = await shareNames(page);
    if (names.length && names.every((n) => seen.has(n))) {
      return { status: 'exists', note: '目标目录已有同名，未转存：' + clip(names.join('、'), 60) };
    }

    // only text that appears AFTER this point counts (the picker's 确定 may already trigger the save)
    const pre = await bodyText(page);
    const fresh = (re, t) => re.test(t) && !re.test(pre);
    let got = [];

    const segs = want ? want.split('/') : ['我的网盘'];
    const pollAdded = async () => {
      for (let i = 0; i < 6 && !got.length; i++) {
        got = await added(disk, dir, seen);
        if (!got.length && i < 5) await sleep(1500);
      }
      return got;
    };

    if (layout === 'top') {
      // 旧版选择框每次都要选目录，选中后点确定即转存；选不上就不点确定，交给用户
      const openTop = () => page.evaluate(() => {
        const a = [...document.querySelectorAll('a[node-type="shareSave"]')].find((x) => x.offsetWidth);
        if (a) a.click();
        return !!a;
      });
      if (!(await openTop())) return { status: 'nofilepage', note: '找不到右上角「保存到网盘」按钮' };
      if (!(await pickInTree(page, segs))) {
        const ok = await waitForUser(page,
          `自动选路径失败：请在浏览器当前标签点右上角「保存到网盘」，在弹窗里选中 我的网盘/${want} 后点确定，脚本检测到新文件后自动继续`,
          async () => (await added(disk, dir, seen)).length > 0);
        if (!ok) return { status: 'pathfail', note: '顶部按钮布局，选择框里没选上目标目录', fatal: true };
        return { status: 'saved', added: await pollAdded() };
      }
    } else {
      // 百度会记住「最近保存路径」，通常只有第一条需要选路径。
      // 新版「保存到」弹窗点确定就直接转存（2026-11 实测），所以选完路径先查目标目录，
      // 已经多出文件就不再点保存，防止重复转存。
      if (normPath(await readSavePath(page)) !== want) {
        for (let attempt = 0; attempt < 2 && normPath(await readSavePath(page)) !== want; attempt++) {
          await page.evaluate(() => document.querySelector('.bottom-save-path-icon')?.click()).catch(() => {});
          if (await pickInTree(page, segs) && (await pollAdded()).length) return { status: 'saved', added: got };
        }
        if (normPath(await readSavePath(page)) !== want) {
          const ok = await waitForUser(page,
            `自动选路径失败：请在浏览器当前标签底部点「最近保存路径」旁的图标，选中 我的网盘/${want} 后点确定（新版弹窗点确定即直接保存），脚本检测到后自动继续`,
            async () => normPath(await readSavePath(page)) === want || (await added(disk, dir, seen)).length > 0);
          if (!ok) return { status: 'pathfail', note: 'save path = ' + (await readSavePath(page)), fatal: true };
        }
        if ((await pollAdded()).length) return { status: 'saved', added: got };
      }

      // 「保存到网盘」：新版是 button.bottom_save_btn，旧版是 a[node-type=bottomShareSave]；可能被 canvas 遮挡，用页面内 click
      const clicked = await page.evaluate(() => {
        const a = document.querySelector('.bottom_save_btn, a[node-type="bottomShareSave"]');
        if (a) a.click();
        return !!a;
      });
      if (!clicked) return { status: 'nofilepage', note: '找不到「保存到网盘」按钮' };
    }
    let verdict = '';
    for (let i = 0; i < 15; i++) {
      await sleep(900);
      verdict = await bodyText(page);
      if ([OK_RE, EXISTS_RE, LIMIT_RE, QUOTA_RE, CAPTCHA_RE, SLOW_RE].some((re) => fresh(re, verdict))) break;
    }
    if (fresh(QUOTA_RE, verdict)) return { status: 'quota', note: around(verdict, QUOTA_RE), fatal: true };
    if (fresh(CAPTCHA_RE, verdict)) {
      const ok = await waitForUser(page,
        '百度要求安全验证：请在浏览器当前标签完成验证，必要时再点一次「保存到网盘」，脚本检测到文件出现后自动继续（最多等 15 分钟）',
        async () => (await added(disk, dir, seen)).length > 0);
      if (!ok) return { status: 'blocked', note: around(verdict, CAPTCHA_RE), fatal: true };
    }

    // "请求超时" sometimes still lands the files, so look before declaring throttled
    const tries = [OK_RE, CAPTCHA_RE, SLOW_RE].some((re) => fresh(re, verdict)) ? 6 : 1;
    for (let i = 0; i < tries && !got.length; i++) {
      got = await added(disk, dir, seen);
      if (!got.length && i + 1 < tries) await sleep(1500);
    }
    if (got.length) return { status: 'saved', added: got };
    if (fresh(SLOW_RE, verdict)) return { status: 'ratelimited', note: around(verdict, SLOW_RE) };
    if (fresh(EXISTS_RE, verdict)) return { status: 'exists', note: around(verdict, EXISTS_RE) };
    if (fresh(LIMIT_RE, verdict)) return { status: 'limit', note: around(verdict, LIMIT_RE) };
    return { status: 'unverified', note: fresh(OK_RE, verdict) ? '提示保存成功但目标目录未见新文件' : clip(verdict) };
  } finally {
    await page.close().catch(() => {});
  }
}

(async () => {
  const [linksPath, progressPath, rawTarget] = process.argv.slice(2);
  if (!linksPath || !progressPath || rawTarget === undefined) {
    console.error('usage: node save-all.js <links.json> <progress.json> <目标网盘路径>');
    process.exit(1);
  }
  const want = normPath(rawTarget);
  const { links } = JSON.parse(fs.readFileSync(linksPath, 'utf8'));
  const progress = load(progressPath, { target: '/' + want, items: {} });
  if (normPath(progress.target) !== want) {
    console.error(`progress.json 属于 ${progress.target}，与本次目标 /${want} 不同；换一个 progress 文件`);
    process.exit(1);
  }

  let { context } = await connect();
  let disk = await diskPage(context);
  const probe = await listDir(disk, '/' + want);
  if (probe.errno !== 0) {
    console.error(`目标目录不可用 errno=${probe.errno}（-6=未登录，-9=目录不存在）——先跑 check.js`);
    process.exit(2);
  }

  const total = links.length, t0 = Date.now();
  let code = 0;
  for (let i = 0; i < total; i++) {
    const l = links[i];
    const label = clip(l.title || l.key, 30);
    const prev = progress.items[l.key];
    if (prev && FINAL.has(prev.status)) { console.log(`[${i + 1}/${total}] skip(${prev.status}) | ${label}`); continue; }

    let r, retried = false, throttled = 0, reconnects = 0;
    // "文件转存中… 请求超时" can finish during the pause; diff against this snapshot before retrying.
    // A failed listing must not become an empty snapshot (everything would look newly saved).
    const snapR = await listDir(disk, '/' + want);
    const snap = snapR.errno === 0 ? new Set(snapR.names) : null;
    for (;;) {
      try { r = await saveOne(context, disk, l, want); } catch (e) { r = { status: 'error', note: clip(e.message) }; }
      if (r.status === 'error' && CLOSED_RE.test(r.note)) {
        if (reconnects >= 3) { r = { status: 'disconnected', note: r.note, fatal: true }; break; }
        reconnects++;
        console.log(`RECONNECT [${i + 1}/${total}] (${reconnects}/3) | ${r.note}`);
        await sleep(10000);
        try { ({ context } = await connect()); disk = await diskPage(context); } catch (e) { /* retried next loop */ }
        continue;
      }
      if (r.status === 'ratelimited') {
        if (throttled >= BACKOFF_MIN.length) { r.fatal = true; break; }
        const min = BACKOFF_MIN[throttled++];
        console.log(`RATELIMIT [${i + 1}/${total}] pause ${min}min (${throttled}/${BACKOFF_MIN.length}) | ${r.note}`);
        await sleep(min * 60000);
        const now = await listDir(disk, '/' + want);
        const late = snap && now.errno === 0 ? now.names.filter((n) => !snap.has(n)) : [];
        if (late.length) { r = { status: 'saved', added: late, note: '限流暂停期间转存完成' }; break; }
        continue;
      }
      if (RETRY.has(r.status) && !retried) { retried = true; await sleep(3000); continue; }
      break;
    }
    progress.items[l.key] = { status: r.status, url: l.url, title: l.title, note: r.note || '', added: r.added || [], at: new Date().toISOString() };
    fs.writeFileSync(progressPath, JSON.stringify(progress, null, 2));
    console.log(`[${i + 1}/${total}] ${r.status} | ${label} | ${r.added ? r.added.join('、') : r.note} | ${Math.round((Date.now() - t0) / 1000)}s`);
    if (r.fatal) { code = EXIT[r.status] || 1; console.log(`STOP ${r.status}`); break; }
    // gentle pacing to avoid baidu risk control
    await sleep(6000 + Math.random() * 6000);
  }

  const counts = {};
  for (const l of links) { const s = (progress.items[l.key] || {}).status || 'pending'; counts[s] = (counts[s] || 0) + 1; }
  progress.stats = { total, ...counts, updatedAt: new Date().toISOString() };
  fs.writeFileSync(progressPath, JSON.stringify(progress, null, 2));
  console.log('DONE ' + JSON.stringify(progress.stats));
  process.exit(code);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
