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
import sys, json, time, argparse, urllib.request, urllib.error

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
