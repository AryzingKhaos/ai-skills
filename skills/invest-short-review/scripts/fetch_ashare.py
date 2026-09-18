#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股个股行情拉取 + 剧烈变动(异动)识别。

确定性部分（短期向）：拉日线行情，算出
  1) 单日异动：|当日涨跌幅| >= day_threshold（默认 5%，含涨停/跌停标注）
  2) 短窗波段：连续若干交易日累计涨跌 >= swing_threshold（默认 10%，窗口默认 5 个交易日）
并以 JSON 输出到 stdout，交给上层(LLM)逐个联网查原因、写复盘报告。
（默认面向"近一周"短期复盘；如需长期，显式传更长的 --start/--end 与更大的阈值/窗口即可。）

数据源：AKShare 优先；不可用/被限时三级回退：akshare → 东方财富 → 新浪（后两者纯 stdlib）。

用法：
  python3 fetch_ashare.py 688981                      # 缺省=近 5~7 交易日（短期）
  python3 fetch_ashare.py 300750 --start 20260601 --end 20260620
  python3 fetch_ashare.py 002371 --start 20260601 --end 20260620 \
      --day-threshold 5 --swing-threshold 10 --swing-window 5 --adjust qfq

注意：精确起见，建议由调用方(LLM)按 system 的 currentDate 计算 start/end 传入；
      不传则用本机日期兜底（end=今天，start≈end 前 10 自然日）。
