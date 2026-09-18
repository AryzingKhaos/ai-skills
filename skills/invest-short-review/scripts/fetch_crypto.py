#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币(币圈)个币行情拉取 + 短期剧烈变动(异动)识别。

确定性部分（短期向，加密高波动）：拉日线(UTC)行情，算出
  1) 单日异动：|当日涨跌幅| >= day_threshold（默认 8%）
  2) 短窗波段：连续若干天累计涨跌 >= swing_threshold（默认 20%，窗口默认 7 天）
并以 JSON 输出到 stdout，交给上层(LLM)逐个联网查原因、写复盘报告。
（加密 24/7 无交易日/无涨跌停概念；窗口按自然日；最后一根日线可能是 UTC 当日未走完的进行中 K 线。）

数据源：免费免 key 三级回退：Binance → OKX → Coinbase（均为公开行情接口、纯标准库）。
  报价货币：Binance/OKX 用 USDT 计价，Coinbase 用 USD 计价。

用法：
  python3 fetch_crypto.py BTC                         # 缺省=近 7 天
  python3 fetch_crypto.py ETH --start 20260601 --end 20260620
  python3 fetch_crypto.py SOL --start 20260601 --end 20260620 \
      --day-threshold 8 --swing-threshold 20 --swing-window 7

注意：精确起见，建议由调用方(LLM)按 system 的 currentDate 计算 start/end 传入；
      不传则用本机日期兜底（end=今天，start≈end 前 7 天）。
