// Debug: screenshot the first tab whose URL contains <urlSubstr>. Usage: node shot.js [urlSubstr] [out.png]
const path = require('path');
const { connect, WORK } = require('./lib');

(async () => {
  const urlSubstr = process.argv[2] || '';
  const out = process.argv[3] || path.join(WORK, 'shot.png');
  const { context } = await connect();
  const pages = context.pages();
  const page = (urlSubstr ? pages.find((p) => p.url().includes(urlSubstr)) : null) || pages[pages.length - 1];
  if (!page) { console.error('no page'); process.exit(1); }
  await page.bringToFront().catch(() => {});
  await page.screenshot({ path: out });
  console.log('shot:', out, '| url:', page.url());
  process.exit(0);
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
