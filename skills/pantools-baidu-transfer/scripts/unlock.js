// Enter the pantools list access password. Usage: node unlock.js <password>
const { connect, listPage, sleep } = require('./lib');
(async () => {
  const pass = process.argv[2];
  if (!pass) { console.error('usage: node unlock.js <password>'); process.exit(1); }
  const { context } = await connect();
  const page = listPage(context);
  if (!page) { console.error('no pantools tab open'); process.exit(1); }

  // clear any pollution in the search box first
  const search = await page.$('input.search-input');
  if (search) await search.fill('');

  const pw = await page.$('input.password-input, input[placeholder*="访问密码"]');
  if (pw) {
    await pw.fill(pass);
    await sleep(300);
    // click the dedicated password confirm button, else Enter
    if (await page.$('#passwordConfirm')) await page.click('#passwordConfirm');
    else await pw.press('Enter');
    await sleep(4000);
  }
  // re-clear search post-unlock (so the list is not filtered)
  const s2 = await page.$('input.search-input');
  if (s2) { await s2.fill(''); await sleep(800); }

  const locked = await page.evaluate(() => {
    const e = document.querySelector('input.password-input');
    return !!(e && e.offsetParent !== null);
  });
  console.log(JSON.stringify({ stillLocked: locked }));
  process.exit(0);
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
