#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股个股行情拉取 + 短期剧烈变动(异动)识别。

确定性部分（短期向）：拉日线行情，算出
  1) 单日异动：|当日涨跌幅| >= day_threshold（默认 5%；并给隔夜跳空 gap 与日内 intraday 分解）
  2) 短窗波段：连续若干交易日累计涨跌 >= swing_threshold（默认 10%，窗口默认 5 个交易日）
并以 JSON 输出到 stdout，交给上层(LLM)逐个联网查原因、写复盘报告。
（美股无涨跌停；earnings 常在盘后，故对单日异动额外拆出『隔夜 gap vs 日内』，帮助区分财报/消息 vs 盘中资金。）

数据源：免费免 key 两级回退：Stooq(CSV) → Yahoo Finance Chart(JSON)，均为纯标准库。

用法：
  python3 fetch_usstock.py NVDA                       # 缺省=近 5~7 交易日（短期）
  python3 fetch_usstock.py AMD --start 20260601 --end 20260620
  python3 fetch_usstock.py TSM --start 20260601 --end 20260620 \
      --day-threshold 5 --swing-threshold 10 --swing-window 5

注意：精确起见，建议由调用方(LLM)按 system 的 currentDate 计算 start/end 传入；
      不传则用本机日期兜底（end=今天，start≈end 前 10 自然日）。