"""

import argparse
import datetime
import json
import sys
import urllib.request


def board_and_limit(code, name):
    """根据代码段与名称(是否 ST)推断板块与每日涨跌停幅度(%)。"""
    name = name or ""
    is_st = ("ST" in name.upper()) or ("退" in name)
    if is_st:
        return ("ST/风险警示", 5.0)
    if code.startswith("688"):
        return ("科创板", 20.0)
    if code.startswith("30"):
        return ("创业板", 20.0)
    if code.startswith(("8", "4", "920")):
        return ("北交所", 30.0)
    if code.startswith(("60", "00")):
        return ("沪深主板", 10.0)
    return ("未知", 10.0)


def _f(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def fetch_akshare(code, start, end, adjust):
    """用 akshare 拉日线，返回 (rows, name, warnings)。失败抛异常由上层 catch。"""
    import akshare as ak

    warnings = []
    name = ""
    try:
        info = ak.stock_individual_info_em(symbol=code)
        # info 是两列 df: item / value
        d = dict(zip(info["item"], info["value"]))
        name = str(d.get("股票简称") or d.get("简称") or "").strip()
    except Exception as e:  # 名称拿不到不致命
        warnings.append(f"akshare 取股票简称失败：{e!r}")

    df = ak.stock_zh_a_hist(
        symbol=code, period="daily", start_date=start, end_date=end, adjust=adjust
    )
    if df is None or len(df) == 0:
        raise RuntimeError("akshare 返回空数据")

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "date": str(r.get("日期")),
            "open": _f(r.get("开盘")),
            "close": _f(r.get("收盘")),
            "high": _f(r.get("最高")),
            "low": _f(r.get("最低")),
            "volume": _f(r.get("成交量")),
            "amount": _f(r.get("成交额")),
            "pct": _f(r.get("涨跌幅")),
            "turnover": _f(r.get("换手率")),
        })
    return rows, name, warnings


def fetch_eastmoney(code, start, end, adjust):
    """东方财富公开 K 线接口回退，纯 stdlib。返回 (rows, name, warnings)。"""
    warnings = []
    secid = ("1." if code.startswith("6") else "0.") + code
    fqt = {"qfq": 1, "hfq": 2, "": 0, "none": 0}.get(adjust, 1)
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&klt=101&fqt={fqt}"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&beg={start}&end={end}"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    data = payload.get("data")
    if not data or not data.get("klines"):
        raise RuntimeError("东方财富接口返回空 klines")
    name = str(data.get("name") or "").strip()

    rows = []
    # f51..f61 顺序: date,open,close,high,low,volume,amount,amplitude,pct,changeamt,turnover
    for line in data["klines"]:
        p = line.split(",")
        rows.append({
            "date": p[0],
            "open": _f(p[1]),
            "close": _f(p[2]),
            "high": _f(p[3]),
            "low": _f(p[4]),
            "volume": _f(p[5]),
            "amount": _f(p[6]),
            "pct": _f(p[8]),
            "turnover": _f(p[10]) if len(p) > 10 else None,
        })
    return rows, name, warnings


def fetch_sina(code, start, end):
    """新浪日线接口回退（纯 stdlib）。返回 (rows, name, warnings)。
    注意：sina 该接口为【不复权】日线，且不含成交额/换手率；涨跌幅按相邻收盘价计算。"""
    warnings = []
    if code.startswith("6"):
        sym = "sh" + code
    elif code.startswith(("8", "4", "920")):
        sym = "bj" + code
    else:
        sym = "sz" + code
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        f"?symbol={sym}&scale=240&ma=no&datalen=1023"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn/",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        arr = json.loads(resp.read().decode("utf-8"))
    if not arr:
        raise RuntimeError("sina 返回空数据")

    full = []
    for it in arr:
        day = str(it.get("day", ""))[:10]
        full.append({
            "date": day,
            "open": _f(it.get("open")),
            "close": _f(it.get("close")),
            "high": _f(it.get("high")),
            "low": _f(it.get("low")),
            "volume": _f(it.get("volume")),
            "amount": None,
            "pct": None,
            "turnover": None,
        })
    full.sort(key=lambda r: r["date"])
    # 在完整序列上算涨跌幅，再裁剪区间（保证区间首日的 pct 也基于前一交易日）
    prev = None
    for r in full:
        if prev and prev["close"]:
            r["pct"] = round((r["close"] / prev["close"] - 1) * 100, 4)
        prev = r

    def k(d):
        return d.replace("-", "")

    rows = [r for r in full if start <= k(r["date"]) <= end]
    if rows and k(rows[0]["date"]) > start:
        warnings.append(f"sina 仅回溯到 {rows[0]['date']}，早于该日的区间未覆盖（datalen 上限）。")

    name = ""
    try:  # 名称走 sina 实时接口（GBK），拿不到不致命
        nreq = urllib.request.Request("https://hq.sinajs.cn/list=" + sym, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/",
        })
        with urllib.request.urlopen(nreq, timeout=10) as nr:
            txt = nr.read().decode("gbk", "ignore")
        inner = txt.split('"')[1] if '"' in txt else ""
        name = inner.split(",")[0].strip()
    except Exception:
        pass

    warnings.append("sina 通道为【不复权】日线，涨跌幅按相邻收盘价计算；成交额/换手率缺省。")
    return rows, name, warnings


def detect_single_day(rows, day_threshold, limit_pct):
    """单日 |涨跌幅| >= day_threshold 的异动点。"""
    events = []
    near = max(0.5, limit_pct * 0.05)  # 距涨跌停的容差
    for r in rows:
        pct = r["pct"]
        if pct is None:
            continue
        if abs(pct) >= day_threshold:
            events.append({
                "date": r["date"],
                "pct": pct,
                "close": r["close"],
                "amount_yi": round(r["amount"] / 1e8, 3) if r["amount"] else None,  # 成交额(亿元)
                "turnover": r["turnover"],
                "limit_up": pct >= (limit_pct - near),
                "limit_down": pct <= -(limit_pct - near),
            })
    return events


def detect_swings(rows, swing_threshold, swing_window):
    """贪心扫描：从某交易日起 <= swing_window 个交易日内累计涨跌 >= swing_threshold 的波段。"""
    swings = []
    n = len(rows)
    i = 0
    while i < n - 1:
        base = rows[i]["close"]
        if not base:
            i += 1
            continue
        hit = None
        jmax = min(n - 1, i + swing_window)
        for j in range(i + 1, jmax + 1):
            cj = rows[j]["close"]
            if not cj:
                continue
            cum = (cj / base - 1.0) * 100.0
            if abs(cum) >= swing_threshold:
                hit = (j, cum)
                break
        if hit:
            j, cum = hit
            days = j - i
            if days >= 2:  # 单日剧变交给 single_day，波段只收多日
                swings.append({
                    "start_date": rows[i]["date"],
                    "end_date": rows[j]["date"],
                    "trading_days": days,
                    "start_close": base,
                    "end_close": rows[j]["close"],
                    "cum_pct": round(cum, 2),
                    "direction": "up" if cum > 0 else "down",
                })
                i = j  # 非重叠，从波段末尾继续
            else:
                i += 1
        else:
            i += 1
    return swings


def main():
    ap = argparse.ArgumentParser(description="A股日线拉取 + 剧烈变动识别")
    ap.add_argument("code", help="6 位股票代码，如 688981 / 300750 / 002371")
    ap.add_argument("--start", default=None, help="起始日 YYYYMMDD（建议由调用方按 currentDate 计算；缺省=end 前约 10 自然日，≈近 5~7 交易日）")
    ap.add_argument("--end", default=None, help="结束日 YYYYMMDD（缺省=本机今天；精确起见建议传 currentDate）")
    ap.add_argument("--day-threshold", type=float, default=5.0, help="单日异动阈值(%)，默认 5")
    ap.add_argument("--swing-threshold", type=float, default=10.0, help="短窗累计异动阈值(%)，默认 10")
    ap.add_argument("--swing-window", type=int, default=5, help="波段最大跨度(交易日)，默认 5（短期）")
    ap.add_argument("--adjust", default="qfq", choices=["qfq", "hfq", "none", ""], help="复权方式，默认 qfq(前复权)")
    args = ap.parse_args()

    code = args.code.strip().zfill(6)
    adjust = "" if args.adjust in ("none", "") else args.adjust

    # 时间区间：优先用调用方传入；缺省给短期默认（end=今天，start=end 前约 10 自然日）
    end = args.end or datetime.date.today().strftime("%Y%m%d")
    if args.start:
        start = args.start
    else:
        _e = datetime.datetime.strptime(end, "%Y%m%d").date()
        start = (_e - datetime.timedelta(days=10)).strftime("%Y%m%d")

    warnings = []
    source = None
    rows, name = [], ""

    # 1) akshare 优先
    try:
        rows, name, w = fetch_akshare(code, start, end, adjust)
        warnings += w
        source = "akshare"
    except ImportError:
        warnings.append("未安装 akshare，回退东方财富网页接口（如需更稳可 pip install akshare）。")
    except Exception as e:
        warnings.append(f"akshare 拉取失败({e!r})，回退东方财富网页接口。")

    # 2) 回退东方财富
    if not rows:
        try:
            rows, nm, w = fetch_eastmoney(code, start, end, adjust)
            warnings += w
            if not name:
                name = nm
            source = "eastmoney"
        except Exception as e:
            warnings.append(f"东方财富回退失败({e!r})，再回退新浪。")

    # 3) 回退新浪
    if not rows:
        try:
            rows, nm, w = fetch_sina(code, start, end)
            warnings += w
            if not name:
                name = nm
            source = "sina"
        except Exception as e:
            print(json.dumps({
                "ok": False,
                "code": code,
                "error": f"三条数据通道(akshare/东方财富/新浪)都失败：{e!r}",
                "warnings": warnings,
            }, ensure_ascii=False, indent=2))
            sys.exit(1)

    rows.sort(key=lambda r: r["date"])
    board, limit_pct = board_and_limit(code, name)
    adjust_display = adjust or "none"
    if source == "sina":
        adjust_display = "none(sina不复权)"

    single = detect_single_day(rows, args.day_threshold, limit_pct)
    swings = detect_swings(rows, args.swing_threshold, args.swing_window)

    closes = [r["close"] for r in rows if r["close"]]
    period_return = round((closes[-1] / closes[0] - 1) * 100, 2) if len(closes) >= 2 else None

    out = {
        "ok": True,
        "data_source": source,
        "meta": {
            "code": code,
            "name": name,
            "board": board,
            "daily_limit_pct": limit_pct,
            "adjust": adjust_display,
            "start": start,
            "end": end,
            "trading_days": len(rows),
            "first_date": rows[0]["date"] if rows else None,
            "last_date": rows[-1]["date"] if rows else None,
            "period_return_pct": period_return,
            "thresholds": {
                "day_pct": args.day_threshold,
                "swing_pct": args.swing_threshold,
                "swing_window_days": args.swing_window,
            },
        },
        "summary": {
            "n_single_day_events": len(single),
            "n_limit_up": sum(1 for e in single if e["limit_up"]),
            "n_limit_down": sum(1 for e in single if e["limit_down"]),
            "n_swings": len(swings),
        },
        "single_day_events": single,
        "multi_day_swings": swings,
        "warnings": warnings,
        "note": "single_day_events 与 multi_day_swings 是『需联网查原因』的异动清单；逐个解释催化是本 skill 的核心。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
