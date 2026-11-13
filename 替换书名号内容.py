#!/usr/bin/env python3
"""把文本文件里《xxxx》的内容全部替换成《***》。

用法：python3 替换书名号内容.py <文件或文件夹>... [-y]
      不带参数运行时，可以把文件拖进终端窗口后回车。
规则：
  - 只替换同一行内成对的《》；空的《》和已经是《***》的不算
  - 文件夹只处理里面的 .txt（不含子文件夹）
  - 自动识别 UTF-8 / UTF-8 BOM / UTF-16 / GBK，按原编码、原换行写回
  - 先预览，确认后才写入（-y 跳过确认）；每个改动的文件先备份为 原名.时间戳.bak
"""
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

PATTERN = re.compile(r'《[^《》\r\n]+》')
MASK = '《***》'


def read_text(path):
    """返回 (文本, 编码, BOM 字节, 编码名)。"""
    data = path.read_bytes()
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8'), 'utf-8', b'\xef\xbb\xbf', 'UTF-8 BOM'
    if data.startswith(b'\xff\xfe'):
        return data[2:].decode('utf-16-le'), 'utf-16-le', b'\xff\xfe', 'UTF-16 LE'
    if data.startswith(b'\xfe\xff'):
        return data[2:].decode('utf-16-be'), 'utf-16-be', b'\xfe\xff', 'UTF-16 BE'
    try:
        return data.decode('utf-8'), 'utf-8', b'', 'UTF-8'
    except UnicodeDecodeError:
        # gb18030 是 GBK 的超集，GBK 文件可以原样往返
        return data.decode('gb18030'), 'gb18030', b'', 'GBK'


def collect(args):
    files = []
    for a in args:
        p = Path(a).expanduser()
        if p.is_dir():
            files += sorted(x for x in p.glob('*.txt') if x.is_file() and not x.name.startswith('.'))
        elif p.is_file():
            files.append(p)
        else:
            print(f'找不到：{a}')
    seen, out = set(), []
    for f in files:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def main():
    args = [a for a in sys.argv[1:] if a != '-y']
    yes = '-y' in sys.argv[1:]
    if not args:
        print('把文本文件或文件夹拖进这个窗口后回车，可以分多次拖；全部拖完后直接回车开始。')
        while True:
            line = input('路径：').strip()
            if not line:
                break
            args += shlex.split(line)

    files = collect(args)
    if not files:
        print('没有可处理的文件。')
        return

    print('\n预览（还没有改动）：')
    plan, total = [], 0
    for f in files:
        try:
            text, enc, bom, name = read_text(f)
        except (OSError, UnicodeDecodeError) as e:
            print(f'  读取失败：{f}（{e}）')
            continue
        hits = [m.group() for m in PATTERN.finditer(text) if m.group() != MASK]
        if not hits:
            print(f'  0 处  {f}')
            continue
        print(f'  {len(hits)} 处  {f}（{name}），例如 {" ".join(hits[:3])}')
        plan.append((f, text, enc, bom, len(hits)))
        total += len(hits)
    if not plan:
        print('没有需要替换的内容。')
        return

    if not yes:
        ans = input(f'\n共 {len(plan)} 个文件、{total} 处将替换成 {MASK}，原文件会先备份。确认执行？[y/N] ')
        if not ans.strip().lower().startswith('y'):
            print('已取消，没有改动任何文件。')
            return

    stamp = time.strftime('%Y%m%d-%H%M%S')
    for f, text, enc, bom, n in plan:
        bak = f.with_name(f'{f.name}.{stamp}.bak')
        try:
            shutil.copy2(f, bak)
            f.write_bytes(bom + PATTERN.sub(MASK, text).encode(enc))
            print(f'  已替换 {n} 处：{f}（备份 {bak.name}）')
        except OSError as e:
            print(f'  失败：{f}（{e}）')
    print('完成。')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\n已取消。')
