# -*- coding: utf-8 -*-
"""
复盘工作台 · 数据聚合 + 页面生成
读 data/raw/*.json（同花顺涨停池/跌停池原始数据）→ 生成单文件 index.html
"""
import json
import os
import re
import sys
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from categories import classify

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "index.html")


def limit_pct(code, name):
    """涨跌停幅度：主板 10%，创业/科创 20%，ST 5%"""
    if "ST" in (name or "").upper():
        return 0.05
    c = str(code or "")
    if c.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10


def limit_price(prev_close, pct):
    """A股涨跌停价：四舍五入到分"""
    return round(prev_close * (1 + pct) + 1e-9, 2)


def build_market_limit_index():
    """
    用全市场日K（2026-07-20 起）补算 8/3 之前的涨停日，
    让 8 月初的连板回溯不断链。
    返回 ({code: set(dates)}, 交易日历 list)
    """
    idx = {}
    cal = set()
    has_trade = {}
    path = os.environ.get("MARKET_KLINE", "")
    if not path or not os.path.exists(path):
        return idx, sorted(cal), has_trade
    try:
        with open(path, encoding="utf-8") as f:
            mk = json.load(f)
    except Exception as e:
        print("market_kline skip:", e, file=sys.stderr)
        return idx, sorted(cal), has_trade

    for code, obj in mk.items():
        kl = obj.get("klines") or []
        name = obj.get("name") or ""
        pct = limit_pct(code, name)
        prev = None
        for line in kl:
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                dt = parts[0].replace("-", "")
                close = float(parts[2])
            except Exception:
                continue
            cal.add(dt)
            has_trade.setdefault(code, set()).add(dt)
            if prev is not None and prev > 0:
                lp = limit_price(prev, pct)
                if close >= lp - 0.005:
                    idx.setdefault(code, set()).add(dt)
            prev = close
    return idx, sorted(cal), has_trade


def apply_boards(rows):
    """用涨停池序列 + 全市场日K 回溯真实连板数"""
    ext, cal, has_trade = build_market_limit_index()
    pool_dates = {c: set(ds) for c, ds in ext.items()}
    for r in rows:
        for s in r["ztl"]:
            pool_dates.setdefault(s["c"], set()).add(r["d"])

    all_dates = sorted(set(cal) | {r["d"] for r in rows})
    pos = {d: i for i, d in enumerate(all_dates)}

    for r in rows:
        i = pos.get(r["d"])
        for s in r["ztl"]:
            ds = pool_dates.get(s["c"], set())
            k = 1
            j = i - 1
            while j >= 0 and all_dates[j] in ds:
                k += 1
                j -= 1
            s["b"] = k
        r["mb"] = max([x["b"] for x in r["ztl"]], default=0)
    return has_trade


def fmt_time(ts):
    """时间戳字符串 -> HH:MM:SS"""
    if ts in (None, "", 0, "0"):
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%H:%M:%S")
    except Exception:
        return "-"


def split_reason(reason):
    """'“羊”字辈+陶瓷纤维+锂电隔热' -> ['“羊”字辈','陶瓷纤维','锂电隔热']"""
    if not reason:
        return []
    parts = [p.strip().strip('“”"') for p in str(reason).split("+")]
    return [p for p in parts if p]


def load_days():
    rows = []
    for fn in sorted(os.listdir(RAW)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(RAW, fn), encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            print("skip", fn, e, file=sys.stderr)
            continue
        zt_list = d.get("zhangting") or []
        dt_list = d.get("dieting") or []
        if not zt_list and not dt_list:
            continue

        zt = d.get("zt_total") or len(zt_list)
        dt = d.get("dt_total") or len(dt_list)

        ztl = []
        for s in zt_list:
            ztl.append({
                "c": s.get("code"),
                "n": s.get("name"),
                "p": s.get("latest"),
                "g": round(float(s.get("change_rate") or 0), 2),
                "b": 1,
                "h": s.get("high_days") or "",
                "t": s.get("limit_up_type") or "",
                "o": s.get("open_num") or 0,
                "f": fmt_time(s.get("first_time")),
                "l": fmt_time(s.get("last_time")),
                "r": s.get("reason") or "",
                "a": s.get("order_amount") or 0,
                "cv": s.get("currency_value") or 0,
                "vr": round(float(s.get("turnover_rate") or 0), 1),
            })
        ztl.sort(key=lambda x: (x["f"] if x["f"] != "-" else "99:99"))

        dtl = []
        for s in dt_list:
            dtl.append({
                "c": s.get("code"),
                "n": s.get("name"),
                "p": s.get("latest"),
                "g": round(float(s.get("change_rate") or 0), 2),
                "r": s.get("reason") or "",
                "f": fmt_time(s.get("first_time")),
            })

        max_board = 0
        open_cnt = sum(1 for x in ztl if x["o"] and int(x["o"]) > 0)

        rows.append({
            "d": d.get("date"),
            "zt": zt,
            "dt": dt,
            "mb": max_board,
            "oc": open_cnt,
            "ztl": ztl,
            "dtl": dtl,
        })
    rows.sort(key=lambda x: x["d"])
    return rows


def compute_zhaban(dates):
    """每日炸板家数（盘中触及涨停但收盘没封住），结果缓存到 data/zhaban.json"""
    cache = os.path.join(BASE, "data", "zhaban.json")
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                c = json.load(f)
            if all(x in c for x in dates):
                return c
        except Exception:
            pass

    mk_path = os.environ.get("MARKET_KLINE") or os.path.join(BASE, "data", "market_kline.json")
    if not os.path.exists(mk_path):
        return {x: 0 for x in dates}
    try:
        with open(mk_path, encoding="utf-8") as f:
            mk = json.load(f)
    except Exception as e:
        print("zhaban skip:", e, file=sys.stderr)
        return {x: 0 for x in dates}

    out = {x: 0 for x in dates}
    for code, obj in mk.items():
        kl = []
        for line in obj.get("klines") or []:
            p = line.split(",")
            if len(p) < 5:
                continue
            try:
                kl.append((p[0].replace("-", ""), float(p[2]), float(p[3])))
            except ValueError:
                continue
        if len(kl) < 2:
            continue
        pct = limit_pct(code, obj.get("name"))
        for i in range(1, len(kl)):
            d, c, h = kl[i]
            if d not in out:
                continue
            prev = kl[i - 1][1]
            if prev <= 0:
                continue
            L = limit_price(prev, pct)
            if h >= L - 0.005 and c < L - 0.005:
                out[d] += 1
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception:
        pass
    return out


def compute_daily_indicators(rows):
    """
    每日情绪指标 + 两个标签：
      warn: 五条恶化里中了几条（跌停↑ 涨停↓ 炸板↑ 晋级↓ 最高板断板）
      ice : 绝对冰点（涨停<=40 且 跌停>=30 且 晋级率<=15 且 最高板<=5）
    """
    dates = [r["d"] for r in rows]
    zb_map = compute_zhaban(dates)

    for i, r in enumerate(rows):
        prev = rows[i - 1] if i > 0 else None
        cur = {s["c"] for s in r["ztl"]}
        prev_set = {s["c"] for s in prev["ztl"]} if prev else set()
        r["promo"] = round(len(prev_set & cur) / len(prev_set) * 100, 1) if prev_set else None

        zb = int(zb_map.get(r["d"], 0))
        r["zb"] = zb
        r["zbr"] = round(zb / (r["zt"] + zb) * 100, 1) if (r["zt"] + zb) else 0.0

        if prev is None:
            r["warn"] = 0
            r["warnFlags"] = [False] * 5
        else:
            f1 = r["dt"] > prev["dt"]
            f2 = r["zt"] < prev["zt"]
            f3 = r["zb"] > prev["zb"]
            f4 = (r["promo"] is not None and prev["promo"] is not None and r["promo"] < prev["promo"])
            f5 = bool(r.get("broke"))
            flags = [f1, f2, f3, f4, f5]
            r["warnFlags"] = flags
            r["warn"] = sum(1 for x in flags if x)

        r["ice"] = bool(
            r["zt"] <= 40 and r["dt"] >= 30
            and (r["promo"] is not None and r["promo"] <= 15)
            and r["mb"] <= 5
        )
    return rows


def build_node_days(rows, has_trade):
    """
    节点日：5 板及以上（含 5 板）的最高板，在之后的第一个交易日断板，
    断板当天记为节点日。
    停牌日不算断板（用全市场日K判断当天是否有成交）。
    """
    pool = {r["d"]: {s["c"] for s in r["ztl"]} for r in rows}
    node = {}  # {date: {code: {n,b,c}}}  同一只票只记一次，保留断板前最高板数
    for i, r in enumerate(rows):
        if (r.get("mb") or 0) < 5:
            continue
        tops = [s for s in r["ztl"] if s["b"] == r["mb"] and s["b"] >= 5]
        for s in tops:
            c = s["c"]
            for j in range(i + 1, len(rows)):
                d2 = rows[j]["d"]
                # 停牌日跳过，不算断板
                if has_trade and c in has_trade and d2 not in has_trade[c]:
                    continue
                if c not in pool[d2]:
                    slot = node.setdefault(d2, {})
                    prev = slot.get(c)
                    if prev is None or s["b"] > prev["b"]:
                        slot[c] = {"n": s["n"], "b": s["b"], "c": c}
                    break
    for r in rows:
        r["node"] = list((node.get(r["d"]) or {}).values())

    # 任意高度的最高板断板（情绪五条用，不限 5 板以上）
    for i in range(len(rows) - 1):
        d1 = rows[i + 1]["d"]
        tops = [s["c"] for s in rows[i]["ztl"] if s["b"] == rows[i]["mb"]]
        rows[i + 1]["broke"] = bool(tops) and all(c not in pool[d1] for c in tops)
    if rows:
        rows[0]["broke"] = False
    return rows


def market_state(rows):
    """按顺序给每天打一个情绪标签（参考用，规则写在页面上）"""
    for i, r in enumerate(rows):
        zt, dt = r["zt"], r["dt"]
        prev = rows[i - 1] if i > 0 else None
        label = "平淡"
        if zt >= 100:
            label = "高潮"
        if dt >= 30 and zt <= 45:
            label = "冰点"
        if prev:
            if prev["zt"] <= 45 and prev["dt"] >= 30 and zt >= max(55, prev["zt"] * 1.3):
                label = "冰点修复"
            elif prev["zt"] >= 90 and zt < prev["zt"] * 0.75 and dt >= prev["dt"]:
                label = "退潮"
        r["st"] = label
    return rows


def theme_stats(day):
    """当天题材分类：一票一类，与同花顺面板同口径（加起来=涨停总数）"""
    from collections import Counter
    c = Counter()
    for s in day["ztl"]:
        cat, tag = classify(s["r"])
        s["cat"] = cat
        c[cat] += 1
    return [{"k": k, "v": v} for k, v in c.most_common(30)]


HTML_TMPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>复盘工作台</title>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2330; --line:#2a3441;
  --txt:#d8e0ea; --dim:#8b98a8; --red:#f0524f; --green:#2ecc8f;
  --gold:#e8b44a; --blue:#4a9eff;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
  font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;font-size:14px;
  -webkit-font-smoothing:antialiased}
