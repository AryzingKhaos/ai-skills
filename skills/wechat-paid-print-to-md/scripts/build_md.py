#!/usr/bin/env python3
"""微信付费文章：打印件(print.pdf) + 全选文本(text.txt) → 本地图文 Markdown 归档。

把"微信浏览器里全选复制得到的纯文本"与"同一篇文章打印成的 PDF"组合还原成
一篇带本地配图的 Markdown：
  - 文案取自 text.txt（其中每个独立成行的「图片」是配图占位）；
  - 配图直接从 print.pdf 内嵌图里抽取（Chromium 打印件通常已内嵌原始分辨率图）；
  - 按"占位顺序 = 正文图顺序"做 1:1 对位，评论区头像/页脚图标等小图自动排除。

用法（3 个路径参数必填、均无默认值）:
  python3 build_md.py \
      --text /path/to/text.txt \
      --pdf  /path/to/print.pdf \
      --out  /path/to/保存母目录 \
      [--seq 37] [--title "标题"] \
      [--account 公众号] [--author 作者] [--date 2026-06-22] [--prev 上一篇] [--series 系列] \
      [--min-width 600] [--min-bytes 50000] [--expect N] [--force] \
      [--static-nums "0,1,5"]

含【视频】的文章（如演示类教程）：视频在 text.txt 里【不占「图片」占位】，但在打印件里会留下
"视频帧/空白播放框"被一并抽出 → 大图数 ≠ 占位数 → 脚本报 MISMATCH。此时：
  1) 看脚本打印的"大图清单+分类标注"，视觉读 print.pdf 确定哪些 num 是真正的静态正文图；
  2) 用 --static-nums "n1,n2,..."（按出现顺序）精确指定，脚本即可正确放置静态图；
  3) 视频另行手工处理：在 md 对应位置加 📹 注释（时长+内容），有首帧就作示意图存 images/（见 SKILL.md）。

产物: <--out>/<序号>-<标题>/<序号>-<标题>.md  +  images/01.<ext> .. NN.<ext>

退出码: 0 成功; 2 参数/路径错误; 3 正文图数与占位数不一致(需人工核对/给 --static-nums, 除非 --force)。
"""
import argparse, glob, os, re, shutil, subprocess, sys, tempfile


def parse_size(s):
    m = re.match(r'([\d.]+)\s*([BKM]?)', s.strip())
    if not m:
        return 0
    return int(float(m.group(1)) * {'': 1, 'B': 1, 'K': 1024, 'M': 1024 * 1024}[m.group(2)])


def list_images(pdf):
    """解析 `pdfimages -list`，返回每张图的 page/num/type/宽/高/字节数（按 num=阅读顺序）。"""
    out = subprocess.run(['pdfimages', '-list', pdf], capture_output=True, text=True).stdout
    rows = []
    for ln in out.splitlines():
        p = ln.split()
        if len(p) >= 15 and p[0].isdigit() and p[1].isdigit() and p[2] in ('image', 'smask', 'stencil'):
            rows.append(dict(page=int(p[0]), num=int(p[1]), type=p[2],
                             w=int(p[3]), h=int(p[4]), size=parse_size(p[-2])))
    return rows


def classify(path):
    """粗判大图类型(辅助挑 --static-nums)：用平均色+方差。
    近纯白=空白视频框；很暗=深色截图(常为视频帧/暗色编辑器)；其余=正文图候选。PIL 缺失时返回 '?'。"""
    try:
        from PIL import Image
        im = Image.open(path).convert('RGB').resize((24, 24))
        px = list(im.getdata())
        avg = sum(sum(p) for p in px) / (len(px) * 3)
        var = sum((sum(p) / 3 - avg) ** 2 for p in px) / len(px)
    except Exception:
        return '?'
    if var < 80 and avg > 200:
        return '空白框'      # 近纯白 = 没渲染出画面的视频播放框
    if avg < 70:
        return '暗截图'      # 深色 = 多为视频帧/暗色编辑器截图
    return '正文图?'


