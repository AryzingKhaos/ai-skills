// Step 2: pull every Baidu netdisk share link (+提取码) out of free text.
// Usage: node extract.js <input.txt> <links.json>
// Writes {count, links:[{key,url,pwd,title}], suspects:[...]}.
// suspects = pan/yun.baidu.com mentions that did not parse as a share link (report them, never guess).
const fs = require('fs');

const CJK = '\\u3000-\\u303f\\u4e00-\\u9fff\\uff00-\\uffef';
const LINK_RE = new RegExp(
  '(?:https?://)?(?:pan|yun)\\.baidu\\.com/(?:s/([A-Za-z0-9_-]+)|share/init\\?[^\\s"\'<>' + CJK + ']*?surl=([A-Za-z0-9_-]+))' +
  '(?:[?#&][^\\s"\'<>' + CJK + ']*)?', 'g');
const CODE_RE = /(?:提取码|提取碼|密码|密碼|访问码|訪問碼|(?<![A-Za-z])pwd|(?<![A-Za-z])code)\s*[:：=]?\s*([A-Za-z0-9]{4})(?![A-Za-z0-9])/gi;
const NOISE_RE = /^(?:-+\s*)?来自百度网盘|复制这段内容|^(?:提取码|提取碼|密码|密碼|访问码|訪問碼)/;

function codesIn(text, offset) {
  const out = [];
  for (const m of text.matchAll(CODE_RE)) out.push({ code: m[1], at: offset + m.index, end: offset + m.index + m[0].length });
  return out;
}