header{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:16px;
  padding:12px 20px;background:linear-gradient(180deg,#141a22,#0f141b);
  border-bottom:1px solid var(--line)}
header h1{margin:0;font-size:17px;font-weight:600;letter-spacing:.5px}
header h1 .fish{color:var(--gold)}
.spacer{flex:1}
select{background:var(--panel2);color:var(--txt);border:1px solid var(--line);
  border-radius:6px;padding:6px 10px;font-size:14px;font-family:inherit;outline:none}
select:focus{border-color:var(--blue)}
.stamp{color:var(--dim);font-size:12px}
.navbtn{background:#1c2330;border:1px solid #2a3441;color:#a9b6c6;border-radius:6px;
  width:30px;height:30px;cursor:pointer;font-size:16px;font-family:inherit;line-height:1;
  padding:0 0 3px;transition:.12s}
.navbtn:hover:not(:disabled){border-color:#4a9eff;color:#fff}
.navbtn:disabled{opacity:.28;cursor:not-allowed}
main{padding:16px 20px 60px;max-width:1560px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
main > .mod{min-width:0;position:relative}
main > .mod.hid{display:none!important}
body.editing main > .mod{outline:1px dashed #33404f;outline-offset:3px;border-radius:12px}
.modctl{display:none;position:absolute;top:-12px;right:10px;z-index:30;gap:4px;background:#0d1117;padding:2px 4px;border-radius:6px}
body.editing .modctl{display:flex}
.modctl button{background:#1c2330;border:1px solid #2a3441;color:#a9b6c6;border-radius:5px;
  font-size:11px;padding:2px 7px;cursor:pointer;font-family:inherit;line-height:1.6}
.modctl button:hover{border-color:#4a9eff;color:#fff}
.modanch{position:relative}
.toolbar{display:flex;gap:8px;align-items:center}
.tbtn{background:#1c2330;border:1px solid #2a3441;color:#a9b6c6;border-radius:6px;
  padding:6px 11px;font-size:12.5px;cursor:pointer;font-family:inherit}
.tbtn:hover{border-color:#4a9eff;color:#fff}
.tbtn.on{background:#1d3a5c;border-color:#4a9eff;color:#cfe6ff}
.hiddenTray{display:none;flex-wrap:wrap;gap:8px;padding:10px 14px;background:#161b22;
  border:1px dashed #2a3441;border-radius:10px;align-items:center;font-size:12px;color:#8b98a8}
body.editing .hiddenTray{display:flex}
th.sortable{cursor:pointer;user-select:none}
th.sortable:hover{color:#cfe6ff}
th .arr{color:#4a9eff;font-size:10px;margin-left:3px}
.theme.drag{cursor:grab}
.theme.dragging{opacity:.4}
.theme.dropbefore{box-shadow:0 -2px 0 #4a9eff}
.theme.dropafter{box-shadow:0 2px 0 #4a9eff}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{color:var(--dim);font-size:12px;margin-bottom:6px}
.card .v{font-size:26px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums}
.card .s{color:var(--dim);font-size:11px;margin-top:4px}
.row{display:grid;grid-template-columns:1.35fr 1fr;gap:14px;margin-bottom:14px}
@media(max-width:1100px){.row{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.panel h2{margin:0 0 12px;font-size:14px;font-weight:600;color:var(--txt);
  display:flex;align-items:center;gap:8px}
.panel h2 .tag{font-size:11px;color:var(--dim);font-weight:400}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}
.b-冰点{background:#1b3a5c;color:#7fc4ff}
.b-冰点修复{background:#1d4a3a;color:#5fe0a8}
.b-高潮{background:#4a1d1d;color:#ff8a87}
.b-退潮{background:#3a2a12;color:#e8b44a}
.b-平淡{background:#242c38;color:#a9b6c6}
.themes{display:flex;flex-direction:column;gap:7px}
.theme{display:flex;align-items:center;gap:10px;cursor:pointer;padding:2px 0}
.theme .name{width:118px;flex:none;color:var(--txt);font-size:13px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.theme .bar{flex:1;height:16px;background:var(--panel2);border-radius:4px;overflow:hidden}
.theme .bar i{display:block;height:100%;background:linear-gradient(90deg,#8a2f2d,#f0524f);border-radius:4px}
.theme .cnt{width:28px;text-align:right;font-variant-numeric:tabular-nums;color:var(--red);font-weight:600}
.theme.on .name{color:var(--gold);font-weight:600}
.ladder{display:flex;flex-direction:column}
.lgrid{display:grid;grid-template-columns:38px repeat(5,minmax(0,1fr));gap:1px;
  background:#334356;border:1px solid #4a5f78;border-radius:6px;overflow:hidden;
  box-shadow:0 0 0 1px rgba(74,95,120,.25)}
.lcell{background:#161b22;padding:3px 5px;display:flex;flex-wrap:wrap;gap:3px;
  align-content:flex-start;min-height:24px;min-width:0}
.lhead{background:#1d2531;color:#c3cfdd;font-size:11px;font-weight:600;
  justify-content:center;align-items:center}
.lv{background:#1b2330;color:#f0c35a;font-weight:700;font-size:11px;
  justify-content:center;align-items:center}
.ldate{cursor:pointer;font-variant-numeric:tabular-nums}
.ldate:hover{color:#4a9eff}
.lcell .chip{font-size:10.5px;padding:0 4px;line-height:17px;gap:3px}
.lcell .cat-tag{font-size:9px;line-height:13px;padding:0 3px}
.chip{display:inline-flex;align-items:center;gap:5px;background:#1c2330;border:1px solid #2a3441;color:inherit;text-decoration:none;
  border-radius:5px;padding:1px 6px;font-size:11.5px;line-height:19px;cursor:pointer;transition:.12s;
  max-width:100%}
.chip:hover{border-color:var(--blue)}
.ladder.hovering .chip{opacity:.22;transition:opacity .15s ease}
.ladder.hovering .chip.lit{opacity:1;border-color:#4a9eff;
  box-shadow:0 0 0 1px rgba(74,158,255,.55),0 0 12px rgba(74,158,255,.2)}
.firstToggle{margin-left:auto;font-size:12px;color:#8b98a8;cursor:pointer;
  display:flex;align-items:center;gap:4px;font-weight:400}
.firstToggle input{cursor:pointer;accent-color:#4a9eff}
.firstHint{color:#6b7889;font-size:11px;cursor:pointer;padding:1px 5px;border-radius:4px;
  border:1px dashed #2a3441;line-height:17px}
.firstHint:hover{color:#4a9eff;border-color:#4a9eff}
.cat-tag{display:inline-block;font-size:9.5px;font-weight:500;padding:0 4px;
  border:1px solid transparent;border-radius:3px;white-space:nowrap;line-height:14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 8px;text-align:left;border-bottom:1px solid #1f2731;white-space:nowrap}
th{color:var(--dim);font-weight:500;font-size:12px;position:sticky;top:0;background:var(--panel)}
td.num{font-variant-numeric:tabular-nums;text-align:right}
tr:hover td{background:#1a212b}
.up{color:var(--red)}
.slnk{color:inherit;text-decoration:none;border-bottom:1px dotted #3f4f63}
.slnk:hover{color:#4a9eff;border-bottom-color:#4a9eff}
.ice{color:#7fc4ff}
.gold{color:#e8b44a}
.down{color:var(--green)}
.tblwrap{max-height:460px;overflow:auto;border-radius:6px}
.legend{display:flex;gap:16px;font-size:12px;color:var(--dim);margin-top:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.empty{color:var(--dim);padding:20px;text-align:center}
.note{color:var(--dim);font-size:12px;margin-top:10px;line-height:1.7}
footer{color:#55606e;font-size:12px;text-align:center;padding:18px}
/* ---- 个股弹窗 ---- */
.modal{position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:200;display:none;
  align-items:center;justify-content:center;padding:16px}
.modal.show{display:flex}
.mbox{background:#161b22;border:1px solid #3a4a5e;border-radius:12px;
  width:min(940px,100%);max-height:92vh;display:flex;flex-direction:column;
  box-shadow:0 18px 60px rgba(0,0,0,.55)}
.mhead{display:flex;align-items:center;gap:14px;padding:10px 14px;border-bottom:1px solid #222c38}
.mhead .mtitle{font-size:15px;font-weight:600}
.mhead .mtitle small{color:#6b7889;font-weight:400;margin-left:8px;font-size:12px}
.mtabs{display:flex;gap:6px;margin-left:auto}
.mtab{background:#1c2330;border:1px solid #2a3441;color:#a9b6c6;border-radius:6px;
  padding:4px 12px;font-size:12.5px;cursor:pointer;font-family:inherit}
.mtab.on{background:#1d3a5c;border-color:#4a9eff;color:#cfe6ff}
.mclose{background:none;border:none;color:#8b98a8;font-size:22px;cursor:pointer;
  line-height:1;padding:0 4px}
.mclose:hover{color:#fff}
.mbody{padding:10px 14px 16px;overflow:auto}
.mbody .empty{padding:40px;text-align:center;color:#8b98a8}
.mstats{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:#8b98a8;margin-bottom:8px}
.mstats b{color:#d8e0ea;font-weight:600}
.watchlist{display:flex;flex-direction:column;gap:6px}
.wrow{display:flex;align-items:center;gap:9px;padding:6px 8px;background:#1a212b;
  border:1px solid #222c38;border-radius:7px;font-size:12.5px}
.wrow:hover{border-color:#3a4a5e;background:#1d2531}
.wrank{flex:none;width:18px;text-align:center;color:#e8b44a;font-weight:700}
.wname{font-weight:600;font-size:13px;min-width:66px}
.wb{color:#e8b44a;font-weight:600;flex:none}
.wa{color:#8b98a8;font-variant-numeric:tabular-nums;flex:none}
.wf{color:#6b7889;flex:none;font-variant-numeric:tabular-nums}
.wa2{color:#55606e;flex:none;font-variant-numeric:tabular-nums;font-size:11.5px}
.wtag{margin-left:auto;color:#7fc4ff;font-size:11px;background:#152535;
  border:1px solid #24485f;border-radius:4px;padding:0 5px;white-space:nowrap}
.wtag.new{color:#7ee787;background:#152b1c;border-color:#245f2f}
.wtag.hot{color:#c9a86a;background:#2a2216;border-color:#4d3f24;font-weight:500}
.str{display:inline-block;min-width:26px;text-align:center;font-weight:700;font-size:11.5px;
  color:#7ee787;background:#152b1c;border:1px solid #245f2f;border-radius:4px;padding:0 4px}
.wcfg{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-bottom:9px;
  font-size:12px;color:#8b98a8}
.wcfg label{display:flex;align-items:center;gap:5px;white-space:nowrap}
.wcfg select{background:#1c2330;color:#d8e0ea;border:1px solid #2a3441;border-radius:5px;
  padding:2px 6px;font-size:12px;font-family:inherit;outline:none;cursor:pointer}
.wcfg select:focus{border-color:#4a9eff}
.wcfg .ck{cursor:pointer}
.wcfg input[type=checkbox]{accent-color:#4a9eff;cursor:pointer}
</style>
</head>
<body>
<header>
  <h1>复盘工作台 <span class="fish">· 珠珠</span></h1>
  <div class="spacer"></div>
  <button class="navbtn" id="prevDay" title="前一天">‹</button>
  <select id="dateSel"></select>
  <button class="navbtn" id="nextDay" title="后一天">›</button>
  <button class="tbtn" id="editBtn">布局</button>
  <span class="stamp" id="stamp"></span>
</header>
<main>
  <div class="hiddenTray" id="hiddenTray">
    <span>已隐藏的板块：</span><span id="hiddenList"></span>
    <span style="color:#55606e">（把板块隐藏后可以从这里恢复）</span>
  </div>

  <div class="cards mod" data-mod="cards" id="cards"></div>

  <div class="row mod" data-mod="row">
    <div class="panel">
      <h2>明日重点关注 <span class="tag" id="watchCount"></span></h2>
      <div class="wcfg">
        <label>最低板数
          <select id="cfgBoard">
            <option value="2">2板</option>
            <option value="3">3板</option>
            <option value="4">4板</option>
          </select>
        </label>
        <label>最少
          <select id="cfgCount">
            <option value="5">5只</option>
            <option value="8">8只</option>
            <option value="10">10只</option>
          </select>
        </label>
        <label class="ck"><input type="checkbox" id="cfgTop" checked> 只列板块最高</label>
      </div>
      <div id="watch" class="watchlist"></div>
      <div class="note" id="watchNote"></div>
    </div>
    <div class="panel">
      <h2>题材分类 <span class="tag" id="themeCount"></span></h2>
      <div class="themes" id="themes"></div>
      <div class="note">一票归一类，计数加起来等于涨停总数；点分类可筛选下方明细，分类可以按住拖动排序。</div>
    </div>
  </div>

  <div class="panel mod" data-mod="ladder">
    <h2>连板梯队 <span class="tag" id="ladderCount"></span>
      <label class="firstToggle"><input type="checkbox" id="showFirst"> 含首板</label>
    </h2>
    <div class="ladder" id="ladder"></div>
  </div>

  <div class="panel mod" data-mod="zt">
    <h2>涨停明细 <span class="tag" id="ztCount"></span></h2>
    <div class="tblwrap" id="ztTable"></div>
  </div>

  <div class="panel mod" data-mod="dt">
    <h2>跌停 <span class="tag" id="dtCount"></span></h2>
    <div class="tblwrap" id="dtTable"></div>
  </div>

  <div class="note mod" data-mod="note">
    <b>情绪标签规则（参考用，不是买卖依据）：</b>
    高潮=涨停≥100；冰点=跌停≥30 且 涨停≤45；冰点修复=前日冰点、今日涨停回升≥55；
    退潮=前日涨停≥90、今日涨停跌破前日的 75% 且跌停增加；其余为平淡。
  </div>
</main>
<div class="modal" id="stockModal">
  <div class="mbox">
    <div class="mhead">
      <div class="mtitle" id="mTitle">—</div>
      <div class="mtabs">
        <button class="mtab on" data-tab="k">日K</button>
        <button class="mtab" data-tab="t">分时</button>
      </div>
      <button class="mclose" id="mClose">×</button>
    </div>
    <div class="mbody" id="mBody"></div>
  </div>
</div>
<footer>数据来源：同花顺涨停池 / 跌停池 · 本地生成，不构成投资建议</footer>

<script>
const DATA = __DATA__;
const THEMES = __THEMES__;
</script>
<script>
const $ = id => document.getElementById(id);
const fmtW = v => (v >= 1e8) ? (v/1e8).toFixed(2)+"亿" : (v >= 1e4) ? (v/1e4).toFixed(0)+"万" : v;
let curDay = null, curTheme = null;


function renderCards(d){
  const ratio = d.dt ? (d.zt/d.dt).toFixed(2) : "∞";
  const nodeTxt = (d.node && d.node.length)
    ? d.node.map(x => `${x.n}${x.b}板`).join("、")
    : "";
  const cards = [
    {k:"涨停家数", v:d.zt, c:"up", s:"封板"},
    {k:"跌停家数", v:d.dt, c:d.dt>=20?"down":"", s:"跌停"},
    {k:"最高连板", v:d.mb+"板", c:"", s:"当日高度"},
    {k:"开板家数", v:d.oc, c:"", s:"日内炸过板"},
    {k:"涨跌停比", v:ratio, c:"", s:"涨停 ÷ 跌停"},
    {k:"节点日", v: nodeTxt ? "⚠ 断板" : "—", c: nodeTxt ? "down" : "",
      s: nodeTxt || "无 5 板+ 断板"},
    {k:"明日预测", v: d.predNode ? ("⚠ " + d.nextBreak + "% 断板") : (d.nextBreak + "% 断板"),
      c: d.predNode ? "down" : "",
      s: d.predNode ? "按节点规则选股" : "按常规规则选股"},
    {k:"市场情绪", v:"", c:"", s:"", badge:d.st}
  ];
  $("cards").innerHTML = cards.map(c=>{
    if(c.badge) return `<div class="card"><div class="k">${c.k}</div>
      <div class="v"><span class="badge b-${c.badge}">${c.badge}</span></div></div>`;
    return `<div class="card"><div class="k">${c.k}</div>
      <div class="v ${c.c}">${c.v}</div><div class="s">${c.s}</div></div>`;
  }).join("");
}

function renderChart(){
  const idx = DATA.days.findIndex(x=>x.d===curDay);
  const slice = DATA.days.slice(Math.max(0, idx-39), idx+1);
  const W = 760, H = 230, padL = 34, padB = 26, padT = 12, padR = 10;
  const n = slice.length;
  const bw = (W - padL - padR) / Math.max(n,1);
  const maxZT = Math.max(20, ...slice.map(d=>Math.max(d.zt, d.dt)));
  const scaleY = v => H - padB - (v/maxZT) * (H - padT - padB);
  let bars = "";
  slice.forEach((d,i)=>{
    const x = padL + i*bw + bw*0.18;
    const w = bw*0.28;
    const h1 = Math.max(1,(H-padB)-scaleY(d.zt));
    const h2 = Math.max(1,(H-padB)-scaleY(d.dt));
    const isCur = d.d===curDay;
    const isNode = d.node && d.node.length;
    bars += `<rect x="${x}" y="${scaleY(d.zt)}" width="${w}" height="${h1}" fill="${isCur?'#ff7a77':'#f0524f'}" rx="1.5"${isNode?' stroke="#ffd166" stroke-width="1.6"':''}><title>${d.d} 涨停 ${d.zt}${isNode?"（节点日）":""}</title></rect>`;
    bars += `<rect x="${x+w+1.5}" y="${scaleY(d.dt)}" width="${w}" height="${h2}" fill="${isCur?'#4fe0a8':'#2ecc8f'}" rx="1.5"><title>${d.d} 跌停 ${d.dt}</title></rect>`;
  });
  // 连板折线
  const maxB = Math.max(3, ...slice.map(d=>d.mb));
  const byScale = v => H - padB - (v/maxB)*(H-padT-padB);
  const pts = slice.map((d,i)=>`${padL + i*bw + bw*0.5},${byScale(d.mb)}`).join(" ");
  // x 轴标签
  let labels = "";
  slice.forEach((d,i)=>{
    if (i % Math.ceil(n/8) === 0 || i === n-1) {
      labels += `<text x="${padL+i*bw+bw*0.5}" y="${H-8}" fill="#6b7889" font-size="10" text-anchor="middle">${d.d.slice(5)}</text>`;
    }
  });
  // y 轴
  let ys = "";
  [0, Math.round(maxZT/2), maxZT].forEach(v=>{
    ys += `<text x="${padL-6}" y="${scaleY(v)+4}" fill="#6b7889" font-size="10" text-anchor="end">${v}</text>`;
  });
  $("chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">
    ${ys}${bars}
    <polyline points="${pts}" fill="none" stroke="#e8b44a" stroke-width="1.6" opacity="0.85"/>
    ${slice.map((d,i)=>`<circle cx="${padL+i*bw+bw*0.5}" cy="${byScale(d.mb)}" r="2.6" fill="#e8b44a"><title>${d.d} 最高 ${d.mb}板</title></circle>`).join("")}
    ${labels}
  </svg>`;
}

/* ---------------- 用户偏好（存浏览器本地） ---------------- */
const LS = {
  get(k, def) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : def; } catch (e) { return def; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
};
const MODULE_NAMES = { cards:"概览卡片", row:"趋势图 + 题材", ladder:"连板梯队", zt:"涨停明细", dt:"跌停", note:"情绪规则说明" };
let modOrder  = LS.get("fupan_mod_order", ["cards","row","ladder","zt","dt","note"]);
let modHidden = LS.get("fupan_mod_hidden", []);
let themeOrder = LS.get("fupan_theme_order", []);
let sortState = { key: null, dir: 1 };
let dtSortState = { key: null, dir: 1 };
let dragging = null;

/* 每个分类一个固定颜色（同色系的底色+描边） */
const CAT_COLORS = {
  "名称炒作": "#ff9b8a", "消费电子": "#7fd1ff", "算力租赁": "#6ee7ff", "机器人": "#b794ff",
  "AI应用":   "#8bb8ff", "半导体":   "#ffcd6b", "光伏":     "#ffd166", "储能电池": "#7ee787",
  "医药":     "#ff8fb1", "军工":     "#9aa7b8", "低空经济": "#6fe0c8", "大农业":   "#a3d977",
  "化工":     "#d4a017", "有色金属": "#c0a080", "电力能源": "#7fc8ff", "新能源车": "#9be8ff",
  "房地产":   "#c9a26b", "大消费":   "#ffb37f", "国资重组": "#8f9bb3", "其他概念": "#7d8896"
};
function catColor(c) { return CAT_COLORS[c] || "#8b98a8"; }
function catTag(c) {
  const name = c || "其他";
  const col = catColor(name);
  return `<span class="cat-tag" style="color:${col};background:${col}1f;border-color:${col}55">${name}</span>`;
}
const ZT_COLS = [
  { k:"c",   label:"代码",      cls:"" },
  { k:"n",   label:"名称",      cls:"" },
  { k:"p",   label:"价格",      cls:"num" },
  { k:"g",   label:"涨幅",      cls:"num" },
  { k:"b",   label:"连板",      cls:"" },
  { k:"strength", label:"强度", cls:"num" },
  { k:"cat", label:"分类",      cls:"" },
  { k:"t",   label:"类型",      cls:"" },
  { k:"f",   label:"首封",      cls:"" },
  { k:"o",   label:"开板",      cls:"num" },
  { k:"a",   label:"封单",      cls:"num" },
  { k:"vr",  label:"换手",      cls:"num" },
  { k:"r",   label:"涨停分析",  cls:"" }
];
const DT_COLS = [
  { k:"c", label:"代码", cls:"" },
  { k:"n", label:"名称", cls:"" },
  { k:"p", label:"价格", cls:"num" },
  { k:"g", label:"跌幅", cls:"num" },
  { k:"f", label:"首封", cls:"" },
  { k:"r", label:"跌停原因", cls:"" }
];
const NUMERIC = new Set(["p","g","b","o","a","vr"]);

function sortList(list, key, dir) {
  if (!key) return list;
  return [...list].sort((a, b) => {
    let x = a[key], y = b[key];
    if (key === "f") { x = (x === "-" || !x) ? "99:99:99" : x; y = (y === "-" || !y) ? "99:99:99" : y; }
    if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
    if (key === "cat" || key === "t") { return String(x || "").localeCompare(String(y || ""), "zh") * dir; }
    if (key === "f" || key === "c") { return String(x).localeCompare(String(y)) * dir; }
    return String(x == null ? "" : x).localeCompare(String(y == null ? "" : y), "zh") * dir;
  });
}

function headerHTML(cols, st) {
  return cols.map(c => `<th class="${c.cls} sortable" data-k="${c.k}">${c.label}` +
    (st.key === c.k ? `<span class="arr">${st.dir > 0 ? "▲" : "▼"}</span>` : "") +
    `</th>`).join("");
}

function bindSort(tableId, cols, st) {
  document.querySelectorAll(`#${tableId} th.sortable`).forEach(th => {
    th.onclick = () => {
      const k = th.dataset.k;
      if (st.key === k) st.dir *= -1;
      else { st.key = k; st.dir = NUMERIC.has(k) ? -1 : 1; }
      const d = DATA.days.find(x => x.d === curDay);
      if (d) (tableId === "ztTable" ? renderZT(d) : renderDT(d));
    };
  });
}

/* ---------------- 板块布局 ---------------- */
function applyLayout() {
  document.querySelectorAll("main > [data-mod]").forEach(el => {
    const id = el.dataset.mod;
    const i = modOrder.indexOf(id);
    el.style.order = i >= 0 ? i : 99;
    el.classList.toggle("hid", modHidden.includes(id));
  });
  const hl = $("hiddenList");
  if (hl) {
    const hidden = [...document.querySelectorAll("main > [data-mod]")].filter(el => modHidden.includes(el.dataset.mod));
    hl.innerHTML = hidden.length
      ? hidden.map(el => `<button class="tbtn" data-restore="${el.dataset.mod}" style="padding:2px 9px;font-size:12px">↺ ${MODULE_NAMES[el.dataset.mod] || el.dataset.mod}</button>`).join("")
      : '<span style="color:#55606e">无</span>';
    hl.querySelectorAll("[data-restore]").forEach(b => {
      b.onclick = () => {
        modHidden = modHidden.filter(x => x !== b.dataset.restore);
        LS.set("fupan_mod_hidden", modHidden);
        applyLayout();
      };
    });
  }
}

function buildModCtls() {
  document.querySelectorAll("main > [data-mod]").forEach(el => {
    if (el.querySelector(":scope > .modctl")) return;
    const ctl = document.createElement("div");
    ctl.className = "modctl";
    ctl.innerHTML = `<button data-act="up">↑ 上移</button><button data-act="down">↓ 下移</button><button data-act="hide">隐藏</button>`;
    ctl.querySelectorAll("button").forEach(b => {
      b.onclick = (e) => {
        e.stopPropagation();
        const id = el.dataset.mod;
        const i = modOrder.indexOf(id);
        if (b.dataset.act === "up" && i > 0) { modOrder.splice(i, 1); modOrder.splice(i - 1, 0, id); }
        if (b.dataset.act === "down" && i >= 0 && i < modOrder.length - 1) { modOrder.splice(i, 1); modOrder.splice(i + 1, 0, id); }
        if (b.dataset.act === "hide") { if (!modHidden.includes(id)) modHidden.push(id); }
        LS.set("fupan_mod_order", modOrder);
        LS.set("fupan_mod_hidden", modHidden);
        applyLayout();
      };
    });
    el.appendChild(ctl);
  });
}

function renderThemes(d){
  let list = (THEMES[d.d] || []).slice();
  $("themeCount").textContent = `共 ${list.length} 个分类`;
  if(!list.length){ $("themes").innerHTML = '<div class="empty">无题材分类</div>'; return; }
  if (themeOrder.length) {
    list.sort((a, b) => {
      const ia = themeOrder.indexOf(a.k), ib = themeOrder.indexOf(b.k);
      if (ia < 0 && ib < 0) return 0;
      if (ia < 0) return 1;
      if (ib < 0) return -1;
      return ia - ib;
    });
  }
  const max = Math.max(...list.map(t => t.v), 1);
  $("themes").innerHTML = list.map(t=>`
    <div class="theme drag ${curTheme===t.k?'on':''}" data-k="${t.k.replace(/"/g,'&quot;')}" draggable="true">
      <div class="name">${t.k}</div>
      <div class="bar"><i style="width:${Math.max(6,t.v/max*100)}%"></i></div>
      <div class="cnt">${t.v}</div>
    </div>`).join("");
  $("themes").querySelectorAll(".theme").forEach(el=>{
    el.onclick = ()=>{ curTheme = (curTheme===el.dataset.k)?null:el.dataset.k; renderThemes(d); renderZT(d); };
    el.ondragstart = (e)=>{ dragging = el; el.classList.add("dragging"); try{ e.dataTransfer.effectAllowed="move"; }catch(_){} };
    el.ondragend  = ()=>{ el.classList.remove("dragging"); dragging = null; };
    el.ondragover = (e)=>{ if(dragging && dragging!==el) e.preventDefault(); };
    el.ondrop     = (e)=>{
      e.preventDefault();
      if (!dragging || dragging===el) return;
      const nodes = [...$("themes").querySelectorAll(".theme")];
      const names = nodes.map(x=>x.dataset.k);
      const from = names.indexOf(dragging.dataset.k);
      const to = names.indexOf(el.dataset.k);
      if (from < 0 || to < 0) return;
      names.splice(from,1);
      names.splice(to,0,dragging.dataset.k);
      themeOrder = names;
      LS.set("fupan_theme_order", themeOrder);
      renderThemes(d);
    };
  });
}

let showFirst = LS.get("fupan_show_first", false);

function renderLadder(d){
  const idx = DATA.days.findIndex(x => x.d === curDay);
  const span = DATA.days.slice(Math.max(0, idx - 4), idx + 1).reverse();
  if (!span.length) { $("ladder").innerHTML = '<div class="empty">无数据</div>'; return; }
  $("ladderCount").textContent = `${span.length} 日并排对比`;

  const maxB = Math.max(2, ...span.map(x => x.mb));
  const chipOf = (s, day) =>
    `<a class="chip" data-cat="${(s.cat||'').replace(/"/g,'&quot;')}" data-day="${day}" ` +
    `href="https://stockpage.10jqka.com.cn/${s.c}/" target="_blank" rel="noopener" ` +
    `title="首封 ${s.f} · 封单 ${fmtW(s.a)}">${s.n}${catTag(s.cat)}</a>`;

  let html = '<div class="lgrid">';
  html += '<div class="lcell lhead lv">板 / 日</div>';
  span.forEach(day => {
    html += `<div class="lcell lhead ldate" data-day="${day.d}" title="点击切到 ${day.d}">${day.d.slice(4)}</div>`;
  });

  for (let b = maxB; b >= 2; b--) {
    html += `<div class="lcell lv">${b}板</div>`;
    span.forEach(day => {
      const items = day.ztl.filter(s => s.b === b)
        .sort((x,y)=> (x.f==="-"?"99:99":x.f).localeCompare(y.f==="-"?"99:99":y.f));
      html += `<div class="lcell">${items.map(s=>chipOf(s, day.d)).join("")}</div>`;
    });
  }

  html += '<div class="lcell lv">首板</div>';
  span.forEach(day => {
    const items = day.ztl.filter(s => s.b < 2);
    if (showFirst) {
      html += `<div class="lcell">${items.map(s=>chipOf(s, day.d)).join("")}</div>`;
    } else {
      html += `<div class="lcell"><span class="firstHint" data-day="${day.d}">+${items.length}</span></div>`;
    }
  });
  html += '</div>';
  $("ladder").innerHTML = html;

  const ladderEl = $("ladder");
  ladderEl.querySelectorAll(".chip").forEach(el => {
    el.onmouseenter = () => {
      ladderEl.classList.add("hovering");
      const c = el.dataset.cat || "";
      ladderEl.querySelectorAll(".chip").forEach(x => {
        x.classList.toggle("lit", (x.dataset.cat || "") === c);
      });
    };
    el.onmouseleave = () => {
      ladderEl.classList.remove("hovering");
      ladderEl.querySelectorAll(".chip").forEach(x => x.classList.remove("lit"));
    };
  });

  ladderEl.querySelectorAll(".ldate").forEach(el => {
    el.onclick = () => {
      const day = el.dataset.day;
      const sel = $("dateSel");
      if (sel) sel.value = day;
      renderDay(day);
    };
  });

  ladderEl.querySelectorAll(".firstHint").forEach(el => {
    el.onclick = () => {
      showFirst = true;
      LS.set("fupan_show_first", true);
      const cb = $("showFirst");
      if (cb) cb.checked = true;
      renderLadder(DATA.days.find(x => x.d === curDay));
    };
  });
}

function renderZT(d){
  let list = d.ztl;
  if(curTheme) list = list.filter(s=>s.cat===curTheme);
  list = sortList(list, sortState.key, sortState.dir);
  $("ztCount").textContent = curTheme ? `筛选「${curTheme}」${list.length} 只` : `${list.length} 只`;
  if(!list.length){ $("ztTable").innerHTML = '<div class="empty">无数据</div>'; return; }
  $("ztTable").innerHTML = `<table><thead><tr>${headerHTML(ZT_COLS, sortState)}</tr></thead><tbody>${list.map(s=>`<tr>
    <td style="color:#6b7889">${s.c}</td>
    <td><a class="slnk" style="display:block;font-weight:700" href="https://stockpage.10jqka.com.cn/${s.c}/" target="_blank" rel="noopener">${s.n}</a></td>
    <td class="num">${s.p}</td>
    <td class="num up">+${s.g}%</td>
    <td>${s.b>=2?'<span style="color:#e8b44a">'+s.b+'板</span>':'首板'}</td>
    <td class="num"><span class="str">${(s.strength||0).toFixed(0)}</span></td>
    <td>${catTag(s.cat)}</td>
    <td style="color:#8b98a8">${s.t||"-"}</td>
    <td>${s.f}</td>
    <td class="num">${s.o||"-"}</td>
    <td class="num">${fmtW(s.a)}</td>
    <td class="num">${s.vr}%</td>
    <td style="color:#8b98a8;max-width:320px;overflow:hidden;text-overflow:ellipsis">${s.r||"-"}</td>
  </tr>`).join("")}</tbody></table>`;
  bindSort("ztTable", ZT_COLS, sortState);
}

function renderDT(d){
  const list = sortList(d.dtl, dtSortState.key, dtSortState.dir);
  $("dtCount").textContent = `${list.length} 只`;
  if(!list.length){ $("dtTable").innerHTML = '<div class="empty">今日无跌停</div>'; return; }
  $("dtTable").innerHTML = `<table><thead><tr>${headerHTML(DT_COLS, dtSortState)}</tr></thead><tbody>${list.map(s=>`<tr>
    <td style="color:#6b7889">${s.c}</td><td><a class="slnk" style="display:block;font-weight:700" href="https://stockpage.10jqka.com.cn/${s.c}/" target="_blank" rel="noopener">${s.n}</a></td>
    <td class="num">${s.p}</td><td class="num down">${s.g}%</td>
    <td>${s.f}</td><td style="color:#8b98a8">${s.r||"-"}</td>
  </tr>`).join("")}</tbody></table>`;
  bindSort("dtTable", DT_COLS, dtSortState);
}

let watchCfg = LS.get("fupan_watch_cfg", { board: 2, sortBy: "fr", count: 5, topOnly: true });

function frOf(s) {
  const cv = s.cv || 0;
  return cv > 0 ? ((s.a || 0) / cv * 100) : 0;
}

function computeWatch(d, cfg) {
  // 选股一律按封流比
  const cmp = (x, y) => frOf(y) - frOf(x);
  const mb = d.mb || 0;
  const picked = [];
  const seen = new Set();
  const push = (s, why) => {
    if (seen.has(s.c)) return;
    seen.add(s.c);
    picked.push({ c: s.c, n: s.n, b: s.b, cat: s.cat, a: s.a || 0,
                  fr: frOf(s), strength: s.strength || 0,
                  f: s.f || "-", why, mkt: (s.b === mb) });
  };

  // 断板票的题材（需要前一日数据）
  const idx = DATA.days.findIndex(x => x.d === d.d);
  const prev = idx > 0 ? DATA.days[idx - 1] : null;
  const prevMap = {};
  if (prev) prev.ztl.forEach(s => { prevMap[s.c] = s; });
  const brokeCats = new Set((d.node || []).map(x => (prevMap[x.c] || {}).cat).filter(Boolean));

  // 近 5 日出现过的分类
  const recent = new Set();
  for (let j = Math.max(0, idx - 5); j < idx; j++) {
    DATA.days[j].ztl.forEach(s => recent.add(s.cat || "其他概念"));
  }

  if (!d.predNode) {
    if (cfg.topOnly) {
      const best = {};
      d.ztl.forEach(s => {
        if ((s.b || 1) < cfg.board) return;
        const c = s.cat || "其他概念";
        const cur = best[c];
        if (!cur || s.b > cur.b || (s.b === cur.b && cmp(s, cur) < 0)) best[c] = s;
      });
      Object.values(best).sort(cmp).forEach(s => push(s, "板块最高板"));
    } else {
      d.ztl.filter(s => (s.b || 1) >= cfg.board)
        .sort(cmp)
        .forEach(s => push(s, (s.b || 1) + "连板"));
    }
  } else {
    // 1) 市场最高板优先入选（不看题材）
    d.ztl.filter(s => (s.b || 1) === mb && (s.b || 1) >= 2)
      .sort((x, y) => frOf(y) - frOf(x))
      .forEach(s => push(s, "市场最高板"));

    // 2) 断板同题材 / 新题材（2~3 板）
    d.ztl.forEach(s => {
      const b = s.b || 1;
      if (b !== 2 && b !== 3) return;
      const cat = s.cat || "其他概念";
      if (brokeCats.has(cat)) push(s, "断板同题材");
      else if (!recent.has(cat) && cat !== "其他概念") push(s, "新题材");
    });
  }

  // 不足最少只数：用其余 2 板以上的票补足
  if (picked.length < cfg.count) {
    const rest = d.ztl
      .filter(s => (s.b || 1) >= 2 && !seen.has(s.c))
      .sort(cmp);
    for (const s of rest) {
      if (picked.length >= cfg.count) break;
      if (seen.has(s.c)) continue;
      seen.add(s.c);
      picked.push({ c: s.c, n: s.n, b: s.b, cat: s.cat, a: s.a || 0,
                    fr: frOf(s), strength: s.strength || 0,
                    f: s.f || "-", why: "封流比靠前", mkt: (s.b === mb) });
    }
  }
  // 显示顺序：最高连板往下排，同板数按封流比
  picked.sort((a, b) => (b.b - a.b) || (b.fr - a.fr));
  return picked;
}

function bindWatchCfg() {
  const b = $("cfgBoard"), c = $("cfgCount"), t = $("cfgTop");
  if (!b) return;
  b.value = String(watchCfg.board);
  c.value = String(watchCfg.count);
  t.checked = !!watchCfg.topOnly;
  const onChange = () => {
    watchCfg = { board: +b.value, sortBy: "fr", count: +c.value, topOnly: t.checked };
    LS.set("fupan_watch_cfg", watchCfg);
    const d = DATA.days.find(x => x.d === curDay);
    if (d) renderWatch(d);
  };
  [b, c, t].forEach(el => { el.onchange = onChange; });
}
function renderWatch(d){
  const list = computeWatch(d, watchCfg);
  $("watchCount").textContent = list.length ? `${list.length} 只` : "";
  const cfgTxt = `最低${watchCfg.board}板 · 选股按封流比 · 按板数从高到低显示 · 至少${watchCfg.count}只`;
  $("watchNote").textContent = (d.predNode
    ? `预测明日断板（${d.nextBreak}%）：优先断板同题材 / 新题材；不足则补足`
    : `预测明日不断板（${d.nextPromote}% 晋级）：各板块最高连板`) + " · " + cfgTxt;
  if (!list.length) {
    $("watch").innerHTML = '<div class="empty">今日无符合条件的标的</div>';
    return;
  }
  $("watch").innerHTML = list.map((s, i) => `
    <div class="wrow">
      <span class="wrank">${i + 1}</span>
      <a class="slnk wname" href="https://stockpage.10jqka.com.cn/${s.c}/" target="_blank" rel="noopener">${s.n}</a>
      ${catTag(s.cat)}
      <span class="wb">${s.b}板</span>
      <span class="str">${(s.strength||0).toFixed(0)}</span>
      <span class="wa">封流比 ${(s.fr||0).toFixed(2)}%</span>
      <span class="wa2">封单 ${fmtW(s.a)}</span>
      <span class="wf">${s.f}</span>
      ${s.mkt ? '<span class="wtag hot">市场最高板</span>' : ''}
      <span class="wtag${s.why === "新题材" ? " new" : ""}">${s.why}</span>
    </div>`).join("");
}

function stepDay(delta) {
  const idx = DATA.days.findIndex(x => x.d === curDay);
  const ni = idx + delta;
  if (ni < 0 || ni >= DATA.days.length) return;
  const d = DATA.days[ni];
  const sel = $("dateSel");
  if (sel) sel.value = d.d;
  renderDay(d.d);
}

function updateNav() {
  const idx = DATA.days.findIndex(x => x.d === curDay);
  const p = $("prevDay"), n = $("nextDay");
  if (p) p.disabled = idx <= 0;
  if (n) n.disabled = idx >= DATA.days.length - 1;
}
function renderDay(date){
  curDay = date; curTheme = null;
  const d = DATA.days.find(x=>x.d===date);
  if(!d) return;
  renderCards(d); renderWatch(d); renderThemes(d); renderLadder(d); renderZT(d); renderDT(d);
  updateNav();
}

function init(){
  const sel = $("dateSel");
  sel.innerHTML = DATA.days.slice().reverse().map(d=>
    `<option value="${d.d}">${d.d.slice(4)}  ${d.st}${(d.node&&d.node.length)?" ⚠节点":""}</option>`).join("");
  sel.onchange = ()=> renderDay(sel.value);
  $("prevDay").onclick = () => stepDay(-1);
  $("nextDay").onclick = () => stepDay(1);
  $("stamp").textContent = `数据 ${DATA.days.length} 个交易日 · 生成于 ${DATA.built}`;

  const sf = $("showFirst");
  if (sf) {
    sf.checked = showFirst;
    sf.onchange = () => {
      showFirst = sf.checked;
      LS.set("fupan_show_first", showFirst);
      renderLadder(DATA.days.find(x => x.d === curDay));
    };
  }
  bindWatchCfg();
  buildModCtls();
  applyLayout();
  const eb = $("editBtn");
  eb.onclick = () => {
    document.body.classList.toggle("editing");
    eb.classList.toggle("on", document.body.classList.contains("editing"));
  };

  renderDay(DATA.days[DATA.days.length-1].d);
}
/* ================= 个股弹窗：同花顺 K线 / 分时 ================= */
const API_BASE = (location.protocol === "file:") ? "http://127.0.0.1:8080" : "";
let mState = { code: null, name: "", k: null, t: null, tab: "k" };

function openStock(code, name) {
  if (!code) return;
  mState = { code, name: name || code, k: null, t: null, tab: "k" };
  $("mTitle").innerHTML = `${name || code}<small>${code}</small>`;
  $("stockModal").classList.add("show");
  $("mBody").innerHTML = '<div class="empty">加载中…</div>';
  Promise.all([
    fetch(`${API_BASE}/api/ths/kline?code=${code}`).then(r => r.json()),
    fetch(`${API_BASE}/api/ths/trends?code=${code}`).then(r => r.json()),
  ]).then(([k, t]) => {
    if (k && k.error) throw new Error(k.error);
    mState.k = k;
    mState.t = t;
    renderM();
  }).catch(e => {
    $("mBody").innerHTML = `<div class="empty">加载失败：${e.message}<br>
      <span style="color:#6b7889;font-size:12px">请确认本地服务在运行（双击「启动服务.bat」）</span></div>`;
  });
}

function closeStock() {
  $("stockModal").classList.remove("show");
}

function renderM() {
  document.querySelectorAll(".mtab").forEach(b => b.classList.toggle("on", b.dataset.tab === mState.tab));
  if (mState.tab === "k") drawKline(); else drawTrends();
}

function drawKline() {
  const j = mState.k;
  if (!j || !j.data) { $("mBody").innerHTML = '<div class="empty">无 K 线数据</div>'; return; }
  const rows = j.data.split(";").filter(Boolean).slice(-120).map(s => {
    const p = s.split(",");
    return { d: p[0], o: +p[1], h: +p[2], l: +p[3], c: +p[4], v: +p[5] };
  });
  if (!rows.length) { $("mBody").innerHTML = '<div class="empty">无 K 线数据</div>'; return; }

  const last = rows[rows.length - 1];
  const first = rows[0];
  const chg = ((last.c - first.c) / first.c * 100);

  const W = 900, H = 400, chartH = 300, volH = 62, padL = 54, padR = 12, padT = 12, padB = 24;
  const n = rows.length;
  const maxP = Math.max(...rows.map(r => r.h));
  const minP = Math.min(...rows.map(r => r.l));
  const maxV = Math.max(...rows.map(r => r.v)) || 1;
  const bw = (W - padL - padR) / n;
  const yP = v => padT + (maxP - v) / ((maxP - minP) || 1) * (chartH - padT - padB);
  const yV = v => chartH + 14 + (1 - v / maxV) * (volH - 14);

  let svg = "";
  for (let i = 0; i <= 4; i++) {
    const price = maxP - (maxP - minP) * i / 4;
    const y = yP(price);
    svg += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#1f2731"/>`;
    svg += `<text x="${padL - 7}" y="${y + 4}" fill="#6b7889" font-size="10" text-anchor="end">${price.toFixed(2)}</text>`;
  }

  rows.forEach((r, i) => {
    const x = padL + i * bw + bw * 0.5;
    const up = r.c >= r.o;
    const col = up ? "#f0524f" : "#2ecc8f";
    const yOpen = yP(r.o), yClose = yP(r.c);
    const top = Math.min(yOpen, yClose);
    const hgt = Math.max(1, Math.abs(yClose - yOpen));
    svg += `<line x1="${x}" y1="${yP(r.h)}" x2="${x}" y2="${yP(r.l)}" stroke="${col}" stroke-width="1"/>`;
    svg += `<rect x="${x - bw * 0.32}" y="${top}" width="${Math.max(1, bw * 0.64)}" height="${hgt}" fill="${col}"/>`;
    svg += `<rect x="${x - bw * 0.32}" y="${yV(r.v)}" width="${Math.max(1, bw * 0.64)}" height="${chartH + volH - yV(r.v)}" fill="${col}" opacity=".5"/>`;
  });

  const step = Math.max(1, Math.ceil(n / 8));
  rows.forEach((r, i) => {
    if (i % step === 0 || i === n - 1) {
      svg += `<text x="${padL + i * bw + bw * 0.5}" y="${H - 7}" fill="#6b7889" font-size="10" text-anchor="middle">${r.d.slice(2)}</text>`;
    }
  });

  $("mBody").innerHTML = `
    <div class="mstats">
      <span>最新 <b style="color:${last.c >= rows[rows.length-2].c ? "#f0524f" : "#2ecc8f"}">${last.c.toFixed(2)}</b></span>
      <span>区间 <b>${rows.length}</b> 个交易日</span>
      <span>区间涨跌 <b style="color:${chg>=0?"#f0524f":"#2ecc8f"}">${chg>=0?"+":""}${chg.toFixed(2)}%</b></span>
      <span>最高 <b>${maxP.toFixed(2)}</b></span>
      <span>最低 <b>${minP.toFixed(2)}</b></span>
      <span style="margin-left:auto;color:#55606e">数据：同花顺 · 前复权</span>
    </div>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${svg}</svg>`;
}

function drawTrends() {
  const t = mState.t;
  const inner = t && (t["hs_" + mState.code] || Object.values(t)[0]);
  if (!inner || !inner.data) { $("mBody").innerHTML = '<div class="empty">无分时数据</div>'; return; }
  const pre = +inner.pre || 0;
  const pts = inner.data.split(";").filter(Boolean).map(s => {
    const p = s.split(",");
    return { tm: p[0], p: +p[1], avg: +p[3], v: +p[4] };
  }).filter(x => x.tm >= "0930" && x.tm <= "1500");
  if (!pts.length) { $("mBody").innerHTML = '<div class="empty">无分时数据</div>'; return; }

  const last = pts[pts.length - 1];
  const chg = pre ? ((last.p - pre) / pre * 100) : 0;

  const W = 900, H = 360, chartH = 268, volH = 62, padL = 54, padR = 12, padT = 12, padB = 24;
  const n = pts.length;
  let maxDev = 0.01;
  pts.forEach(x => { maxDev = Math.max(maxDev, Math.abs(x.p - pre), Math.abs(x.avg - pre)); });
  const hi = pre + maxDev, lo = pre - maxDev;
  const bw = (W - padL - padR) / Math.max(n, 1);
  const yP = v => padT + (hi - v) / ((hi - lo) || 1) * (chartH - padT - padB);
  const maxV = Math.max(...pts.map(x => x.v)) || 1;
  const yV = v => chartH + 14 + (1 - v / maxV) * (volH - 14);

  let svg = "";
  [hi, pre + maxDev / 2, pre, pre - maxDev / 2, lo].forEach((v, i) => {
    const y = yP(v);
    svg += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="${v === pre ? "#3a4a5e" : "#1f2731"}" stroke-dasharray="${v === pre ? "4 4" : "0"}"/>`;
    const pct = pre ? ((v - pre) / pre * 100) : 0;
    svg += `<text x="${padL - 7}" y="${y + 4}" fill="${v > pre ? "#f0524f" : v < pre ? "#2ecc8f" : "#6b7889"}" font-size="10" text-anchor="end">${v.toFixed(2)}</text>`;
    svg += `<text x="${W - padR + 2}" y="${y + 4}" fill="#4a5f78" font-size="9">${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%</text>`;
  });

  const line = pts.map((x, i) => `${padL + i * bw + bw * 0.5},${yP(x.p)}`).join(" ");
  const avgLine = pts.map((x, i) => `${padL + i * bw + bw * 0.5},${yP(x.avg)}`).join(" ");
  svg += `<polyline points="${line}" fill="none" stroke="#4a9eff" stroke-width="1.6"/>`;
  svg += `<polyline points="${avgLine}" fill="none" stroke="#e8b44a" stroke-width="1.1" opacity=".85"/>`;

  pts.forEach((x, i) => {
    const col = x.p >= (i ? pts[i - 1].p : pre) ? "#f0524f" : "#2ecc8f";
    svg += `<rect x="${padL + i * bw + bw * 0.2}" y="${yV(x.v)}" width="${Math.max(1, bw * 0.6)}" height="${chartH + volH - yV(x.v)}" fill="${col}" opacity=".55"/>`;
  });

  ["0930", "1000", "1030", "1100", "1130", "1330", "1400", "1430", "1500"].forEach(tm => {
    const i = pts.findIndex(x => x.tm >= tm);
    if (i < 0) return;
    svg += `<text x="${padL + i * bw + bw * 0.5}" y="${H - 7}" fill="#6b7889" font-size="9" text-anchor="middle">${tm === "1130" ? "11:30/13:00" : tm.slice(0,2)+":"+tm.slice(2)}</text>`;
  });

  $("mBody").innerHTML = `
    <div class="mstats">
      <span>现价 <b style="color:${chg>=0?"#f0524f":"#2ecc8f"}">${last.p.toFixed(2)}</b></span>
      <span>涨幅 <b style="color:${chg>=0?"#f0524f":"#2ecc8f"}">${chg>=0?"+":""}${chg.toFixed(2)}%</b></span>
      <span>均价 <b>${last.avg.toFixed(2)}</b></span>
      <span>昨收 <b>${pre.toFixed(2)}</b></span>
      <span style="color:#6b7889">${inner.date || ""}</span>
      <span style="margin-left:auto;color:#55606e">数据：同花顺</span>
    </div>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${svg}</svg>`;
}

function bindStockClicks() {
  $("mClose").onclick = closeStock;
  $("stockModal").onclick = (e) => { if (e.target.id === "stockModal") closeStock(); };
  document.querySelectorAll(".mtab").forEach(b => {
    b.onclick = () => { mState.tab = b.dataset.tab; renderM(); };
  });
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeStock();
});
init();
bindStockClicks();
</script>
</body>
</html>
"""


def fr_of(s):
    """封流比 = 封单额 / 流通市值 * 100"""
    cv = s.get("cv") or 0
    if cv <= 0:
        return 0.0
    return (s.get("a") or 0) / cv * 100


def compute_strength(rows):
    """
    个股强度（0~100），权重按 4036 笔样本回测的区分度分配：
      封流比 0.28 / 首封时间 0.17 / 连板数 0.15 / 封单额 0.12
      封板类型 0.10 / 流通市值 0.06 / 换手率 0.05 / 炸板次数 0.04 / 题材有最高板 0.03
    """
    def seal_score(f):
        if not f or f == "-":
            return 0.1
        try:
            m = int(f[:2]) * 60 + int(f[3:5])
        except Exception:
            return 0.1
        if m <= 570:
            return 1.0
        if m <= 600:
            return 0.7
        if m <= 690:
            return 0.5
        if m <= 780:
            return 0.35
        if m <= 840:
            return 0.25
        return 0.1

    def cv_score(cv):
        y = cv / 1e8
        if y < 30:
            return 1.0
        if y < 60:
            return 0.85
        if y < 100:
            return 0.6
        if y < 200:
            return 0.4
        return 0.2

    def to_score(v):
        if v < 5:
            return 1.0
        if v < 15:
            return 0.6
        return 0.3

    def open_score(o):
        if o <= 0:
            return 1.0
        if o <= 2:
            return 0.55
        return 0.2

    def type_score(t):
        if t == "一字板":
            return 1.0
        if t == "T字板":
            return 0.55
        return 0.3

    for r in rows:
        if not r["ztl"]:
            continue
        tops = {s.get("cat") for s in r["ztl"] if s["b"] == r["mb"]}
        for s in r["ztl"]:
            cv = s.get("cv") or 0
            fr = ((s.get("a") or 0) / cv * 100) if cv > 0 else 0.0
            amt_yi = (s.get("a") or 0) / 1e8
            b = s.get("b") or 1
            parts = {
                "fr": min(fr / 5.0, 1.0) * 0.28,
                "seal": seal_score(s.get("f")) * 0.17,
                "board": min((b - 1) / 3.0, 1.0) * 0.15,
                "amt": min(amt_yi / 3.0, 1.0) * 0.12,
                "type": type_score(s.get("t") or "") * 0.10,
                "cv": cv_score(cv) * 0.06,
                "to": to_score(float(s.get("vr") or 0)) * 0.05,
                "open": open_score(int(s.get("o") or 0)) * 0.04,
                "top": (1.0 if s.get("cat") in tops else 0.0) * 0.03,
            }
            s["strength"] = round(sum(parts.values()) * 100, 1)
            s["sParts"] = {k: round(v * 100, 1) for k, v in parts.items()}
    return rows

def predict_nodes(rows, short=20):
    """
    节点预测：
      统计「当天最高板 = k 板」时，次日最高板股票是否继续涨停（晋级）。
      用最近 short 个交易日的同类样本（不足 2 个则回退到全量统计）。
      nextBreak = 断板概率；predNode = 预测明天是节点日（断板概率 > 50%）
    """
    from collections import defaultdict

    events = []  # (i, k, promoted)
    for i in range(len(rows) - 1):
        k = rows[i]["mb"]
        if k < 2:
            continue
        codes = [s["c"] for s in rows[i]["ztl"] if s["b"] == k]
        if not codes:
            continue
        nxt = {s["c"] for s in rows[i + 1]["ztl"]}
        promoted = any(c in nxt for c in codes)
        events.append((i, k, promoted))

    all_stat = defaultdict(lambda: [0, 0])
    for _, k, pr in events:
        all_stat[k][0] += 1 if pr else 0
        all_stat[k][1] += 1

    def rate_of(stat, k):
        hit, n = stat[k]
        return (hit / n * 100) if n else None

    for i, r in enumerate(rows):
        k = r["mb"]
        recent = [(kk, pr) for ii, kk, pr in events if ii <= i and ii >= i - short]
        same = [pr for kk, pr in recent if kk == k]
        if len(same) >= 2:
            rate = sum(1 for x in same if x) / len(same) * 100
            src_txt = f"近{len(same)}次同高度样本"
        else:
            rate = rate_of(all_stat, k)
            src_txt = "全量同高度样本" if rate is not None else "无样本"
        if rate is None:
            rate = 50.0
        r["nextPromote"] = round(rate, 1)
        r["nextBreak"] = round(100 - rate, 1)
        r["predNode"] = bool(rate < 50)
        r["predWhy"] = src_txt
    return rows


def build_watch(rows):
    """
    明日重点关注池（按封流比排序，至少 5 只）：
      非节点日：各题材分类里板数最高的票（>=2 板）
      节点日  ：2/3 板 或 当日最高板，且属于「断板票同题材」或「近 5 日新题材」
      严格条件不足 5 只时，用其余 2 板以上的票按封流比补足
    """
    def pack(s, why, mb):
        return {
            "c": s["c"], "n": s["n"], "b": s["b"], "cat": s.get("cat"),
            "a": s.get("a") or 0, "cv": s.get("cv") or 0, "fr": fr_of(s),
            "mkt": (s["b"] == mb),
            "f": s.get("f") or "-", "why": why
        }

    for i, r in enumerate(rows):
        mb = r["mb"]
        picked = []
        seen = set()

        if not r.get("predNode"):
            best = {}
            for s in r["ztl"]:
                if (s.get("b") or 1) < 2:
                    continue
                c = s.get("cat") or "其他概念"
                cur = best.get(c)
                if (cur is None or s["b"] > cur["b"]
                        or (s["b"] == cur["b"] and fr_of(s) > fr_of(cur))):
                    best[c] = s
            for s in sorted(best.values(), key=lambda x: -fr_of(x)):
                if s["c"] in seen:
                    continue
                seen.add(s["c"])
                picked.append(pack(s, "板块最高板", mb))
            r["watchWhy"] = "各板块最高连板（2板以上），按封流比排序"
        else:
            broke_cats = set()
            if i > 0:
                prev_map = {s["c"]: s for s in rows[i - 1]["ztl"]}
                for x in (r.get("node") or []):
                    ps = prev_map.get(x.get("c"))
                    if ps:
                        broke_cats.add(ps.get("cat") or "其他概念")
            recent = set()
            for j in range(max(0, i - 5), i):
                for s in rows[j]["ztl"]:
                    recent.add(s.get("cat") or "其他概念")

            # 1) 市场最高板优先入选（不看题材）
            tops = [s for s in r["ztl"]
                    if (s.get("b") or 1) == mb and (s.get("b") or 1) >= 2]
            for s in sorted(tops, key=lambda x: -fr_of(x)):
                if s["c"] in seen:
                    continue
                seen.add(s["c"])
                picked.append(pack(s, "市场最高板", mb))

            # 2) 断板同题材 / 新题材（2~3 板）
            cand = []
            for s in r["ztl"]:
                b = s.get("b") or 1
                if b not in (2, 3):
                    continue
                cat = s.get("cat") or "其他概念"
                if cat in broke_cats:
                    cand.append((s, "断板同题材"))
                elif cat not in recent and cat != "其他概念":
                    cand.append((s, "新题材"))
            cand.sort(key=lambda x: -fr_of(x[0]))
            for s, why in cand:
                if s["c"] in seen:
                    continue
                seen.add(s["c"])
                picked.append(pack(s, why, mb))
            r["watchWhy"] = "最高板断板日：断板同题材补涨 / 新题材（2~3板或最高板），按封流比"

        if len(picked) < 5:
            rest = [s for s in r["ztl"]
                    if (s.get("b") or 1) >= 2 and s["c"] not in seen]
            rest.sort(key=lambda x: -fr_of(x))
            for s in rest:
                if len(picked) >= 5:
                    break
                seen.add(s["c"])
                picked.append(pack(s, "封流比靠前", mb))

        r["watch"] = picked
    return rows

def main():
    days = load_days()
    if not days:
        print("no data", file=sys.stderr)
        return 1
    has_trade = apply_boards(days)
    market_state(days)
    build_node_days(days, has_trade)
    compute_daily_indicators(days)

    themes = {d["d"]: theme_stats(d) for d in days}
    compute_strength(days)
    predict_nodes(days)
    build_watch(days)

    payload = {
        "built": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "days": days,
    }

    html = HTML_TMPL.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("__THEMES__", json.dumps(themes, ensure_ascii=False, separators=(",", ":")))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"OK days={len(days)} size={os.path.getsize(OUT)/1024:.0f}KB -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
