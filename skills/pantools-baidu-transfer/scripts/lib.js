// Shared helpers for driving an isolated Chrome over CDP (playwright-core).
// CDP endpoint configurable via env CDP_URL (default http://127.0.0.1:9333).
const { chromium } = require('playwright-core');

const CDP = process.env.CDP_URL || 'http://127.0.0.1:9333';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Connect over CDP. NEVER call browser.close() (it would kill the user's Chrome);
// just process.exit() — the websocket drops and Chrome keeps running.
async function connect() {
  const browser = await chromium.connectOverCDP(CDP);
  const context = browser.contexts()[0];
  return { browser, context };
}

function listPage(context) {
  return context.pages().find((p) => p.url().includes('list.pantools.cn'));
}
function baiduSharePage(context) {
  return context.pages().find((p) => /pan\.baidu\.com\/s\//.test(p.url()))
      || context.pages().find((p) => p.url().includes('pan.baidu.com') && !p.url().includes('disk/main'));
}
async function closeStrayBaiduTabs(context) {
  for (const p of context.pages()) {
    const u = p.url();
    if (u.includes('pan.baidu.com') && !u.includes('disk/main')) await p.close().catch(() => {});
  }
}

module.exports = { connect, listPage, baiduSharePage, closeStrayBaiduTabs, sleep, CDP };
