// Shared helpers: CDP connection to this skill's isolated Chrome, netdisk API, path normalization.
// Work dir configurable via env BAIDU_SAVE_WORK (default ~/workspace/aiDownload/baidu-share-save).
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright-core');

const WORK = process.env.BAIDU_SAVE_WORK || path.join(os.homedir(), 'workspace/aiDownload/baidu-share-save');
const USERDATA = path.join(WORK, 'chrome-userdata');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// launch.sh starts Chrome with --remote-debugging-port=0: Chrome picks a free port and
// writes it to <user-data-dir>/DevToolsActivePort, so we never collide with the user's Chrome.
function cdpUrl() {
  if (process.env.CDP_URL) return process.env.CDP_URL;
  const port = fs.readFileSync(path.join(USERDATA, 'DevToolsActivePort'), 'utf8').split('\n')[0].trim();
  return `http://127.0.0.1:${port}`;
}

// NEVER call browser.close(); process.exit() just drops the websocket and Chrome keeps running.
async function connect() {
  const browser = await chromium.connectOverCDP(cdpUrl());
  return { browser, context: browser.contexts()[0] };
}

// A pan.baidu.com tab that is not a share page (disk/main, or the login page before login), used for /api calls.
async function diskPage(context) {
  let p = context.pages().find((x) => /^https:\/\/pan\.baidu\.com\//.test(x.url()) && !/pan\.baidu\.com\/(s\/|share\/)/.test(x.url()));
  if (!p) {
    p = await context.newPage();
    await p.goto('https://pan.baidu.com/disk/main', { waitUntil: 'domcontentloaded' }).catch(() => {});
    await sleep(2000);
  }
  return p;
}

// '/a/b/'、'我的网盘/a/b'、'全部文件/a/b'、' a / b '、'全部文件 > a > b' -> 'a/b'; root -> ''
// ('>' is not allowed in netdisk names, so it is safe to treat it as a breadcrumb separator)
function normPath(s) {
  return String(s || '').replace(/\s+/g, '').replace(/[>＞›]/g, '/').replace(/^\/*(?:我的网盘|全部文件)/, '')
    .replace(/\/{2,}/g, '/').replace(/^\/+|\/+$/g, '');
}

// Names directly under dir via the logged-in netdisk API. errno: 0 ok, -6 not logged in, -9 dir missing.
// Retries while the page is still navigating/redirecting ("Execution context was destroyed").
async function listDir(page, dir) {
  let r;
  for (let a = 0; a < 4; a++) {
    await page.waitForLoadState('load', { timeout: 15000 }).catch(() => {});
    if (!/^https:\/\/pan\.baidu\.com\//.test(page.url())) return { errno: -6, names: [] };
    r = await listDirOnce(page, dir);
    if (!String(r.errno).startsWith('eval:')) return r;
    await sleep(1500);
  }
  return r;
}

async function listDirOnce(page, dir) {
  return page.evaluate(async (d) => {
    let names = [], errno = 0;
    for (let pg = 1; pg <= 50; pg++) {
      const r = await fetch(`/api/list?order=name&desc=0&dir=${encodeURIComponent(d)}&num=1000&page=${pg}&web=1&clienttype=0`, { credentials: 'include' });
      const j = await r.json();
      errno = j.errno;
      if (errno !== 0) break;
      const list = j.list || [];
      names = names.concat(list.map((x) => x.server_filename));
      if (list.length < 1000) break;
    }
    return { errno, names };
  }, dir).catch((e) => ({ errno: 'eval:' + e.message.slice(0, 60), names: [] }));
}

module.exports = { WORK, USERDATA, sleep, connect, diskPage, normPath, listDir };