"""

import argparse
import csv
import datetime
import io
import json
import sys
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _f(x):
    try:
        if x in (None, "", "N/D", "null"):
            return None
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def fetch_stooq(ticker, beg, end):
    """Stooq 日线 CSV。返回 (full_rows, name, warnings)。full_rows 覆盖 beg..end(含缓冲)。"""
    s = ticker.lower().replace(".", "-")
    url = f"https://stooq.com/q/d/l/?s={s}.us&i=d&d1={beg}&d2={end}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8", "ignore")
    if not text or not text.startswith("Date"):
        raise RuntimeError(f"stooq 异常返回（疑限频/无此代码）：{text[:60]!r}")
    rows = []
    for d in csv.DictReader(io.StringIO(text)):
        c = _f(d.get("Close"))
        if c is None:
            continue
        rows.append({
            "date": d.get("Date"),
            "open": _f(d.get("Open")),
            "high": _f(d.get("High")),
            "low": _f(d.get("Low")),
            "close": c,
            "volume": _f(d.get("Volume")),
        })
    if not rows:
        raise RuntimeError("stooq 解析为空")
    return rows, "", ["Stooq 为拆股调整价（未做分红调整）。"]


def fetch_yahoo(ticker, beg, end):
    """Yahoo Finance Chart API（JSON）。多重尝试：query1/query2 + period 与 range 两种参数，抗 429。
    返回 (full_rows, name, warnings)。"""
    p1 = int(datetime.datetime.strptime(beg, "%Y%m%d")
             .replace(tzinfo=datetime.timezone.utc).timestamp())
    p2 = int((datetime.datetime.strptime(end, "%Y%m%d") + datetime.timedelta(days=2))
             .replace(tzinfo=datetime.timezone.utc).timestamp())
    span = (datetime.datetime.strptime(end, "%Y%m%d") - datetime.datetime.strptime(beg, "%Y%m%d")).days
    rng = ("1mo" if span <= 25 else "3mo" if span <= 80 else "6mo" if span <= 170
           else "1y" if span <= 350 else "2y" if span <= 700 else "5y")
    t = ticker.upper()
    attempts = [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?period1={p1}&period2={p2}&interval=1d",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{t}?period1={p1}&period2={p2}&interval=1d",
        f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range={rng}&interval=1d",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{t}?range={rng}&interval=1d",
    ]
    payload, errs = None, []
    for u in attempts:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if (payload.get("chart") or {}).get("result"):
                break
            payload = None
        except Exception as e:
            errs.append(repr(e))
            payload = None
    if not payload:
        raise RuntimeError("yahoo 全部尝试失败：" + "; ".join(errs[:3]))
    res = (payload.get("chart") or {}).get("result")
    if not res:
        raise RuntimeError("yahoo 无 result")
    r0 = res[0]
    ts = r0.get("timestamp") or []
    q = (r0.get("indicators") or {}).get("quote", [{}])[0]
    meta = r0.get("meta") or {}
    name = meta.get("shortName") or meta.get("longName") or ""
    opens, highs = q.get("open") or [], q.get("high") or []
    lows, closes = q.get("low") or [], q.get("close") or []
    vols = q.get("volume") or []
    rows = []
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        dt = datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")
        rows.append({
            "date": dt,
            "open": _f(opens[i] if i < len(opens) else None),
            "high": _f(highs[i] if i < len(highs) else None),
            "low": _f(lows[i] if i < len(lows) else None),
            "close": _f(c),
            "volume": _f(vols[i] if i < len(vols) else None),
        })
    if not rows:
        raise RuntimeError("yahoo 解析为空")
    return rows, name, ["Yahoo close 为未复权收盘（adjclose 另存，本脚本未取）。"]


def finalize(full, start, end):
    """完整序列上算 prev_close/pct，再裁剪到 [start, end]。"""
    full.sort(key=lambda r: r["date"])
    prev = None
    for r in full:
        r["prev_close"] = prev["close"] if prev else None
        if prev and prev["close"]:
            r["pct"] = round((r["close"] / prev["close"] - 1) * 100, 4)
        else:
            r["pct"] = None
        prev = r

    def k(d):
        return d.replace("-", "")

    return [r for r in full if start <= k(r["date"]) <= end]


def detect_single_day(rows, day_threshold):
    events = []
    for r in rows:
        if r["pct"] is None or abs(r["pct"]) < day_threshold:
            continue
        pc, op = r.get("prev_close"), r.get("open")
        gap = round((op / pc - 1) * 100, 2) if (pc and op) else None        # 隔夜跳空(盘后消息)
        intra = round((r["close"] / op - 1) * 100, 2) if op else None        # 日内
        events.append({
            "date": r["date"],
            "pct": r["pct"],
            "close": r["close"],
            "gap_pct": gap,
            "intraday_pct": intra,
            "volume": r["volume"],
        })
    return events


def detect_swings(rows, swing_threshold, swing_window):
    swings, n, i = [], len(rows), 0
    while i < n - 1:
        base = rows[i]["close"]
        if not base:
            i += 1
            continue
        hit = None
        for j in range(i + 1, min(n - 1, i + swing_window) + 1):
            cj = rows[j]["close"]
            if not cj:
                continue
            cum = (cj / base - 1.0) * 100.0
            if abs(cum) >= swing_threshold:
                hit = (j, cum)
                break
        if hit:
            j, cum = hit
            if j - i >= 2:
                swings.append({
                    "start_date": rows[i]["date"], "end_date": rows[j]["date"],
                    "trading_days": j - i, "start_close": base, "end_close": rows[j]["close"],
                    "cum_pct": round(cum, 2), "direction": "up" if cum > 0 else "down",
                })
                i = j
            else:
                i += 1
        else:
            i += 1
    return swings


def main():
    ap = argparse.ArgumentParser(description="美股日线拉取 + 短期剧烈变动识别（Stooq→Yahoo）")
    ap.add_argument("ticker", help="美股代码，如 NVDA / AMD / TSM / ASML")
    ap.add_argument("--start", default=None, help="起始日 YYYYMMDD（缺省=end 前约 10 自然日，≈近 5~7 交易日）")
    ap.add_argument("--end", default=None, help="结束日 YYYYMMDD（缺省=本机今天；精确起见建议传 currentDate）")
    ap.add_argument("--day-threshold", type=float, default=5.0, help="单日异动阈值(%)，默认 5")
    ap.add_argument("--swing-threshold", type=float, default=10.0, help="短窗累计异动阈值(%)，默认 10")
    ap.add_argument("--swing-window", type=int, default=5, help="波段最大跨度(交易日)，默认 5（短期）")
    args = ap.parse_args()

    ticker = args.ticker.strip().upper()
    end = args.end or datetime.date.today().strftime("%Y%m%d")
    if args.start:
        start = args.start
    else:
        _e = datetime.datetime.strptime(end, "%Y%m%d").date()
        start = (_e - datetime.timedelta(days=10)).strftime("%Y%m%d")
    # 多取约 12 自然日缓冲，保证区间首日也能算出涨跌幅(需前一交易日收盘)
    buf = (datetime.datetime.strptime(start, "%Y%m%d").date() - datetime.timedelta(days=12)).strftime("%Y%m%d")

    warnings, source, full, name = [], None, [], ""

    # 1) Stooq 优先
    try:
        full, name, w = fetch_stooq(ticker, buf, end)
        warnings += w
        source = "stooq"
    except Exception as e:
        warnings.append(f"stooq 拉取失败({e!r})，回退 Yahoo。")

    # 2) 回退 Yahoo
    if not full:
        try:
            full, nm, w = fetch_yahoo(ticker, buf, end)
            warnings += w
            name = name or nm
            source = "yahoo"
        except Exception as e:
            print(json.dumps({
                "ok": False, "ticker": ticker,
                "error": f"两条数据通道(Stooq/Yahoo)都失败：{e!r}",
                "warnings": warnings,
            }, ensure_ascii=False, indent=2))
            sys.exit(1)

    rows = finalize(full, start, end)
    if not rows:
        print(json.dumps({
            "ok": False, "ticker": ticker,
            "error": f"{source} 取到数据但 [{start},{end}] 区间内无交易日（代码或区间有误？）",
            "warnings": warnings,
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    single = detect_single_day(rows, args.day_threshold)
    swings = detect_swings(rows, args.swing_threshold, args.swing_window)
    closes = [r["close"] for r in rows if r["close"]]
    period_return = round((closes[-1] / closes[0] - 1) * 100, 2) if len(closes) >= 2 else None
    adjust = "split-adj(stooz)" if source == "stooq" else "raw close(yahoo未复权)"

    out = {
        "ok": True,
        "data_source": source,
        "meta": {
            "ticker": ticker,
            "name": name,
            "market": "US",
            "adjust": adjust,
            "start": start,
            "end": end,
            "trading_days": len(rows),
            "first_date": rows[0]["date"],
            "last_date": rows[-1]["date"],
            "period_return_pct": period_return,
            "thresholds": {
                "day_pct": args.day_threshold,
                "swing_pct": args.swing_threshold,
                "swing_window_days": args.swing_window,
            },
        },
        "summary": {
            "n_single_day_events": len(single),
            "n_up_events": sum(1 for e in single if e["pct"] > 0),
            "n_down_events": sum(1 for e in single if e["pct"] < 0),
            "n_swings": len(swings),
        },
        "single_day_events": single,
        "multi_day_swings": swings,
        "warnings": warnings,
        "note": "single_day_events 与 multi_day_swings 是『需联网查原因』的异动清单；gap_pct=隔夜跳空(常为盘后财报/消息)、intraday_pct=日内。逐个解释催化、并区分个股/产业链/板块/大盘是本 skill 的核心。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
