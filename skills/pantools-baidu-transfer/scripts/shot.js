// Screenshot a page by URL substring. Usage: node shot.js <urlSubstr> <outfile>
const { connect } = require('./lib');
(async () => {
  const urlSubstr = process.argv[2] || '';
  const out = process.argv[3] || '/tmp/shot.png';
  const { context } = await connect();
  const pages = context.pages();
  const page = (urlSubstr ? pages.find((p) => p.url().includes(urlSubstr)) : null) || pages[0];
  if (!page) { console.error('no page'); process.exit(1); }
  await page.bringToFront().catch(() => {});
  await page.screenshot({ path: out });
  console.log('shot:', out, '| url:', page.url());
  process.exit(0);
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
