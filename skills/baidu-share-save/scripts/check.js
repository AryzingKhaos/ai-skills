// Step 1 gate: is the isolated browser logged in, and does the target dir exist?
// Usage: node check.js [目标网盘路径]   (no arg: just list top-level dirs to help pick one)
// Exit: 0 ok, 2 not logged in, 3 target dir missing.
const { connect, diskPage, normPath, listDir } = require('./lib');

(async () => {
  const { context } = await connect();
  const page = await diskPage(context);
  const root = await listDir(page, '/');
  if (root.errno !== 0) {
    console.log(JSON.stringify({ loggedIn: false, errno: root.errno, url: page.url() }));
    process.exit(2);
  }
  if (process.argv[2] === undefined) {
    console.log(JSON.stringify({ loggedIn: true, rootEntries: root.names.slice(0, 80) }));
    process.exit(0);
  }
  const want = normPath(process.argv[2]);
  const t = want ? await listDir(page, '/' + want) : root;
  const out = { loggedIn: true, target: '/' + want, exists: t.errno === 0, errno: t.errno, entries: t.names.length };
  console.log(JSON.stringify(out));
  process.exit(out.exists ? 0 : 3);
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