const cleanLabel = (s) => s
  .replace(/(?:通过网盘)?分享的文件[:：]?/g, '')
  .replace(/链接\s*[:：]?/g, '')
  .replace(/百度(?:网盘)?\s*[:：]/g, '')
  .replace(CODE_RE, '')
  .replace(/(?:^|\s)\d{1,3}\s*[.、]\s*$/, '')
  .replace(/[\s(（\[【]+$/, '')
  .trim();

// Display label only. Same-line prefix if it has content ("【经济学原理】链接: …"), else the nearest
// preceding line without a URL/code (e.g. "通过网盘分享的文件：xxx" or a series header), plus " #n" numbering.
function guessTitle(text, start, prevEnd) {
  const lineStart = text.lastIndexOf('\n', start - 1) + 1;
  const prefix = text.slice(Math.max(lineStart, prevEnd), start);
  const num = (prefix.match(/(\d{1,3})\s*[.、]\s*(?:链接\s*[:：]?\s*)?$/) || [])[1];
  const same = cleanLabel(prefix);
  if (same.length >= 2) return same.slice(0, 80);
  const lines = text.slice(0, lineStart).split('\n').reverse().slice(0, 6);
  for (const raw of lines) {
    if (!raw.trim() || /https?:\/\/|(?:pan|yun)\.baidu\.com/.test(raw) || NOISE_RE.test(raw.trim())) continue;
    const t = cleanLabel(raw);
    if (t.length >= 2) return t.slice(0, 80) + (num ? ` #${num}` : '');
  }
  return num ? `#${num}` : '';
}

function extractLinks(text) {
  const hits = [...text.matchAll(LINK_RE)].map((m) => ({
    start: m.index, end: m.index + m[0].length, raw: m[0],
    key: m[1] || '1' + m[2],
  }));
  const used = new Set();  // code positions already claimed
  let prevEnd = 0;
  const links = [];
  hits.forEach((h, i) => {
    const nextStart = i + 1 < hits.length ? hits[i + 1].start : text.length;
    let pwd = (h.raw.match(/[?&#]pwd=([A-Za-z0-9]{4})/) || [])[1] || '';
    let codeEnd = h.end;
    if (!pwd) {
      // after the link, up to the next link; a code on the next link's own line belongs to that link
      const after = codesIn(text.slice(h.end, Math.min(nextStart, h.end + 200)), h.end)
        .filter((c) => !(i + 1 < hits.length && !text.slice(c.end, nextStart).includes('\n')));
      if (after.length) { pwd = after[0].code; used.add(after[0].at); codeEnd = after[0].end; }
    }
    if (!pwd) {
      // same line, before the link: "密码 abcd 链接 https://..."
      const lineStart = Math.max(prevEnd, text.lastIndexOf('\n', h.start - 1) + 1);
      const before = codesIn(text.slice(lineStart, h.start), lineStart).filter((c) => !used.has(c.at));
      if (before.length) { const c = before[before.length - 1]; pwd = c.code; used.add(c.at); }
    }
    links.push({
      key: h.key,
      url: `https://pan.baidu.com/s/${h.key}` + (pwd ? `?pwd=${pwd}` : ''),
      pwd,
      title: guessTitle(text, h.start, prevEnd),
    });
    prevEnd = Math.max(h.end, codeEnd);
  });

  const byKey = new Map();
  for (const l of links) {
    const old = byKey.get(l.key);
    if (!old) byKey.set(l.key, l);
    else { if (!old.pwd && l.pwd) { old.pwd = l.pwd; old.url = l.url; } if (!old.title) old.title = l.title; }
  }

  const suspects = [];
  const snippet = (i) => text.slice(Math.max(0, i - 20), i + 60).replace(/\s+/g, ' ');
  for (const m of text.matchAll(/(?:pan|yun)\.baidu\.com/g)) {
    if (!hits.some((h) => m.index >= h.start && m.index < h.end)) suspects.push(snippet(m.index));
  }
  // non-baidu URL followed by a 提取码 = a third-party short link; the user must swap in the real pan.baidu.com link
  for (const m of text.matchAll(/https?:\/\/[^\s"'<>，。；]+/g)) {
    if (/(?:pan|yun)\.baidu\.com/.test(m[0])) continue;
    const tail = text.slice(m.index + m[0].length, m.index + m[0].length + 30);
    if (codesIn(tail, 0).length) suspects.push('非百度直链(短链?) ' + snippet(m.index));
  }
  return { links: [...byKey.values()], suspects };
}

// Third-party short links (e.g. v.v8l.cn/s/xxx) show desktop browsers only a "仅允许手机端访问" QR page,
// but 302 a mobile UA to the real pan.baidu.com link. Mobile UA use approved by the user (2026-11-10).
const MOBILE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const BAIDU_URL_RE = /https?:\/\/(?:pan|yun)\.baidu\.com\/(?:s\/[A-Za-z0-9_-]+|share\/init\?[^\s"'<>]*?surl=[A-Za-z0-9_-]+)[^\s"'<>]*/;

async function resolveOne(url) {
  let cur = url;
  for (let hop = 0; hop < 6; hop++) {
    if (BAIDU_URL_RE.test(cur)) return { url: cur.match(BAIDU_URL_RE)[0] };
    const r = await fetch(cur, { redirect: 'manual', headers: { 'User-Agent': MOBILE_UA }, signal: AbortSignal.timeout(10000) });
    const loc = r.headers.get('location');
    if (r.status >= 300 && r.status < 400 && loc) { cur = new URL(loc, cur).href; continue; }
    // meta-refresh / JS redirect pages: take the first baidu share URL in the body
    const m = (await r.text()).match(BAIDU_URL_RE);
    return m ? { url: m[0] } : { error: `HTTP ${r.status}，未跳转到百度` };
  }
  return { error: '跳转次数过多' };
}

// Rewrite every non-baidu URL that is followed by a 提取码 into its resolved pan.baidu.com URL.
async function resolveShortLinks(text) {
  const resolved = [], failed = [];
  const seen = new Map();
  for (const m of [...text.matchAll(/https?:\/\/[^\s"'<>，。；]+/g)]) {
    if (/(?:pan|yun)\.baidu\.com/.test(m[0]) || seen.has(m[0])) continue;
    const tail = text.slice(m.index + m[0].length, m.index + m[0].length + 30);
    if (!codesIn(tail, 0).length) continue;
    const r = await resolveOne(m[0]).catch((e) => ({ error: e.message.slice(0, 60) }));
    seen.set(m[0], r);
    if (r.url) resolved.push({ short: m[0], url: r.url }); else failed.push({ short: m[0], error: r.error });
    await new Promise((res) => setTimeout(res, 500));
  }
  for (const { short, url } of resolved) text = text.split(short).join(url);
  return { text, resolved, failed };
}

module.exports = { extractLinks, resolveShortLinks };

if (require.main === module) {
  (async () => {
    const [inPath, outPath] = process.argv.slice(2);
    if (!inPath || !outPath) { console.error('usage: node extract.js <input.txt> <links.json>'); process.exit(1); }
    const { text, resolved, failed } = await resolveShortLinks(fs.readFileSync(inPath, 'utf8'));
    const { links, suspects } = extractLinks(text);
    for (const r of resolved) {
      const l = links.find((x) => r.url.includes('/s/' + x.key) || r.url.includes('surl=' + x.key.slice(1)));
      if (l) l.via = r.short;
    }
    fs.writeFileSync(outPath, JSON.stringify({ count: links.length, links, suspects, shortLinks: { resolved, failed } }, null, 2));
    links.forEach((l, i) => console.log(`${i + 1}. ${l.key} | 提取码=${l.pwd || '(无)'} | ${l.title}${l.via ? ' | 短链 ' + l.via : ''}`));
    if (failed.length) { console.log(`SHORTLINK-FAILED ${failed.length}:`); failed.forEach((f) => console.log(`  ${f.short} | ${f.error}`)); }
    if (suspects.length) { console.log(`SUSPECTS ${suspects.length}:`); suspects.forEach((s) => console.log('  ' + s)); }
    console.log(`COUNT ${links.length} -> ${outPath}`);
  })().catch((e) => { console.error('ERR', e.message); process.exit(1); });
}