# ——— 代码围栏：把正文里的代码段包成 ```lang ———
_KW = (r'^(import|export|const|let|var|function|async|await|return|if|else|for|while|do|new|class'
       r'|try|catch|finally|switch|case|break|continue|throw|default|interface|type|enum)\b')
_SH = r'^(mkdir|cd|npm|pnpm|npx|node|echo|git|ls|cat|chmod|source|touch|rm|cp|mv|curl|wget|pwd|docker|kubectl|nest)\b'
# SQL / Cypher 语句起始关键字（大小写不敏感匹配）
_SQL = (r'^(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|MATCH|MERGE|RETURN|UNWIND|DETACH'
        r'|WITH|WHERE|FROM|VALUES|JOIN)\b')


def _is_code(t):
    t = t.strip()
    if not t:
        return False
    if re.match(r'^(//|/\*|\*/?|#\s)', t):                       # 注释(含中文)
        return True
    if '//' in t and '://' not in t:                            # 行内注释(排除URL)
        return True
    if re.match(_KW, t):                                         # JS/TS 关键字
        return True
    if re.match(_SQL, t, re.I):                                  # SQL/Cypher 关键字
        return True
    if re.match(_SH, t) and not re.search(r'[一-鿿]', t):       # shell 命令(无中文)
        return True
    if re.match(r'^[A-Z][A-Z0-9_]*=', t):                       # ENV=val
        return True
    if re.match(r'^[A-Za-z_$][\w$.\-]*\s*:(\s|$)', t):          # ASCII 键: (对象属性/yaml键, 值可含中文)
        return True
    if re.match(r"""^['"].*['"][\s,;)\]]*$""", t):              # 整行就是字符串字面量(数组元素等, 可含中文)
        return True
    if re.match(r"^[\w$]+\s*:\s*['\"`]", t):                     # key: 'str'
        return True
    if re.search(r'[{};]\s*$', t):                              # 以 { } ; 结尾
        return True
    if re.match(r'^[\]\)};,]+\s*$', t):                         # 纯收尾括号
        return True
    if re.search(r'=>|===|!==', t):
        return True
    if re.search(r'\b\w+\([^)]*\)', t) and re.search(r'[{}();=]', t):   # 函数调用
        return True
    if re.search(r"['\"]\s*[,;]?\s*$", t) and re.search(r'[:=]', t):    # …'值',
        return True
    if re.match(r"^[\w$.'\"\[\]]+\s*:\s*\S", t) and not re.search(r'[一-鿿]', t):
        return True
    return False


def _line_kind(l, tpl):
    t = l.strip()
    if tpl:
        return 'code'
    if t == '':
        return 'blank'
    if re.match(r'^(!\[|>|#{2,6}\s|---$|\|)', t):   # 图片/视频引用/##标题/分隔线 → 不是代码
        return 'md'
    has_cjk = bool(re.search(r'[一-鿿]', t))
    if not has_cjk and re.match(r'^(https?://\S+|[\w@./\-]+\.\w+|[\w./\-]+/[\w./\-]*)$', t):
        return 'prose'                              # 裸 URL / 文件路径
    if _is_code(t):
        return 'code'
    if l[:1] not in (' ', '\t', '　'):          # 未缩进且非代码 → 散文
        return 'prose'
    return 'code'