"""

import argparse
import datetime
import json
import sys
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
QUOTES = ["USDT", "USDC", "FDUSD", "BUSD", "USD"]


def _f(x):
    try:
        if x in (None, "", "null"):
            return None
        return round(float(x), 8)
    except (TypeError, ValueError):
        return None


def base_of(ticker):
    """从用户输入提取基础币种符号（去掉计价后缀/分隔符）。"""
    b = ticker.upper().replace("-", "").replace("/", "").strip()
    for q in QUOTES:
        if b.endswith(q) and len(b) > len(q):
            return b[:-len(q)]
    return b


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ms(d):
    return int(datetime.datetime.strptime(d, "%Y%m%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def fetch_binance(base, beg, end):
    """Binance 公开日线。USDT 计价。返回 (full_rows, quote, warnings)。"""
    sym = base + "USDT"
    start_ms = _ms(beg)
    end_ms = _ms(end) + 86400000  # 含 end 当天
    hosts = ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"]
    errs = []
    for h in hosts:
        url = f"{h}/api/v3/klines?symbol={sym}&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=1000"
        try:
            arr = _get_json(url)
            if isinstance(arr, dict) and arr.get("code"):
                raise RuntimeError(f"binance 业务错误 {arr}")
            if not arr:
                raise RuntimeError("binance 空数据")
            rows = []
            for k in arr:
                rows.append({
                    "date": datetime.datetime.utcfromtimestamp(k[0] / 1000).strftime("%Y-%m-%d"),
                    "open": _f(k[1]), "high": _f(k[2]), "low": _f(k[3]), "close": _f(k[4]),
                    "volume": _f(k[5]), "quote_volume": _f(k[7]),
                })
            return rows, "USDT", []
        except Exception as e:
            errs.append(f"{h.split('//')[1]}:{e!r}")
    raise RuntimeError("binance 全部 host 失败：" + "; ".join(errs[:3]))


def fetch_okx(base, beg, end):
    """OKX 公开日线(UTC)。USDT 计价。返回 (full_rows, quote, warnings)。"""
    inst = base + "-USDT"
    url = f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar=1Dutc&limit=300"
    payload = _get_json(url)
    if str(payload.get("code")) != "0" or not payload.get("data"):
        raise RuntimeError(f"okx 返回异常：code={payload.get('code')} msg={payload.get('msg')}")
    rows = []
    for c in payload["data"]:  # 新→旧: [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]
        rows.append({
            "date": datetime.datetime.utcfromtimestamp(int(c[0]) / 1000).strftime("%Y-%m-%d"),
            "open": _f(c[1]), "high": _f(c[2]), "low": _f(c[3]), "close": _f(c[4]),
            "volume": _f(c[5]), "quote_volume": _f(c[7]) if len(c) > 7 else None,
        })
    rows.reverse()
    return rows, "USDT", []


def fetch_coinbase(base, beg, end):
    """Coinbase Exchange 公开日线。USD 计价。返回 (full_rows, quote, warnings)。"""
    prod = base + "-USD"
    s = datetime.datetime.strptime(beg, "%Y%m%d").replace(tzinfo=datetime.timezone.utc).isoformat()
    e = (datetime.datetime.strptime(end, "%Y%m%d") + datetime.timedelta(days=1)).replace(tzinfo=datetime.timezone.utc).isoformat()
    url = f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity=86400&start={s}&end={e}"
    arr = _get_json(url)
    if not isinstance(arr, list) or not arr:
        raise RuntimeError("coinbase 空数据/异常")
    rows = []
    for c in arr:  # [time(s), low, high, open, close, volume]
        rows.append({
            "date": datetime.datetime.utcfromtimestamp(int(c[0])).strftime("%Y-%m-%d"),
            "open": _f(c[3]), "high": _f(c[2]), "low": _f(c[1]), "close": _f(c[4]),
            "volume": _f(c[5]), "quote_volume": None,
        })
    rows.sort(key=lambda r: r["date"])
    return rows, "USD", ["Coinbase 通道无成交额(quote volume)。"]


def finalize(full, start, end):
    """完整序列上算 prev_close/pct，再裁剪到 [start, end]。"""
    # 去重(同日多源/多根)取最后，按日期排序
    seen = {}
    for r in full:
        seen[r["date"]] = r
    rows_all = sorted(seen.values(), key=lambda r: r["date"])
    prev = None
    for r in rows_all:
        r["prev_close"] = prev["close"] if prev else None
        r["pct"] = round((r["close"] / prev["close"] - 1) * 100, 4) if (prev and prev["close"]) else None
        prev = r
    return [r for r in rows_all if start <= r["date"].replace("-", "") <= end]


def detect_single_day(rows, day_threshold):
    out = []
    for r in rows:
        if r["pct"] is None or abs(r["pct"]) < day_threshold:
            continue
        qv = r.get("quote_volume")
        out.append({
            "date": r["date"], "pct": r["pct"], "close": r["close"],
            "amplitude_pct": round((r["high"] - r["low"]) / r["prev_close"] * 100, 2) if (r.get("prev_close") and r.get("high") and r.get("low")) else None,
            "quote_volume_musd": round(qv / 1e6, 2) if qv else None,
        })
    return out


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
                    "days": j - i, "start_close": base, "end_close": rows[j]["close"],
                    "cum_pct": round(cum, 2), "direction": "up" if cum > 0 else "down",
                })
                i = j
            else:
                i += 1
        else:
            i += 1
    return swings


def main():
    ap = argparse.ArgumentParser(description="加密货币日线拉取 + 短期剧烈变动识别（Binance→OKX→Coinbase）")
    ap.add_argument("ticker", help="币种符号，如 BTC / ETH / SOL（也可写 BTCUSDT）")
    ap.add_argument("--start", default=None, help="起始日 YYYYMMDD（缺省=end 前约 7 天）")
    ap.add_argument("--end", default=None, help="结束日 YYYYMMDD（缺省=本机今天；精确起见建议传 currentDate）")
    ap.add_argument("--day-threshold", type=float, default=8.0, help="单日异动阈值(%)，默认 8")
    ap.add_argument("--swing-threshold", type=float, default=20.0, help="短窗累计异动阈值(%)，默认 20")
    ap.add_argument("--swing-window", type=int, default=7, help="波段最大跨度(天)，默认 7")
    args = ap.parse_args()

    base = base_of(args.ticker)
    end = args.end or datetime.date.today().strftime("%Y%m%d")
    if args.start:
        start = args.start
    else:
        _e = datetime.datetime.strptime(end, "%Y%m%d").date()
        start = (_e - datetime.timedelta(days=7)).strftime("%Y%m%d")
    buf = (datetime.datetime.strptime(start, "%Y%m%d").date() - datetime.timedelta(days=4)).strftime("%Y%m%d")

    warnings, source, full, quote = [], None, [], ""
    for fn, label in ((fetch_binance, "binance"), (fetch_okx, "okx"), (fetch_coinbase, "coinbase")):
        try:
            full, quote, w = fn(base, buf, end)
            warnings += w
            source = label
            break
        except Exception as e:
            warnings.append(f"{label} 失败({e!r})，回退下一通道。")

    if not full:
        print(json.dumps({"ok": False, "ticker": base,
                          "error": "三条数据通道(Binance/OKX/Coinbase)都失败（代码不存在或网络受限？）",
                          "warnings": warnings}, ensure_ascii=False, indent=2))
        sys.exit(1)

    rows = finalize(full, start, end)
    if not rows:
        print(json.dumps({"ok": False, "ticker": base,
                          "error": f"{source} 取到数据但 [{start},{end}] 区间内无 K 线",
                          "warnings": warnings}, ensure_ascii=False, indent=2))
        sys.exit(1)

    single = detect_single_day(rows, args.day_threshold)
    swings = detect_swings(rows, args.swing_threshold, args.swing_window)
    closes = [r["close"] for r in rows if r["close"]]
    period_return = round((closes[-1] / closes[0] - 1) * 100, 2) if len(closes) >= 2 else None

    out = {
        "ok": True,
        "data_source": source,
        "meta": {
            "symbol": base, "pair": f"{base}/{quote}", "market": "crypto",
            "quote": quote, "start": start, "end": end,
            "candles": len(rows), "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
            "last_close": rows[-1]["close"], "period_return_pct": period_return,
            "thresholds": {"day_pct": args.day_threshold, "swing_pct": args.swing_threshold, "swing_window_days": args.swing_window},
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
        "note": "加密 24/7、无涨跌停；最后一根日线可能为 UTC 当日进行中 K 线。single_day_events/multi_day_swings 是『需联网查原因』的异动清单；逐个解释催化、并区分 个币 / 板块叙事 / BTC·ETH(大盘) / 链上·衍生品 是本 skill 的核心。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
