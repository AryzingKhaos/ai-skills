#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dota2-match-review · STRATZ 增强取数脚本（可选，需 token）
用法:
    python3 fetch_stratz.py <match_id> [--account 137084212] [--append /path/_review_<id>.md] [--out /path/snippet.md]

做的事（全部确定性，只取 OpenDota 没有的"表现评价"增量，不与 fetch_match.py 重复）:
    1) 从 STRATZ GraphQL 拉该局：每人 IMP(影响力分)、奖章、识别出的位置/角色/分路、三路对线胜负
    2) 算出：你的 IMP 及队内/全场排名、你的对线胜负判定、本局奖章归属、比赛类型
    3) 打印一段"## 10. STRATZ 评价"数据卡片 + 一段 stratz 信号 JSON（供 SKILL.md 里 Claude 判断用）

判断（责任点/为什么没拿MVP/选人）仍交给 SKILL.md 的 Claude；本脚本只给 STRATZ 的数字。
核心价值：IMP 是 STRATZ 神经网络给"每局每人"的表现分，正好补上 OpenDota 完全没有的能力，
         并校准 fetch_match.py 里那个自研 MVP-proxy（有 IMP 时以 IMP/奖章为主，proxy 退为对照）。

token 获取：Steam 登录 stratz.com → https://stratz.com/api → My Tokens → 复制 JWT。
token 来源优先级：环境变量 STRATZ_TOKEN > --token-file > ~/.stratz_token
仅用标准库(urllib)，无需第三方依赖。需要联网。无 token / 该局未被 STRATZ 解析 / 网络失败 → 静默降级（不报错中断、不编数）。
"""
import os, sys, json, argparse, urllib.request, urllib.error

URL = "https://api.stratz.com/graphql"
DEFAULT_ACCOUNT = 137084212

QUERY = """
query ($id: Long!) {
  match(id: $id) {
    id didRadiantWin durationSeconds parsedDateTime
    analysisOutcome averageImp
    topLaneOutcome midLaneOutcome bottomLaneOutcome
    players {
      steamAccountId isRadiant isVictory heroId
      hero { displayName }
      kills deaths assists networth imp
      lane role roleBasic position award
    }
  }
}
"""

def load_token(token_file):
    t = os.environ.get("STRATZ_TOKEN")
    if t and t.strip():
        return t.strip()
    for path in [token_file, os.path.expanduser("~/.stratz_token")]:
        if path and os.path.exists(path):
            try:
                v = open(path, encoding="utf-8").read().strip()
                if v:
                    return v
            except Exception:
                pass
    return None

def gql(token, query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + token,
        "User-Agent": "STRATZ_API",          # 缺这个 header 会 403（常见坑）
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

# ---- 对线胜负解读 ----
def phys_lane(lane, is_radiant):
    """STRATZ 的 lane(SAFE/MID/OFF) + 阵营 → 物理三路(top/mid/bottom)。
    天辉: 安全路=下, 劣势路=上; 夜魇: 安全路=上, 劣势路=下; 中路=中。"""
    if lane == "MID_LANE": return "mid"
    if lane == "SAFE_LANE": return "bottom" if is_radiant else "top"
    if lane == "OFF_LANE":  return "top" if is_radiant else "bottom"
    return None  # ROAMING / JUNGLE / UNKNOWN

def read_outcome(enum, is_radiant):
    """LaneOutcomeEnums(TIE/RADIANT_VICTORY/RADIANT_STOMP/DIRE_VICTORY/DIRE_STOMP) → 从该阵营视角的中文判定。"""
    if not enum: return None
    if enum == "TIE": return "平"
    side = "RADIANT" if is_radiant else "DIRE"
    win = enum.startswith(side)
    stomp = enum.endswith("STOMP")
    if win:  return "碾压赢" if stomp else "赢"
    else:    return "被碾压" if stomp else "输"

def imp_tier(v):
    """IMP 粗读（非 STRATZ 官方分档，仅按符号/量级给个方向）。"""
    if v is None: return "无"
    if v >= 15:  return "很高(拉开胜负的关键正贡献)"
    if v >= 5:   return "偏正(有正贡献)"
    if v > -5:   return "接近均线"
    if v > -15:  return "偏负(略拖累团队赢面)"
    return "很低(明显拖累团队赢面)"

ANALYSIS_CN = {"NONE": "常规局", "STOMPED": "碾压局", "COMEBACK": "翻盘局", "CLOSE_GAME": "胶着局"}
LANE_CN = {"MID_LANE": "中路", "SAFE_LANE": "安全路", "OFF_LANE": "劣势路", "ROAMING": "游走", "JUNGLE": "打野", "UNKNOWN": "未知"}
ROLE_CN = {"CORE": "核心", "LIGHT_SUPPORT": "辅助(4号)", "HARD_SUPPORT": "辅助(5号)", "UNKNOWN": "未知"}

def hero_name(p):
    h = p.get("hero") or {}
    return h.get("displayName") or ("hero#" + str(p.get("heroId")))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match_id", type=int)
    ap.add_argument("--account", type=int, default=DEFAULT_ACCOUNT)
    ap.add_argument("--out", default=None, help="把片段单独写到该文件")
    ap.add_argument("--append", default=None, help="把片段追加到现有数据卡(_review_<id>.md)末尾")
    ap.add_argument("--token-file", default=None)
    a = ap.parse_args()

    out = []
    P = out.append
    P("## 10. STRATZ 评价（IMP / 对线判定 / 奖章 / 位置 — 来自 STRATZ 神经网络模型）")

    def emit(degraded_note=None, signals=None):
        if degraded_note:
            P(degraded_note)
        P("")
        P("### STRATZ 信号 JSON（供判断用，勿直接展示给用户）")
        P("```json")
        P(json.dumps(signals or {"source": "stratz", "parsed": False}, ensure_ascii=False, indent=2))
        P("```")
        text = "\n".join(out)
        print(text)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
        if a.append and os.path.exists(a.append):
            with open(a.append, "a", encoding="utf-8") as f:
                f.write("\n" + text + "\n")
            print("\n[已把 STRATZ 片段追加到 " + a.append + "]", file=sys.stderr)

    # --- token 降级 ---
    token = load_token(a.token_file)
    if not token:
        emit("> ⚠️ **未配置 STRATZ token**，本局无 IMP/对线判定/奖章。"
             "（如需：Steam 登录 stratz.com → https://stratz.com/api → My Tokens → 复制 JWT，"
             "存到 ~/.stratz_token 或设环境变量 STRATZ_TOKEN。）本局仍可用 OpenDota 数据卡正常复盘。",
             {"source": "stratz", "parsed": False, "reason": "no_token"})
        return

    # --- 请求 + HTTP 降级 ---
    try:
        resp = gql(token, QUERY, {"id": a.match_id})
    except urllib.error.HTTPError as e:
        code = e.code
        note = "> ⚠️ STRATZ 请求失败 HTTP " + str(code)
        if code in (401, 403):
            note += "（token 无效或已过期——JWT 约 1 年过期，去 https://stratz.com/api 重新生成）。"
        note += " 本局降级为仅 OpenDota 复盘。"
        emit(note, {"source": "stratz", "parsed": False, "reason": "http_" + str(code)})
        return
    except Exception as e:
        emit("> ⚠️ STRATZ 网络请求失败：" + str(e) + "。本局降级为仅 OpenDota 复盘。",
             {"source": "stratz", "parsed": False, "reason": "network"})
        return

    if resp.get("errors") and not resp.get("data", {}).get("match"):
        emit("> ⚠️ STRATZ 返回错误：" + json.dumps(resp["errors"], ensure_ascii=False)[:300] + "。降级为仅 OpenDota 复盘。",
             {"source": "stratz", "parsed": False, "reason": "graphql_error"})
        return

    m = (resp.get("data") or {}).get("match")
    if not m:
        emit("> ⚠️ STRATZ 查无此局（" + str(a.match_id) + "）。降级为仅 OpenDota 复盘。",
             {"source": "stratz", "parsed": False, "reason": "no_match"})
        return

    players = m.get("players") or []
    me = next((p for p in players if p.get("steamAccountId") == a.account), None)
    if me is None:
        emit("> ⚠️ 账号 " + str(a.account) + " 不在该 STRATZ 对局里。降级为仅 OpenDota 复盘。",
             {"source": "stratz", "parsed": False, "reason": "account_not_in_match"})
        return

    # --- 未解析降级：parsedDateTime 为空或 imp 缺失 ---
    if not m.get("parsedDateTime") or me.get("imp") is None:
        emit("> ⚠️ STRATZ 尚未解析该局（IMP/对线判定/奖章暂不可用，可能太新或非公开局）。其余维度照常用 OpenDota。",
             {"source": "stratz", "parsed": False, "reason": "stratz_unparsed"})
        return

    # ---------- 正常路径 ----------
    rad = me.get("isRadiant")
    my_imp = me.get("imp")
    team = [p for p in players if p.get("isRadiant") == rad]
    enemy = [p for p in players if p.get("isRadiant") != rad]
    by_imp_all = sorted(players, key=lambda p: -(p.get("imp") if p.get("imp") is not None else -999))
    by_imp_team = sorted(team, key=lambda p: -(p.get("imp") if p.get("imp") is not None else -999))
    rank_overall = by_imp_all.index(me) + 1
    rank_team = by_imp_team.index(me) + 1

    # 奖章
    def award_holder(aw):
        return next((hero_name(p) for p in players if p.get("award") == aw), None)
    mvp = award_holder("MVP"); top_core = award_holder("TOP_CORE"); top_sup = award_holder("TOP_SUPPORT")

    # 对线
    lane_enum_map = {"top": m.get("topLaneOutcome"), "mid": m.get("midLaneOutcome"), "bottom": m.get("bottomLaneOutcome")}
    my_phys = phys_lane(me.get("lane"), rad)
    my_lane_outcome = read_outcome(lane_enum_map.get(my_phys), rad) if my_phys else None
    lane_cn = {"top": "上路", "mid": "中路", "bottom": "下路"}
    three = {k: read_outcome(v, rad) for k, v in lane_enum_map.items()}

    # ---------- 输出 ----------
    aoc = ANALYSIS_CN.get(m.get("analysisOutcome"), m.get("analysisOutcome"))
    P("- **你的 IMP：%+d**（%s）— 队内第 **%d/%d**、全场第 **%d/10**" % (
        my_imp, imp_tier(my_imp), rank_team, len(team), rank_overall))
    P("  > IMP=STRATZ 神经网络给每局每人的表现分，按英雄/分路/位置/段位/时长归一；正=拉高了团队赢面，负=拖累。本局 averageImp=%s。" % m.get("averageImp"))
    P("- 队内 IMP（高→低）：" + " / ".join(
        "%s %+d%s" % (hero_name(p), p.get("imp"), "(你)" if p is me else "") for p in by_imp_team))
    P("- 敌方 IMP（高→低）：" + " / ".join(
        "%s %+d" % (hero_name(p), p.get("imp")) for p in sorted(enemy, key=lambda p: -(p.get("imp") or -999))))
    if my_phys:
        P("- **STRATZ 对线判定**：你在%s(%s/%s) → **%s**" % (
            lane_cn.get(my_phys, "?"), LANE_CN.get(me.get("lane"), me.get("lane")),
            ROLE_CN.get(me.get("role"), me.get("role")), my_lane_outcome or "无判定"))
    P("- 三路对线(你方视角)：" + " / ".join(
        "%s %s" % (lane_cn[k], three[k] or "无") for k in ["top", "mid", "bottom"]))
    P("- 奖章：MVP=%s / TOP_CORE=%s / TOP_SUPPORT=%s；**你的 award=%s**" % (
        mvp or "无", top_core or "无", top_sup or "无", me.get("award")))
    P("- STRATZ 识别你：位置 %s · 角色 %s · 分路 %s" % (
        me.get("position"), ROLE_CN.get(me.get("role"), me.get("role")), LANE_CN.get(me.get("lane"), me.get("lane"))))
    P("- 比赛类型(analysisOutcome)：%s" % aoc)

    signals = {
        "source": "stratz",
        "parsed": True,
        "my_imp": my_imp,
        "my_imp_tier": imp_tier(my_imp),
        "my_imp_rank_in_team": rank_team,
        "my_imp_rank_overall": rank_overall,
        "average_imp": m.get("averageImp"),
        "analysis_outcome": m.get("analysisOutcome"),
        "my_lane": me.get("lane"),
        "my_position": me.get("position"),
        "my_role": me.get("role"),
        "my_lane_outcome": my_lane_outcome,
        "lane_outcomes": three,                      # 你方视角 上/中/下
        "lane_outcomes_raw": lane_enum_map,          # 原始枚举(天辉视角)
        "award": {"mine": me.get("award"), "mvp": mvp, "top_core": top_core, "top_support": top_sup},
        "team_imp": [{"hero": hero_name(p), "imp": p.get("imp"), "me": p is me} for p in by_imp_team],
        "enemy_imp": [{"hero": hero_name(p), "imp": p.get("imp")} for p in sorted(enemy, key=lambda p: -(p.get("imp") or -999))],
    }
    emit(None, signals)

if __name__ == "__main__":
    main()