def _detect_lang(block, default):
    nb = [b for b in block if b.strip()]
    if not nb:
        return default
    txt = '\n'.join(block)
    if nb[0].strip().startswith('{') and re.search(r'"\w+"\s*:', txt):
        return 'json'
    def sh(t):
        t = t.strip()
        return (t == '' or t.startswith('#') or bool(re.match(_SH, t))
                or bool(re.match(r'^[A-Z][A-Z0-9_]*=', t)))
    if all(sh(b) for b in block):
        return 'bash'
    has_js = bool(re.search(r'=>|\b(const|let|var|import|export|function|await|async|require)\b', txt))
    # SQL/Cypher：较多行以 SQL 关键字起始，且无 JS 结构
    sql_starts = sum(1 for b in nb if re.match(_SQL, b.strip(), re.I))
    if not has_js and sql_starts >= max(1, len(nb) // 3):
        return 'sql'
    # YAML：多数行是 key: 或 - item，且无 JS / 大括号结构
    yk = sum(1 for b in nb if re.match(r'^\s*[\w.\-]+:(\s|$)', b) or re.match(r'^\s*-\s', b))
    if not has_js and not re.search(r'[{}]', txt) and yk >= max(2, len(nb) * 2 // 3):
        return 'yaml'
    return default


def fence_code(lines, default='typescript'):
    """把 body 行里连续的代码段包成 ```lang…```（语言：默认 typescript，纯 shell/.env→bash，JSON→json）。
    用反引号模板状态跟踪，确保多行中文模板字符串(如 SystemMessage/case1 prompt)整段留在代码块内。"""
    n = len(lines)
    marks, tpl = [], False
    for l in lines:
        marks.append(_line_kind(l, tpl))
        if l.count('`') % 2 == 1:
            tpl = not tpl
    blocks, i = {}, 0
    while i < n:
        if marks[i] == 'code':
            last, k = i, i + 1
            while k < n and marks[k] in ('code', 'blank'):
                if marks[k] == 'code':
                    last = k
                k += 1
            blocks[i] = last
            i = last + 1
        else:
            i += 1
    out, i = [], 0
    while i < n:
        if i in blocks:
            e = blocks[i]
            out.append('```' + _detect_lang(lines[i:e + 1], default))
            out += lines[i:e + 1]
            out.append('```')
            i = e + 1
        else:
            out.append(lines[i])
            i += 1
    return out


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--text', required=True, help='微信全选文本 text.txt 路径（必填，无默认）')
    ap.add_argument('--pdf',  required=True, help='打印件 print.pdf 路径（必填，无默认）')
    ap.add_argument('--out',  required=True, help='保存母目录（必填，无默认）；产物为 <out>/<序号>-<标题>/')
    ap.add_argument('--seq',     default=None, help='文章序号；缺省时取 text.txt 所在父目录名')
    ap.add_argument('--title',   default=None, help='文章标题；缺省时取 text.txt 第一行（去掉"已付费"）')
    ap.add_argument('--account', default=None, help='公众号名')
    ap.add_argument('--author',  default=None, help='作者/署名')
    ap.add_argument('--date',    default=None, help='发布日期')
    ap.add_argument('--prev',    default=None, help='上一篇标题')
    ap.add_argument('--series',  default=None, help='所属系列/合集')
    ap.add_argument('--min-width', type=int, default=600,   help='正文图最小像素宽（默认 600，用于排除头像/图标）')
    ap.add_argument('--min-bytes', type=int, default=50000, help='正文图最小字节数（默认 50000）')
    ap.add_argument('--expect',    type=int, default=None,  help='期望正文图数；默认=text.txt 中「图片」占位数')
    ap.add_argument('--force', action='store_true', help='图数≠占位数时仍按顺序强行构建')
    ap.add_argument('--static-nums', default=None,
                    help='逗号分隔的内嵌图 num（按出现顺序），精确指定哪些是静态正文图，'
                         '用于含视频/装饰图、自动筛选不准的文章。给了它就不再按尺寸自动筛。')
    ap.add_argument('--heading', action='append', default=[],
                    help='把正文里"和此文本完全相同的整行"提升为 ## 标题（可多次传）。'
                         '"总结" 默认自动识别，无需传。')
    ap.add_argument('--code-lang', default='typescript',
                    help='代码段默认语言（默认 typescript；纯 shell/.env 自动判为 bash、JSON 判为 json）。')
    ap.add_argument('--no-fence', action='store_true', help='不要自动给代码段加 ``` 围栏。')
    a = ap.parse_args()

    # —— 校验三个必填路径 ——
    if not os.path.isfile(a.text):
        print(f'ERROR(2): --text 文件不存在: {a.text}'); sys.exit(2)
    if not os.path.isfile(a.pdf):
        print(f'ERROR(2): --pdf 文件不存在: {a.pdf}'); sys.exit(2)
    if not os.path.isdir(a.out):
        print(f'ERROR(2): --out 母目录不存在: {a.out}'); sys.exit(2)

    raw = open(a.text, encoding='utf-8', errors='replace').read().split('\n')
    title = a.title or re.sub(r'\s*已?付费\s*$', '', raw[0].strip()).strip()
    seq = a.seq or os.path.basename(os.path.dirname(os.path.abspath(a.text)))
    folder = f'{seq}-{title}'
    outdir = os.path.join(a.out, folder)
    outimg = os.path.join(outdir, 'images')

    placeholders = sum(1 for l in raw if l.strip() == '图片')
    expect = a.expect if a.expect is not None else placeholders

    # —— 抽出 PDF 内嵌图 ——
    tmp = tempfile.mkdtemp(prefix='wxprint_')
    subprocess.run(['pdfimages', '-all', a.pdf, os.path.join(tmp, 'img')], check=True)
    files = {}
    for f in glob.glob(os.path.join(tmp, 'img-*')):
        m = re.search(r'img-(\d+)\.', os.path.basename(f))
        if m:
            files[int(m.group(1))] = f

    rows = list_images(a.pdf)
    big = sorted([r for r in rows if r['type'] == 'image' and r['w'] >= a.min_width],
                 key=lambda r: r['num'])
    for r in big:
        r['label'] = classify(files.get(r['num'], ''))

    if a.static_nums:
        want = [int(x) for x in a.static_nums.replace('，', ',').split(',') if x.strip()]
        bynum = {r['num']: r for r in big}
        missing = [n for n in want if n not in bynum]
        if missing:
            print(f'ERROR(2): --static-nums 含未识别的 num {missing}；可用大图 num: {[r["num"] for r in big]}')
            shutil.rmtree(tmp, ignore_errors=True); sys.exit(2)
        content = [bynum[n] for n in want]          # 严格按给定顺序
    else:
        content = [r for r in big if r['size'] >= a.min_bytes]

    print(f'「图片」占位: {placeholders}   期望正文图: {expect}   选定正文图: {len(content)}')
    print(f'PDF 内嵌大图清单(≥{a.min_width}px，✔=本次选为正文图；挑 --static-nums 用):')
    for r in big:
        mark = '✔' if r in content else ' '
        print(f"  {mark} num{r['num']:>2} p{r['page']} {r['w']}x{r['h']} {r['size']//1024}K [{r.get('label','?')}]")
    small = sum(1 for r in rows if r['type'] == 'image' and r['w'] < a.min_width)
    if small:
        print(f'(另排除 {small} 张 <{a.min_width}px 的小图：头像/图标/二维码等)')

    if len(content) != expect and not a.force:
        print(f'\nMISMATCH(3): 选定正文图({len(content)}) ≠ 占位/期望({expect})。'
              f'\n→ 多因文章含【视频】或装饰图：视频在 text.txt 不占「图片」位，却在打印件留下视频帧/空白框。'
              f'\n→ 视觉读 print.pdf，确定真正的静态正文图，用 --static-nums "n1,n2,…"(按出现顺序) 重跑；'
              f'\n  上表 [空白框]=没渲染出的视频框、[暗截图]=多为视频帧，一般都不是正文图。'
              f'\n→ 视频另用 📹 注释手工补（见 SKILL.md）；某正文图打印空白则回退微信缓存补图；纯多/少几张可 --force。')
        shutil.rmtree(tmp, ignore_errors=True)
        sys.exit(3)

    # —— 落盘：复制图片（保留真实扩展名） ——
    os.makedirs(outimg, exist_ok=True)
    refs = []
    for i, r in enumerate(content, 1):
        src = files.get(r['num'])
        if not src:
            print(f'ERROR: 抽图缺少 num{r["num"]} 对应文件'); shutil.rmtree(tmp, ignore_errors=True); sys.exit(3)
        ext = os.path.splitext(src)[1].lower() or '.png'
        dst = f'{i:02d}{ext}'
        shutil.copy(src, os.path.join(outimg, dst))
        refs.append(dst)

    # —— 组装正文：截断评论区；「图片」换图片引用；标题行提升为 ## ——
    headings = set(h.strip() for h in a.heading) | {'总结'}   # 总结 自动识别
    body, img_n = [], 0
    for l in raw[1:]:
        s = l.strip()
        if re.fullmatch(r'留言\s*\d*', s):   # 评论区起点：'留言' 或 '留言 64'
            break
        if s in ('写留言', '暂无评论'):
            continue
        if s == '图片':
            img_n += 1
            ref = f'images/{refs[img_n-1]}' if img_n <= len(refs) else 'images/MISSING.png'
            body += [f'![图{img_n}]({ref})', '']
        elif s and s in headings:
            body += ['', f'## {s}', '']       # 标题独立成段
        else:
            body.append(l)
    out, pb = [], False
    for x in body:
        b = (x.strip() == '')
        if b and pb:
            continue
        out.append(x); pb = b
    while out and out[-1].strip() == '':
        out.pop()

    if not a.no_fence:
        out = fence_code(out, a.code_lang)   # 代码段包 ```lang

    def meta(k, v):
        return f'> {k}：{v}\n' if v else f'> {k}：（待人工核对·从 print.pdf 抬头/页脚补全）\n'

    header = (f'# {folder}\n\n'
              + meta('公众号', a.account) + meta('作者', a.author) + meta('发布', a.date)
              + meta('系列', a.series) + meta('上一篇', a.prev)
              + f'> 说明：付费文章的个人本地归档。正文取自微信全选文本（{os.path.basename(a.text)}），'
                f'版式与配图对照打印件（{os.path.basename(a.pdf)}）还原；'
                f'{len(content)} 张正文配图为打印件内嵌原图、存于 images/；评论区头像/页脚图标等非正文已剔除。\n\n'
              + '---\n\n')
    md = header + '\n'.join(out) + '\n'
    open(os.path.join(outdir, f'{folder}.md'), 'w', encoding='utf-8').write(md)
    shutil.rmtree(tmp, ignore_errors=True)

    print(f'\n✅ 写出: {outdir}/{folder}.md')
    print(f'   配图: {len(content)} 张 → images/01..{len(content):02d}')
    print(f'   正文「图片」占位替换: {img_n}')
    if img_n != len(content):
        print(f'   ⚠️ 占位({img_n})与配图({len(content)})数不等，请核对映射！')
    miss = [k for k in ('account', 'author', 'date', 'prev', 'series') if not getattr(a, k)]
    if miss:
        print(f'   ⚠️ 头部元信息待补: {miss}（视觉读 print.pdf 第1页/末页后用对应 --flag 重跑，或直接编辑 md 头部）')
    hd = [h for h in headings if h != '总结']
    print(f'\n   标题已提升为 ##: {sorted(headings)}')
    print('\n模型必做核对: ①视觉读 print.pdf 确认每张图与占位语义对位(尤其同页多图)；'
          '②补全公众号/作者/日期/上一篇/系列；③标题行用 --heading 补全(本次已含"总结"'
          + ('、' + '、'.join(hd) if hd else '') + ')；'
          '④若文章含视频→在对应位置手工加 📹 注释(+首帧示意，见 SKILL.md)；'
          '⑤某正文图打印空白→回退微信缓存补图。')


if __name__ == '__main__':
    main()
