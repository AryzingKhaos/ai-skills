#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dota2-match-review · 取数与确定性计算脚本
用法:
    python3 fetch_match.py <match_id> [--account 137084212] [--out /path/report_data.md]
做的事(全部确定性, 不做判断):
    1) 触发 OpenDota 解析(POST /request) 并轮询到 version!=null(录像未过期才行)
    2) 拉取整局 /matches/{id} 与 /heroes(英雄名映射)
    3) 计算并打印一张"数据卡"(markdown): 对线@5@10 / 补刀 / 击杀死亡时间线 /
       经济·经验优势曲线与转折点 / 出装顺序 / 百分位基准 / 全员表 / MVP-proxy 排名 /
       一段供 LLM 判断用的【信号 JSON】
判断(责任点/MVP原因/选人是否适配)交给 SKILL.md 里的 Claude 来写, 脚本只给数字。
仅用标准库(urllib), 无需第三方依赖。需要联网。
"""
import sys, json, time, math, argparse, urllib.request, urllib.error

API = "https://api.opendota.com/api"
DEFAULT_ACCOUNT = 137084212

def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "dota2-match-review/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def post(url, timeout=30):
    req = urllib.request.Request(url, method="POST", headers={"User-Agent": "dota2-match-review/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def mmss(t):
    t = int(t); s = "-" if t < 0 else ""; t = abs(t)
    return f"{s}{t//60}:{t%60:02d}"

def at(arr, i):
    if not arr: return None
    return arr[i] if len(arr) > i else arr[-1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match_id")
    ap.add_argument("--account", type=int, default=DEFAULT_ACCOUNT)
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-polls", type=int, default=20)
    ap.add_argument("--poll-interval", type=int, default=15)
    a = ap.parse_args()
    mid = a.match_id; acct = a.account
    out = []  # markdown lines
    P = out.append

    # 英雄映射
    heroes = get(f"{API}/heroes")
    LOC = {h["id"]: h["localized_name"] for h in heroes}
    KEY = {h["id"]: h["name"] for h in heroes}        # id -> npc_dota_hero_x
    ATK = {h["id"]: h.get("attack_type") for h in heroes}

    # 物品映射（用于"首个大件"诊断——看每个核心贪 farm 还是打架取向）
    try:
        items_const = get(f"{API}/constants/items")
    except Exception:
        items_const = {}
    IDNAME = {k: (v.get("dname") or k) for k, v in items_const.items() if isinstance(v, dict)}
    # 诊断用"首个大件"白名单：玩家常裸/首出的成品大件
    BIG_ITEMS = {
        "radiance","battle_fury","maelstrom","mjollnir","manta","sange_and_yasha","yasha_and_kaya",
        "kaya_and_sange","echo_sabre","harpoon","desolator","black_king_bar","blink","armlet",
        "mask_of_madness","vanguard","crimson_guard","hand_of_midas","dragon_lance","hurricane_pike",
        "diffusal_blade","orchid","bloodthorn","witch_blade","rod_of_atos","gungir","force_staff",
        "glimmer_cape","aether_lens","guardian_greaves","mekansm","pipe","solar_crest","vladmir",
        "eternal_shroud","kaya","phylactery","falcon_blade","helm_of_the_dominator","helm_of_the_overlord",
        "octarine_core","shivas_guard","assault","heart","satanic","abyssal_blade","skull_basher",
        "eye_of_skadi","butterfly","greater_crit","monkey_king_bar","silver_edge","ultimate_scepter",
        "aghanims_shard","cyclone","meteor_hammer","spirit_vessel","veil_of_discord","lotus_orb",
        "blade_mail","heavens_halberd","sheepstick","wraith_pact","khanda","disperser","parasma",
    }
    def first_big(p):
        for e in (p.get("purchase_log") or []):
            if e.get("key") in BIG_ITEMS:
                return IDNAME.get(e["key"], e["key"]), e["time"]
        return None, None

    # 触发解析 + 轮询
    parsed = False; m = None
    try:
        post(f"{API}/request/{mid}")
    except Exception as e:
        P(f"> ⚠️ 触发解析请求出错: {e}（继续尝试直接拉取）")
    for i in range(a.max_polls):
        try:
            m = get(f"{API}/matches/{mid}")
        except Exception as e:
            m = None
        if m and m.get("version") and (m.get("players") or [{}])[0].get("lh_t") is not None:
            parsed = True; break
        time.sleep(a.poll_interval)
    if m is None:
        print(f"错误: 拉取对局 {mid} 失败(网络或 match_id 无效)。", file=sys.stderr); sys.exit(2)

    players = m.get("players") or []
    me = next((p for p in players if p.get("account_id") == acct), None)
    if me is None:
        print(f"错误: 账号 {acct} 不在对局 {mid} 中(确认 match_id 是该账号的对局)。", file=sys.stderr); sys.exit(3)

    rad = me["player_slot"] < 128
    won = (rad == m.get("radiant_win"))
    dur = m.get("duration", 0)
    myhero = LOC.get(me["hero_id"], str(me["hero_id"]))
    mykey = KEY.get(me["hero_id"])

    P(f"# 数据卡 · match {mid} · {myhero}（{'天辉' if rad else '夜魇'}）· {'胜' if won else '负'} · {dur//60}分{dur%60}秒")
    P("")
    if not parsed:
        P("> ⚠️ **本局未能解析**（录像可能已过期——OpenDota 一般只保留约最近 1–2 周的录像可解析）。")
        P("> 以下仅含**未解析也能拿到的字段**（KDA/净值/GPM 等），**对线@10、逐分钟曲线、出装顺序、转折点、死亡时间线均不可用**。")
        P("")

    # ---------- 个人总览 ----------
    P("## 1. 个人总览")
    P(f"- 位置(lane_role)={me.get('lane_role')} 游走={me.get('is_roaming')} | K/D/A={me['kills']}/{me['deaths']}/{me['assists']} | GPM {me['gold_per_min']} / XPM {me['xp_per_min']}")
    P(f"- 正补 {me.get('last_hits')} / 反补 {me.get('denies')} | 净值 {me.get('net_worth')} | 英雄伤害 {me.get('hero_damage')} | 塔伤 {me.get('tower_damage')} | 治疗 {me.get('hero_healing')}")

    # 百分位
    b = me.get("benchmarks") or {}
    if b:
        P("")
        P("## 2. 百分位基准（同英雄全球，p 值越高越好）")
        for k in ["gold_per_min","xp_per_min","last_hits_per_min","hero_damage_per_min","hero_healing_per_min","tower_damage","kills_per_min","stuns_per_min"]:
            if k in b and b[k]:
                P(f"- {k:<22} 原始 {b[k].get('raw',0):.1f} → **p{round(b[k].get('pct',0)*100)}**")

    # ---------- 全员表 ----------
    def lane_name(n): return {1:"优势路",2:"中路",3:"劣势路",4:"打野"}.get(n, "?")
    P("")
    P("## 3. 双方全员")
    P("| 队 | 英雄 | 路 | K/D/A | 净值 | GPM | 英雄伤害 | |")
    P("|---|---|---|---|---|---|---|---|")
    for p in players:
        side = "天辉" if p["player_slot"] < 128 else "夜魇"
        me_tag = "**←你**" if p.get("account_id") == acct else ""
        P(f"| {side} | {LOC.get(p['hero_id'],'?')} | {lane_name(p.get('lane_role'))}{'游' if p.get('is_roaming') else ''} | {p['kills']}/{p['deaths']}/{p['assists']} | {p.get('net_worth')} | {p['gold_per_min']} | {p.get('hero_damage')} | {me_tag} |")

    # ---------- 3e. BP / 选人顺序（谁先手、谁被针对） ----------
    draft_sig = None
    pb = m.get("picks_bans")
    if pb:
        my_team = 0 if rad else 1
        picks = sorted([x for x in pb if x.get("is_pick")], key=lambda x: x.get("order", 0))
        bans = sorted([x for x in pb if not x.get("is_pick")], key=lambda x: x.get("order", 0))
        P("")
        P("## 3e. BP / 选人顺序（谁先手、谁被针对）")
        P("- Pick 顺序：" + " → ".join(
            ("你方·" if x["team"] == my_team else "敌方·") + LOC.get(x["hero_id"], str(x["hero_id"])) for x in picks))
        if bans:
            P("- Ban：" + ", ".join(LOC.get(x["hero_id"], "?") for x in bans))
        my_pick = next((i for i, x in enumerate(picks) if x["hero_id"] == me["hero_id"] and x["team"] == my_team), None)
        after_me_enemy = [LOC.get(x["hero_id"]) for x in picks[(my_pick + 1):] if x["team"] != my_team] if my_pick is not None else []
        if my_pick is not None:
            P(f"- 你的英雄 **{myhero}** 是全场第 **{my_pick + 1}** 手选出")
            if after_me_enemy:
                P(f"  - 在你之后 pick 的对手：{', '.join(after_me_enemy)} —— 可能是**冲着你选的**，结合第 11 节相性看是不是克你的英雄")
            else:
                P("  - 你是较晚手：能针对对面已亮的英雄反选（信息优势在你，若还没占到便宜要检讨选人）")
        draft_sig = {
            "pick_order": [{"side": "team" if x["team"] == my_team else "enemy",
                            "hero": LOC.get(x["hero_id"]), "order": x.get("order")} for x in picks],
            "bans": [LOC.get(x["hero_id"]) for x in bans],
            "my_pick_index": (my_pick + 1) if my_pick is not None else None,
            "enemy_picked_after_me": after_me_enemy,
        }

    # ---------- 3b 双方首个大件（看贪 farm / 打架取向；判断队友出装是否拖累你） ----------
    first_big_sig = {"team": [], "enemy": []}
    if parsed and any(p.get("purchase_log") for p in players):
        P("")
        P("## 3b. 双方首个大件（看贪 farm/打架取向；用于判断队友出装是否拖累了你的 farm/参团）")
        for label, key, is_team in (("你方", "team", True), ("敌方", "enemy", False)):
            parts = []
            for p in players:
                if ((p["player_slot"] < 128) == rad) != is_team: continue
                nm, t = first_big(p)
                tag = "(你)" if p.get("account_id") == acct else ""
                parts.append(f"{LOC.get(p['hero_id'],'?')}{tag} {nm}@{mmss(t)}" if nm else f"{LOC.get(p['hero_id'],'?')}{tag} 无大件")
                first_big_sig[key].append({"hero": LOC.get(p['hero_id']), "item": nm,
                                           "min": round((t or 0)/60, 1) if nm else None,
                                           "me": p.get("account_id") == acct})
            P(f"- {label}：" + " / ".join(parts))
        P("> 看点：你方核心若**裸贪 farm 大件（如双辉耀/漩涡/狂战）打进对面打架阵容**，会逼你这个中单放弃 farm 去单扛打架——此时你 farm 低/助攻低是**被迫的**，别算你头上。")

    signals = {"parsed": parsed, "won": won, "hero": myhero, "duration_min": round(dur/60,1),
               "kda": [me['kills'], me['deaths'], me['assists']],
               "benchmarks": {k: round(b[k]['pct']*100) for k in b if b.get(k)} if b else {},
               "first_big_items": first_big_sig}
    if draft_sig: signals["draft"] = draft_sig

    if parsed:
        # ---------- 对线 ----------
        my_lane = me.get("lane_role")
        opp_lane = {1:3, 2:2, 3:1}.get(my_lane)  # 镜像: 优势对敌劣势, 中对中, 劣势对敌优势
        cands = [p for p in players if (p["player_slot"]<128)!=rad and p.get("lane_role")==opp_lane and not p.get("is_roaming")]
        opp = max(cands, key=lambda p: p.get("net_worth",0)) if cands else None
        P("")
        P("## 4. 对线（@5 / @10）")
        def lrow(p, lab):
            P(f"- {lab} {LOC.get(p['hero_id'],'?'):<14} 正补 @5={at(p.get('lh_t'),5)} @10={at(p.get('lh_t'),10)} | 金钱 @10={at(p.get('gold_t'),10)} | 经验 @10={at(p.get('xp_t'),10)}")
        lrow(me, "你  ")
        lane_verdict = "无对位数据"
        if opp:
            lrow(opp, f"对位({lane_name(opp_lane)})")
            g10 = (at(me.get('gold_t'),10) or 0) - (at(opp.get('gold_t'),10) or 0)
            x10 = (at(me.get('xp_t'),10) or 0) - (at(opp.get('xp_t'),10) or 0)
            l10 = (at(me.get('lh_t'),10) or 0) - (at(opp.get('lh_t'),10) or 0)
            lane_verdict = "对线优势" if g10 > 400 else ("对线劣势" if g10 < -400 else "对线基本五五")
            P(f"- **对线差(@10)：金钱 {g10:+d} / 经验 {x10:+d} / 正补 {l10:+d} → {lane_verdict}**")
            signals["lane"] = {"vs": LOC.get(opp['hero_id']), "gold_diff_10": g10, "xp_diff_10": x10, "lh_diff_10": l10, "verdict": lane_verdict,
                               "my_lh10": at(me.get('lh_t'),10), "opp_lh10": at(opp.get('lh_t'),10)}

        # ---------- 击杀/死亡 ----------
        deaths = sorted(t for p in players for e in (p.get("kills_log") or []) if e.get("key")==mykey for t in [e["time"]])
        kills = sorted(e["time"] for e in (me.get("kills_log") or []))
        def phase(ts): return (sum(1 for t in ts if t<600), sum(1 for t in ts if 600<=t<1200), sum(1 for t in ts if t>=1200))
        dp = phase(deaths)
        P("")
        P("## 5. 击杀 / 死亡时间线")
        P(f"- 击杀({len(kills)})：" + ", ".join(mmss(t) for t in kills))
        P(f"- 死亡({len(deaths)})：" + ", ".join(mmss(t) for t in deaths))
        P(f"- 死亡分布：对线期(<10') **{dp[0]}** / 中期(10–20') **{dp[1]}** / 后期(>20') **{dp[2]}**")
        team_deaths_avg = round(sum(p['deaths'] for p in players if (p['player_slot']<128)==rad)/5, 1)
        P(f"- 你方场均死亡 {team_deaths_avg}，你死亡 {me['deaths']}（{'高于' if me['deaths']>team_deaths_avg else '不高于'}队均）")
        signals["deaths"] = {"total": me['deaths'], "phase_lane_mid_late": dp, "team_avg": team_deaths_avg, "lane_deaths": dp[0]}
        signals["kills_count"] = len(kills)

        # ---------- 经济/经验优势曲线 + 转折点 ----------
        def oriented(adv):
            if not adv: return []
            return [v if rad else -v for v in adv]
        ga = oriented(m.get("radiant_gold_adv")); xa = oriented(m.get("radiant_xp_adv"))
        P("")
        P("## 6. 团队优势曲线（正=你方领先）与转折点")
        marks = [t for t in [5,10,15,20,25,30,35,40,45] if t < len(ga)]
        P("| 分钟 | " + " | ".join(str(t) for t in marks) + " |")
        P("|---|" + "---|"*len(marks))
        P("| 金钱差 | " + " | ".join(f"{ga[t]:+d}" for t in marks) + " |")
        if xa: P("| 经验差 | " + " | ".join(f"{xa[t]:+d}" for t in marks) + " |")
        # 转折点: 金钱差从领先翻负的时刻 + 最大单段下滑(5分窗)
        cross = next((t for t in range(1,len(ga)) if ga[t-1]>=0 and ga[t]<0), None)
        worst_t, worst_d = None, 0
        for t in range(5, len(ga)):
            d = ga[t] - ga[t-5]
            if d < worst_d: worst_d, worst_t = d, t
        if cross is not None:
            P(f"- **领先转落后**发生在约 **{cross} 分**（金钱差跨过 0）")
        if worst_t is not None:
            P(f"- **最陡崩盘段**：{worst_t-5}–{worst_t} 分，5 分钟净亏 **{worst_d:+d}** 金钱差")
        signals["swing"] = {"lead_lost_min": cross, "worst_window": [worst_t-5 if worst_t else None, worst_t], "worst_5min_gold": worst_d,
                            "final_gold_adv": ga[-1] if ga else None}
        # 转折点附近的客观事件
        obj = m.get("objectives") or []
        if worst_t is not None:
            lo, hi = (worst_t-5)*60, worst_t*60+60
            near = [o for o in obj if lo-60 <= o.get("time",0) <= hi+60 and o.get("type") in
                    ("CHAT_MESSAGE_ROSHAN_KILL","building_kill","CHAT_MESSAGE_AEGIS")]
            if near:
                P("- 崩盘段附近事件：" + "; ".join(f"{mmss(o['time'])} {o.get('type','').replace('CHAT_MESSAGE_','').replace('building_kill','建筑')} {o.get('key','')}" for o in near[:8]))

        # ---------- 出装顺序 ----------
        SKIP = ("tango","branches","faerie_fire","ward_observer","ward_sentry","clarity","enchanted_mango","tpscroll","flask","circlet","recipe_")
        seq = [f"{mmss(e['time'])} {e['key']}" for e in (me.get("purchase_log") or []) if not any(s in e["key"] for s in SKIP)]
        P("")
        P("## 7. 出装顺序（去掉消耗/真假眼）")
        P("- " + " → ".join(seq))

        # ---------- MVP proxy ----------
        def z(vals):
            mu = sum(vals)/len(vals); sd = (sum((v-mu)**2 for v in vals)/len(vals))**0.5 or 1
            return [(v-mu)/sd for v in vals]
        nw = z([p.get("net_worth",0) for p in players])
        hd = z([p.get("hero_damage",0) for p in players])
        ka = z([(p["kills"]+p["assists"]) for p in players])
        dz = z([p["deaths"] for p in players])
        td = z([p.get("tower_damage",0) for p in players])
        scores = []; impact = {}
        for i,p in enumerate(players):
            pwin = (p["player_slot"]<128)==m.get("radiant_win")
            s = nw[i]*1.0 + hd[i]*1.0 + ka[i]*1.0 - dz[i]*0.7 + td[i]*0.4 + (0.8 if pwin else 0)
            impact[p["player_slot"]] = s
            scores.append((s,p))
        scores.sort(key=lambda x:-x[0])
        mvp = scores[0][1]
        my_rank = next(r for r,(s,p) in enumerate(scores,1) if p.get("account_id")==acct)
        P("")
        P("## 8. MVP-proxy（影响力综合分排名，透明算法，仅近似——Dota 无官方 MVP）")
        P("> 算法：队内 z 分加权 = 净值×1 + 英雄伤害×1 + (击杀+助攻)×1 − 死亡×0.7 + 塔伤×0.4 + 胜方+0.8")
        for r,(s,p) in enumerate(scores[:3],1):
            tag = "**←你**" if p.get("account_id")==acct else ""
            P(f"- 第{r}名 {LOC.get(p['hero_id'],'?')}（{'天辉' if p['player_slot']<128 else '夜魇'}）影响力分 {s:+.2f} {tag}")
        P(f"- **你排第 {my_rank}/10**")
        if mvp.get("account_id") != acct:
            gap = {"net_worth": mvp.get("net_worth",0)-me.get("net_worth",0),
                   "hero_damage": mvp.get("hero_damage",0)-me.get("hero_damage",0),
                   "deaths": me["deaths"]-mvp["deaths"],
                   "kills+assists": (mvp["kills"]+mvp["assists"])-(me["kills"]+me["assists"])}
            P(f"- 与本局 MVP（{LOC.get(mvp['hero_id'])}）的差距：净值 {gap['net_worth']:+d}、英雄伤害 {gap['hero_damage']:+d}、你多死 {gap['deaths']:+d}、击杀+助攻 {gap['kills+assists']:+d}")
            signals["mvp"] = {"mvp_hero": LOC.get(mvp['hero_id']), "mvp_on_winning_team": (mvp['player_slot']<128)==m.get('radiant_win'),
                              "my_rank": my_rank, "gap": gap}
        else:
            signals["mvp"] = {"my_rank": 1, "note": "你就是本局影响力最高(MVP-proxy)"}

        # ---------- 队友因素（数据化：你在队内位置 + 两队对比） ----------
        team = [p for p in players if (p['player_slot']<128)==rad]   # 含你
        enemy = [p for p in players if (p['player_slot']<128)!=rad]
        def kdar(p): return round((p["kills"]+p["assists"])/max(p["deaths"],1), 2)
        team_by_imp = sorted(team, key=lambda p:-impact[p['player_slot']])
        team_by_nw  = sorted(team, key=lambda p:-(p.get("net_worth") or 0))
        rank_imp = team_by_imp.index(me)+1
        rank_nw  = team_by_nw.index(me)+1
        # 崩盘玩家 = 击杀+助攻 < 死亡（KDA比<1）
        team_flops = [p for p in team if kdar(p)<1.0 and p.get("account_id")!=acct]
        enemy_flops = [p for p in enemy if kdar(p)<1.0]
        def topnw(side): return max(side, key=lambda p:p.get("net_worth") or 0)
        def sumnw(side): return sum((p.get("net_worth") or 0) for p in side)
        t_top, e_top = topnw(team), topnw(enemy)
        def brief(p):
            return {"hero":LOC.get(p['hero_id']), "kda":f"{p['kills']}/{p['deaths']}/{p['assists']}",
                    "nw":p.get("net_worth"), "impact":round(impact[p['player_slot']],2),
                    "me": p.get("account_id")==acct}
        P("")
        P("## 8b. 队友因素（数据化：你在队里的位置 + 两队对比）")
        def flopstr(ps):
            return ", ".join("{}({}/{}/{})".format(LOC.get(p['hero_id']), p['kills'], p['deaths'], p['assists']) for p in ps)
        tflop, eflop = flopstr(team_flops), flopstr(enemy_flops)
        P(f"- 你在本队：**影响力第 {rank_imp}/5**、**净值第 {rank_nw}/5**")
        P(f"- 崩盘玩家(击杀+助攻<死亡)：你方(不含你) **{len(team_flops)}** 人" + (f" — {tflop}" if team_flops else "")
          + f" ｜ 敌方 **{len(enemy_flops)}** 人" + (f" — {eflop}" if enemy_flops else ""))
        P(f"- 双方最肥核心：你方 {LOC.get(t_top['hero_id'])} {t_top.get('net_worth')} vs 敌方 {LOC.get(e_top['hero_id'])} {e_top.get('net_worth')}（差 {(t_top.get('net_worth') or 0)-(e_top.get('net_worth') or 0):+d}）")
        P(f"- 全队总净值：你方 {sumnw(team)} vs 敌方 {sumnw(enemy)}（差 {sumnw(team)-sumnw(enemy):+d}）")
        P("- 你方阵容(按影响力)：" + " / ".join(f"{LOC.get(p['hero_id'])} {p['kills']}/{p['deaths']}/{p['assists']}{'(你)' if p.get('account_id')==acct else ''}" for p in team_by_imp))
        P("- 敌方阵容(按影响力)：" + " / ".join(f"{LOC.get(p['hero_id'])} {p['kills']}/{p['deaths']}/{p['assists']}" for p in sorted(enemy, key=lambda p:-impact[p['player_slot']])))

        signals["context"] = {
            "team_gold_adv_final": ga[-1] if ga else None,
            "my_rank_in_team_by_impact": rank_imp,   # 1=本队最佳; 你越靠前=越不是你的锅
            "my_rank_in_team_by_networth": rank_nw,
            "team_flops_excl_me": [LOC.get(p['hero_id']) for p in team_flops],
            "enemy_flops": [LOC.get(p['hero_id']) for p in enemy_flops],
            "team_top_core": brief(t_top), "enemy_top_core": brief(e_top),
            "team_total_nw": sumnw(team), "enemy_total_nw": sumnw(enemy),
            "team_breakdown": [brief(p) for p in team_by_imp],
            "enemy_breakdown": [brief(p) for p in sorted(enemy, key=lambda p:-impact[p['player_slot']])],
        }

        # ---------- 3c. 首件/中件时间轴 + power spike 对局势的影响 ----------
        def first_bigs(p, n=2):
            res = []
            for e in (p.get("purchase_log") or []):
                if e.get("key") in BIG_ITEMS:
                    res.append((IDNAME.get(e["key"], e["key"]), e["key"], e["time"]))
                    if len(res) >= n: break
            return res
        def ga_at(minute):
            if not ga: return None
            i = int(minute)
            return ga[i] if i < len(ga) else ga[-1]
        # 判定"核心"：各队按净值前 3 名视作核心（拿大件更能左右局势的人）
        team_players = [p for p in players if (p["player_slot"] < 128) == rad]
        enemy_players = [p for p in players if (p["player_slot"] < 128) != rad]
        core_slots = set()
        for side in (team_players, enemy_players):
            for p in sorted(side, key=lambda x: -(x.get("net_worth") or 0))[:3]:
                core_slots.add(p["player_slot"])
        P("")
        P("## 3c. 首件/中件时间轴 + power spike 影响（越早成型越滚雪球）")
        P("| 队 | 英雄 | 首个大件 | 中件(第2大件) |")
        P("|---|---|---|---|")
        spikes = []  # 每个大件成型事件
        for p in players:
            side = "天辉" if p["player_slot"] < 128 else "夜魇"
            is_team = (p["player_slot"] < 128) == rad
            bigs = first_bigs(p, 2)
            c1 = f"{bigs[0][0]}@{mmss(bigs[0][2])}" if len(bigs) >= 1 else "无"
            c2 = f"{bigs[1][0]}@{mmss(bigs[1][2])}" if len(bigs) >= 2 else "—"
            hero_cell = LOC.get(p['hero_id'], '?') + ("（你）" if p.get("account_id") == acct else "")
            P(f"| {side} | {hero_cell} | {c1} | {c2} |")
            for idx, (nm, key, t) in enumerate(bigs):
                spikes.append({"t": t, "hero": LOC.get(p['hero_id']), "item": nm, "is_team": is_team,
                               "me": p.get("account_id") == acct, "is_core": p["player_slot"] in core_slots,
                               "which": "首件" if idx == 0 else "中件"})
        spikes.sort(key=lambda s: s["t"])
        # (a) 核心首件"时间竞赛"：谁先成型
        core_first_team = [s for s in spikes if s["is_core"] and s["is_team"] and s["which"] == "首件"]
        core_first_enemy = [s for s in spikes if s["is_core"] and not s["is_team"] and s["which"] == "首件"]
        if core_first_team and core_first_enemy:
            ft = min(core_first_team, key=lambda s: s["t"]); fe = min(core_first_enemy, key=lambda s: s["t"])
            P("")
            P(f"- **核心成型竞赛**：你方最快 {ft['hero']} {ft['item']}@{mmss(ft['t'])} vs 敌方最快 {fe['hero']} {fe['item']}@{mmss(fe['t'])}"
              + f"（{'你方' if ft['t'] < fe['t'] else '敌方'}早 {abs(ft['t']-fe['t'])//60}分{abs(ft['t']-fe['t'])%60}秒）")
        # (b) 大件后 5 分钟本方视角金钱差变化——仅核心、并明确标注"相关非因果"
        impact_rows = []
        for s in spikes:
            if not s["is_core"]: continue
            g0, g5 = ga_at(s["t"]/60.0), ga_at(s["t"]/60.0 + 5)
            if g0 is None or g5 is None: continue
            swing = (g5 - g0) if s["is_team"] else -(g5 - g0)  # 本方视角
            impact_rows.append({**s, "swing5": swing})
        notable = sorted([r for r in impact_rows if abs(r["swing5"]) >= 2000], key=lambda r: -abs(r["swing5"]))[:6]
        if notable:
            P("- **核心大件 ±5 分钟的团队金钱差变化（⚠️相关非因果：同期常有多件/团战叠加，仅供 Claude 结合团战/推塔判断）：**")
            for r in notable:
                who = "你方" if r["is_team"] else "敌方"
                P(f"  - {mmss(r['t'])} {who} {r['hero']} {r['which']} {r['item']} → 后5分钟本方金钱差 {r['swing5']:+d}")
        signals["item_impact"] = {
            "timeline": [{"min": round(s["t"]/60, 1), "hero": s["hero"], "item": s["item"], "which": s["which"],
                          "is_team": s["is_team"], "is_core": s["is_core"], "me": s["me"]} for s in spikes],
            "core_first_item_race": ({"team": {"hero": ft["hero"], "item": ft["item"], "min": round(ft["t"]/60,1)},
                                       "enemy": {"hero": fe["hero"], "item": fe["item"], "min": round(fe["t"]/60,1)},
                                       "team_earlier": ft["t"] < fe["t"]}
                                      if core_first_team and core_first_enemy else None),
            "notable_core_swings_correlational": [{"min": round(r["t"]/60, 1), "hero": r["hero"], "item": r["item"],
                                "is_team": r["is_team"], "gold_swing_next5": r["swing5"]} for r in notable],
        }

        # ---------- 5b. 团战参与度（从 OpenDota teamfights） ----------
        tfs = m.get("teamfights") or []
        my_idx = players.index(me)
        tf_summary = []
        my_part = 0; my_net = 0; my_tf_deaths = 0
        for f in tfs:
            fps = f.get("players") or []
            if len(fps) <= my_idx: continue
            mine = fps[my_idx]
            team_net = sum((fps[i].get("gold_delta") or 0) for i, p in enumerate(players) if (p["player_slot"] < 128) == rad and i < len(fps))
            enemy_net = sum((fps[i].get("gold_delta") or 0) for i, p in enumerate(players) if (p["player_slot"] < 128) != rad and i < len(fps))
            participated = (mine.get("damage") or 0) > 0 or (mine.get("deaths") or 0) > 0
            if participated: my_part += 1
            my_net += (mine.get("gold_delta") or 0)
            my_tf_deaths += (mine.get("deaths") or 0)
            tf_summary.append({"start": f.get("start"), "end": f.get("end"), "deaths": f.get("deaths"),
                               "team_gold": team_net, "enemy_gold": enemy_net,
                               "my_gold": mine.get("gold_delta"), "my_deaths": mine.get("deaths"),
                               "me_in": participated})
        if tfs:
            P("")
            P("## 5b. 团战参与度（OpenDota 团战聚类）")
            P(f"- 全局 **{len(tfs)}** 场团；你参与 **{my_part}** 场（参与率 {round(100*my_part/max(len(tfs),1))}%）")
            P(f"- 你团战净金钱 {my_net:+d}、团战内死亡 {my_tf_deaths}")
            wins = sum(1 for f in tf_summary if f["team_gold"] > f["enemy_gold"])
            P(f"- 团胜场（本方净金钱>敌方）：{wins}/{len(tfs)}")
            P("- 逐场(开始~结束｜本方净金/敌方净金｜你净金/你死)：" + "; ".join(
                f"{mmss(f['start'])}~{mmss(f['end'])} {f['team_gold']:+d}/{f['enemy_gold']:+d}｜{(f['my_gold'] or 0):+d}/{f['my_deaths']}" for f in tf_summary[:10]))
            signals["teamfights"] = {"total": len(tfs), "my_participation": my_part,
                                     "participation_rate": round(my_part/max(len(tfs),1), 2),
                                     "my_net_gold": my_net, "my_deaths_in_fights": my_tf_deaths,
                                     "team_won_fights": wins, "fights": tf_summary}

        # ---------- 6b. 四阶段（对线/游走gank/刷钱/后期）优劣势与变化原因 ----------
        durmin = dur // 60
        phase_defs = [(0, 10, "对线期"), (10, 20, "游走/gank 期"), (20, 30, "刷钱/推进期"), (30, 999, "后期")]
        # 建筑归属：goodguys=天辉建筑, badguys=夜魇建筑
        def building_side_lost(key):  # 返回该建筑属于哪方(radiant/dire)
            if "goodguys" in (key or ""): return True   # 天辉建筑被拆
            if "badguys" in (key or ""): return False   # 夜魇建筑被拆
            return None
        P("")
        P("## 6b. 四阶段优劣势与变化原因（对线 / 游走gank / 刷钱 / 后期）")
        phase_sig = []
        for a0, a1, name in phase_defs:
            if a0 >= durmin + 1: break
            b1 = min(a1, durmin)
            g0, g1 = ga_at(a0), ga_at(b1)
            x0, x1 = (xa[a0] if xa and a0 < len(xa) else None), (xa[b1] if xa and b1 < len(xa) else None)
            dg = (g1 - g0) if (g0 is not None and g1 is not None) else None
            dx = (x1 - x0) if (x0 is not None and x1 is not None) else None
            # 团战
            fwin = [f for f in tf_summary if a0*60 <= (f["start"] or 0) < b1*60+60]
            tf_net = sum((f["team_gold"] or 0) - (f["enemy_gold"] or 0) for f in fwin)
            # 建筑
            lo, hi = a0*60, b1*60+30
            tk_taken = tk_lost = 0; rosh = 0
            for o in obj:
                if not (lo <= o.get("time", 0) < hi): continue
                if o.get("type") == "building_kill":
                    s = building_side_lost(o.get("key"))
                    if s is None: continue
                    if s == rad: tk_lost += 1       # 我方建筑被拆
                    else: tk_taken += 1             # 拆掉敌方建筑
                elif o.get("type") == "CHAT_MESSAGE_ROSHAN_KILL":
                    rosh += 1
            # 个人
            kw = sum(1 for t in kills if a0*60 <= t < b1*60)
            dw = sum(1 for t in deaths if a0*60 <= t < b1*60)
            lh0, lh1 = at(me.get("lh_t"), a0), at(me.get("lh_t"), b1)
            lh_gain = (lh1 - lh0) if (lh0 is not None and lh1 is not None) else None
            verdict = "—"
            if dg is not None:
                verdict = "本方净赢" if dg > 800 else ("本方净亏" if dg < -800 else "基本拉锯")
            reasons = []
            if fwin: reasons.append(f"{len(fwin)}场团净{tf_net:+d}金")
            if tk_taken or tk_lost: reasons.append(f"拆敌塔{tk_taken}/丢己塔{tk_lost}")
            if rosh: reasons.append(f"肉山{rosh}次")
            reasons.append(f"你 {kw}杀{dw}死" + (f"·补{lh_gain}" if lh_gain is not None else ""))
            P(f"- **{name}（{a0}-{min(a1,durmin)}′）**：金钱差 {('%+d' % g0) if g0 is not None else '?'}→{('%+d' % g1) if g1 is not None else '?'}（Δ{('%+d' % dg) if dg is not None else '?'}）"
              + (f"、经验差Δ{dx:+d}" if dx is not None else "") + f" → **{verdict}**")
            P(f"  - 原因：" + "；".join(reasons))
            phase_sig.append({"phase": name, "from_min": a0, "to_min": min(a1, durmin),
                              "gold_adv_start": g0, "gold_adv_end": g1, "gold_delta": dg, "xp_delta": dx,
                              "verdict": verdict, "fights": len(fwin), "fights_net_gold": tf_net,
                              "towers_taken": tk_taken, "towers_lost": tk_lost, "roshan": rosh,
                              "my_kills": kw, "my_deaths": dw, "my_lasthits_gained": lh_gain})
            if a1 == 999: break
        signals["phases"] = phase_sig

        # ================= 深挖维度：视野 / 经济来源 / 死亡代价买活 / 伤害去向 =================
        def gr(p, k): return (p.get("gold_reasons") or {}).get(k, 0) or 0
        NPC2LOC = {KEY[hid]: LOC[hid] for hid in LOC if KEY.get(hid)}

        # ---------- 5c. 视野与做眼 ----------
        has_vision = any((p.get("obs_placed") is not None or p.get("obs_log")) for p in players)
        if has_vision:
            def obsn(p): return p.get("obs_placed") if p.get("obs_placed") is not None else len(p.get("obs_log") or [])
            def senn(p): return p.get("sen_placed") if p.get("sen_placed") is not None else len(p.get("sen_log") or [])
            t_obs = sum(obsn(p) for p in team); t_sen = sum(senn(p) for p in team)
            e_obs = sum(obsn(p) for p in enemy); e_sen = sum(senn(p) for p in enemy)
            my_obs, my_sen = obsn(me), senn(me)
            ward_rank = sorted(team, key=lambda p: -(obsn(p)+senn(p))).index(me) + 1
            P("")
            P("## 5c. 视野与做眼（假眼 obs / 真眼 sen）")
            P(f"- 你：假眼 **{my_obs}** / 真眼 **{my_sen}**（做眼总数队内第 {ward_rank}/5）")
            P(f"- 全队做眼：你方 假{t_obs}/真{t_sen} vs 敌方 假{e_obs}/真{e_sen}")
            P("- 各人做眼：" + " / ".join(f"{LOC.get(p['hero_id'])}{'(你)' if p.get('account_id')==acct else ''} 假{obsn(p)}真{senn(p)}" for p in team))
            P("> 看点：核心做眼少是常态；但你若**一个眼不插**、或全队视野被敌方碾压，团战/gank 常被单方面看死。辅助视野拉胯是团队问题。")
            signals["vision"] = {"my_obs": my_obs, "my_sen": my_sen, "my_ward_rank_in_team": ward_rank,
                                 "team_obs": t_obs, "team_sen": t_sen, "enemy_obs": e_obs, "enemy_sen": e_sen,
                                 "per_player": [{"hero": LOC.get(p['hero_id']), "obs": obsn(p), "sen": senn(p),
                                                 "me": p.get("account_id")==acct} for p in team]}

        # ---------- 3d. 经济来源 + farm 分布（打架钱 vs farm 钱） ----------
        if me.get("gold_reasons"):
            fight_g = gr(me, "12")                       # 击杀/助攻英雄的金钱
            creep_g = gr(me, "13")                       # 补小兵
            neut_g = gr(me, "14")                        # 打野
            struct_g = gr(me, "11")                      # 推塔分成
            rosh_g = gr(me, "15")                        # 肉山
            death_loss = gr(me, "1")                     # 死亡损失(负)
            farm_g = creep_g + neut_g
            base = fight_g + farm_g or 1
            P("")
            P("## 3d. 经济来源 + farm 分布（钱是打架赚的还是刷出来的）")
            P(f"- 打架收入(击杀/助攻) **{fight_g}** vs farm 收入(小兵+野) **{farm_g}** → 打架占比 **{round(100*fight_g/base)}%**")
            P(f"- 明细：补兵 {creep_g} / 打野 {neut_g} / 推塔分成 {struct_g} / 肉山 {rosh_g} / 死亡损失 {death_loss}")
            P(f"- farm 分布(击杀数)：线上小兵 **{me.get('lane_kills')}** / 野怪 **{me.get('neutral_kills')}** / 远古 **{me.get('ancient_kills')}**")
            P("> 看点：吃 farm 核打架占比过高=被迫打架没发育（结合 3b 队友出装看是不是被逼的）；辅助 farm 收入过高=在抢核心的钱。")
            signals["economy"] = {"fight_gold": fight_g, "farm_gold": farm_g,
                                  "fight_ratio": round(fight_g/base, 2),
                                  "creep_gold": creep_g, "neutral_gold": neut_g, "structure_gold": struct_g,
                                  "roshan_gold": rosh_g, "death_gold_loss": death_loss,
                                  "lane_kills": me.get("lane_kills"), "neutral_kills": me.get("neutral_kills"),
                                  "ancient_kills": me.get("ancient_kills")}

        # ---------- 5d. 死亡代价与买活 ----------
        dead_s = me.get("life_state_dead")
        # buyback_log 每项可能是 dict({"time":..}) 或直接数字——统一取秒数
        bb = [(e.get("time") if isinstance(e, dict) else e) for e in (me.get("buyback_log") or [])]
        bb = [t for t in bb if isinstance(t, (int, float))]
        if dead_s is not None or bb:
            team_dead_avg = None
            deads = [p.get("life_state_dead") for p in team if p.get("life_state_dead") is not None]
            if deads: team_dead_avg = round(sum(deads)/len(deads))
            P("")
            P("## 5d. 死亡代价与买活")
            if dead_s is not None:
                P(f"- 躺尸时间 **{mmss(dead_s)}**（占全场 {round(100*dead_s/max(dur,1))}%）" + (f"，队均 {mmss(team_dead_avg)}" if team_dead_avg else ""))
            P(f"- 死亡直接损失金钱 **{gr(me,'1')}**（掉的可靠金/买活成本外）")
            if bb:
                P(f"- 买活 **{len(bb)}** 次：" + ", ".join(mmss(t) for t in bb) + "（买活会掏空经济，净值低不等于没 farm）")
            else:
                P("- 本局未买活")
            P("> 看点：躺尸时间远超队均=你死得频繁或死在关键期，farm/参团双输；脆皮英雄躺尸占比高常是站位/进场问题。")
            signals["death_cost"] = {"seconds_dead": dead_s, "dead_pct": round(dead_s/max(dur,1), 2) if dead_s is not None else None,
                                     "team_avg_dead": team_dead_avg, "gold_lost_on_death": gr(me, "1"),
                                     "buyback_count": len(bb), "buyback_times": bb}

        # ---------- 5e. 伤害去向（你把输出砸在谁身上） ----------
        dmg_by_enemy = {}
        for ability, targets in (me.get("damage_targets") or {}).items():
            for tgt, dmg in (targets or {}).items():
                if tgt.startswith("npc_dota_hero_"):
                    dmg_by_enemy[tgt] = dmg_by_enemy.get(tgt, 0) + dmg
        if dmg_by_enemy:
            enemy_nw = {KEY.get(p['hero_id']): (p.get("net_worth") or 0) for p in enemy}
            fed = max(enemy, key=lambda p: p.get("net_worth") or 0)
            fed_npc = KEY.get(fed['hero_id'])
            total_dmg = sum(dmg_by_enemy.values()) or 1
            rows = sorted(dmg_by_enemy.items(), key=lambda kv: -kv[1])
            P("")
            P("## 5e. 伤害去向（你的英雄伤害砸在谁身上）")
            P("- " + " / ".join(f"{NPC2LOC.get(k, k)} {v}（{round(100*v/total_dmg)}%）" for k, v in rows))
            fed_share = round(100 * dmg_by_enemy.get(fed_npc, 0) / total_dmg)
            P(f"- 对面最肥核心 **{LOC.get(fed['hero_id'])}**（净值 {fed.get('net_worth')}）吃到你 **{fed_share}%** 的伤害")
            P("> 看点：团战该集火对面大核/关键输出；若你大量伤害砸在肉盾/辅助身上、最肥核心吃得很少，说明目标选择有问题。")
            signals["damage_focus"] = {"by_enemy": {NPC2LOC.get(k, k): v for k, v in rows},
                                       "fed_core": LOC.get(fed['hero_id']), "fed_core_dmg_share": fed_share}

        # ---------- 6c. 胜率曲线与 throw 度（启发式模型，非官方） ----------
        def winprob(minute):
            g = ga_at(minute)
            if g is None: return None
            x = (xa[minute] if xa and 0 <= minute < len(xa) else 0) or 0
            scale = max(3000.0, 11000.0 - 170.0 * minute)   # 越晚，同样领先越致命
            z = (g + 0.5 * x) / scale
            return 1.0 / (1.0 + math.exp(-max(-40, min(40, z))))
        wp = [w for w in (winprob(t) for t in range(len(ga))) if w is not None]
        if wp:
            peak = max(wp); trough = min(wp); final_wp = wp[-1]
            peak_min = wp.index(peak); trough_min = wp.index(trough)
            if not won and peak >= 0.70:
                throw_note = f"⚠️ **优势局崩盘(throw)**：你方在 {peak_min}′ 胜率一度达 **{round(peak*100)}%**，却最终输 —— 优势没收住"
                throw_kind = "throw"
            elif won and trough <= 0.30:
                throw_note = f"🔥 **劣势翻盘(comeback)**：你方在 {trough_min}′ 胜率一度低到 **{round(trough*100)}%**，最终赢回来"
                throw_kind = "comeback"
            else:
                throw_note = "胜负与优势曲线基本一致，无明显 throw/翻盘"
                throw_kind = "normal"
            P("")
            P("## 6c. 胜率曲线与 throw 度（启发式模型，仅供参考）")
            P(f"- 峰值胜率 **{round(peak*100)}%**@{peak_min}′ / 谷值 **{round(trough*100)}%**@{trough_min}′ / 终局 {round(final_wp*100)}%")
            P(f"- {throw_note}")
            P("> 模型：按金钱+经验领先随时间加权的 logistic 估的赢面，**非官方**，只用来量化'优势局有没有崩/劣势有没有翻'。")
            signals["throw"] = {"peak_winprob": round(peak, 2), "peak_min": peak_min,
                                "trough_winprob": round(trough, 2), "trough_min": trough_min,
                                "final_winprob": round(final_wp, 2), "kind": throw_kind}

            # ---------- 6d. 关键决策点（自动标注） ----------
            events = []
            for o in obj:
                t = o.get("time", 0); typ = o.get("type")
                if typ == "CHAT_MESSAGE_ROSHAN_KILL":
                    events.append((t, "肉山被击杀"))
                elif typ == "CHAT_MESSAGE_AEGIS":
                    events.append((t, "有人吃到不朽盾(Aegis)"))
                elif typ == "building_kill" and "rax" in (o.get("key") or ""):
                    s = building_side_lost(o.get("key"))
                    events.append((t, "你方兵营被破" if s == rad else ("破敌方兵营" if s is False or s is True else "兵营被破")))
            for f in tf_summary:
                net = (f["team_gold"] or 0) - (f["enemy_gold"] or 0)
                if abs(net) >= 3000:
                    events.append((f["start"], f"大团战（{'你方净赢' if net > 0 else '你方净亏'} {net:+d}金）"))
            events.sort(key=lambda e: e[0])
            if events:
                P("")
                P("## 6d. 关键决策点（自动标注：肉山/盾 · 兵营 · 大团战 → 之后 3 分钟金钱差变化）")
                dp_sig = []
                for t, label in events:
                    g0, g3 = ga_at(t/60.0), ga_at(t/60.0 + 3)
                    swing = (g3 - g0) if (g0 is not None and g3 is not None) else None
                    wph = winprob(int(t/60))
                    P(f"- {mmss(t)} **{label}**"
                      + (f" → 后3分钟金钱差 {swing:+d}" if swing is not None else "")
                      + (f"（当时赢面 {round(wph*100)}%）" if wph is not None else ""))
                    dp_sig.append({"time": mmss(t), "event": label,
                                   "gold_swing_next3": swing, "winprob_at": round(wph, 2) if wph is not None else None})
                signals["decision_points"] = dp_sig

    # ---------- 信号 JSON ----------
    P("")
    P("## 9. 信号 JSON（供判断责任点/MVP原因/选人适配，勿直接展示给用户）")
    P("```json")
    P(json.dumps(signals, ensure_ascii=False, indent=2))
    P("```")

    text = "\n".join(out)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n[已保存数据卡到 {a.out}]", file=sys.stderr)

if __name__ == "__main__":
    main()
