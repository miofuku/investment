# -*- coding: utf-8 -*-
"""
signal_tracker.py — 前瞻信号跟踪(唯一诚实的"业绩档案",不是回测)
================================================================
为什么不是回测:免费数据无 point-in-time、无退市股,任何"事后重算历史"的回测都
带前视/幸存者偏差(见 README §7,本系统刻意不做回测)。
本模块反其道而行:**在简报发布的当下,把当时不可复原的判断冻结存档**,之后只用
"发布之后才到来的数据"去对照——这是唯一不带幸存者偏差的成绩单(因为我们是按发布
顺序逐条记录,不是事后挑赢家)。越早开始记,越值钱。

冻结存档的内容(signals.csv,每条简报一行,按 code+signal_date 去重):
  · anchor_price   发布当日收盘(qfq)——价格事后可从行情复原,但仍冻结以简化对照
  · pb             发布当日 PB
  · implied_g      反向DCF市场隐含增速(小数)——**最可证伪的假设**:日后真实增长来了,
                   可对照"市场当时price的预期"是否够得着
  · roe_3y/roic_3y 发布当日质量
  · major_flags    发布当日的重大红旗(日后看这些红旗是否兑现)
这些里只有价格能事后复原;PB/隐含增速/红旗是"当时的判断快照",过期不候。

事后对照(signal_outcomes.csv,evaluate_signals 生成):
  对每条已到期的信号,取发布日后 N 个交易日的价格,算个股前瞻收益,并与沪深300
  同窗口收益对照(超额=个股-基准)。**纯客观观察,简报从不给买卖建议**,所以这里
  记的是"发布以来价格走势(相对基准)",不是"我们的推荐赚没赚"。
  取不到价就如实记 unable,绝不编造。

数据源纪律(同 README §0):价格走新浪(stock_zh_a_daily / stock_zh_index_daily),
不走在本机不稳的东财。取不到→诚实降级,不臆造。

用法:
  python signal_tracker.py --evaluate         # 对所有到期信号做前瞻对照,写 outcomes
  python signal_tracker.py --evaluate --dryrun
  python signal_tracker.py --show             # 打印当前信号档案概览
(快照通常由 push_to_sheets.py 在发布简报时自动调用 snapshot_signal,无需手动。)
"""

import os
import json
import math
import datetime as dt

import pandas as pd

try:                                    # 与其它脚本一致:启动自动加载 .env(此处无强依赖)
    from _env import load_dotenv
    load_dotenv()
except Exception:
    pass


SIGNALS_CSV = "signals.csv"
OUTCOMES_CSV = "signal_outcomes.csv"
REALIZATION_CSV = "signal_realization.csv"

# 业绩类红旗关键词(可证伪子集):亏损型 vs 下滑型。其余红旗(监管/减持/大宗)不自动核验。
_LOSS_FLAGS = ("首亏", "续亏", "预亏")
_DECLINE_FLAGS = ("预减", "略减")

# 价值取向的对照窗口:季度 / 半年 / 一年(按交易日近似)。短线窗口刻意不设。
HORIZONS = {"1q": 63, "2q": 126, "1y": 252}
BENCHMARK = "sh000300"                  # 沪深300(新浪源)

SIGNAL_COLS = ["code", "name", "industry", "signal_date", "anchor_price",
               "pb", "roe_3y", "roic_3y", "implied_g", "major_flags", "lenses"]

LENSES_JSON = "factor_lenses.json"   # lens_screen.py 产;发布时据此冻结"当时镜头归属"


# ================================================================
# 工具:NaN 清洗 / 代码→新浪带前缀符号
# ================================================================
def _num(v):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _sina_symbol(code):
    """6位代码 → 新浪带市场前缀符号。6→沪;0/3→深;4/8/9(北交所/新三板)→bj。"""
    code = str(code).zfill(6)
    if code[0] == "6":
        return "sh" + code
    if code[0] in ("0", "3"):
        return "sz" + code
    return "bj" + code


# ================================================================
# 价格历史(新浪 qfq,进程内缓存)
# ================================================================
_PRICE_CACHE = {}


def _price_history(code, is_index=False):
    """返回 DataFrame[date(datetime.date), close],qfq。失败返回 None(诚实降级)。"""
    key = ("idx:" if is_index else "stk:") + str(code)
    if key in _PRICE_CACHE:
        v = _PRICE_CACHE[key]
        return None if isinstance(v, Exception) else v
    try:
        import akshare as ak
        if is_index:
            df = ak.stock_zh_index_daily(symbol=code)
        else:
            df = ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust="qfq")
        df = df[["date", "close"]].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        _PRICE_CACHE[key] = df
        return df
    except Exception as e:                 # 新浪源也取不到 → 缓存异常,不反复重试
        _PRICE_CACHE[key] = e
        return None


def _close_on_or_after(df, target):
    """目标日(含)之后的首个收盘价 + 其实际日期。够不到(数据未到该日)返回 (None, None)。"""
    if df is None or df.empty:
        return None, None
    hit = df[df["date"] >= target]
    if hit.empty:
        return None, None
    row = hit.iloc[0]
    return float(row["close"]), row["date"]


def _close_on_or_before(df, target):
    """目标日(含)之前的最后收盘价 + 其实际日期。用于取锚点价。"""
    if df is None or df.empty:
        return None, None
    hit = df[df["date"] <= target]
    if hit.empty:
        return None, None
    row = hit.iloc[-1]
    return float(row["close"]), row["date"]


# ================================================================
# 快照:发布简报时冻结当时判断
# ================================================================
def _read_signals():
    if not os.path.exists(SIGNALS_CSV):
        return pd.DataFrame(columns=SIGNAL_COLS)
    df = pd.read_csv(SIGNALS_CSV, dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)
    for c in SIGNAL_COLS:                      # 旧档案可能缺新列(如 lenses),补空保持兼容
        if c not in df.columns:
            df[c] = ""
    return df


def write_signals(records, signals_csv=SIGNALS_CSV):
    """把信号记录列表写回 signals.csv(列对齐 SIGNAL_COLS)。供从 Sheets 恢复本地档案。"""
    if not records:
        return 0
    df = pd.DataFrame(records)
    df["code"] = df["code"].astype(str).str.zfill(6)
    for c in SIGNAL_COLS:
        if c not in df.columns:
            df[c] = ""
    df[SIGNAL_COLS].to_csv(signals_csv, index=False, encoding="utf-8-sig")
    return len(df)


def _lens_membership(code, path=LENSES_JSON):
    """该 code 当前命中哪些价值镜头(读 factor_lenses.json)。返回逗号串,缺失则空。
    发布时调用 → 冻结"当时镜头归属",日后做按镜头的诚实成绩单,避免镜头会员事后漂移。"""
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            packs = json.load(f)
    except Exception:
        return ""
    code = str(code).zfill(6)
    hit = [name for name, pk in packs.items()
           if any(str(c.get("code")).zfill(6) == code for c in pk.get("candidates", []))]
    return ",".join(hit)


def snapshot_signal(rec, meta=None, signals_csv=SIGNALS_CSV):
    """在发布一条简报时调用:把当时的判断冻结进 signals.csv。
    rec  = push_to_sheets.prepare_report 产出的记录(含 code/name/industry/date/pb/roe/roic)。
    meta = agent_step8 的结构化 META(优先,含 implied_g)。
    幂等:同 (code, signal_date) 已存在则不重复写。anchor_price 取发布日收盘(新浪 qfq);
    取不到则留空(事后 evaluate 仍可用真实历史复原,不阻断)。返回 True=新写入。"""
    meta = meta or {}
    code = str(rec.get("code")).zfill(6)
    signal_date = str(rec.get("date") or dt.date.today().strftime("%Y-%m-%d"))

    df = _read_signals()
    if signals_csv == SIGNALS_CSV and not df.empty:
        dup = (df["code"] == code) & (df["signal_date"].astype(str) == signal_date)
        if dup.any():
            print(f"  [信号] {code}@{signal_date} 已存档,跳过")
            return False

    anchor, anchor_d = (None, None)
    hist = _price_history(code)
    if hist is not None:
        anchor, anchor_d = _close_on_or_before(hist, _parse_date(signal_date))
    if anchor is None:
        print(f"  [信号] {code} 锚点价暂取不到(发布日收盘),留空,事后复原")

    row = {
        "code": code,
        "name": rec.get("name"),
        "industry": rec.get("industry"),
        "signal_date": signal_date,
        "anchor_price": _num(anchor),
        "pb": _num(rec.get("pb")),
        "roe_3y": _num(rec.get("roe_3y")),
        "roic_3y": _num(rec.get("roic_3y")),
        "implied_g": _num(meta.get("implied_g")),
        "major_flags": (rec.get("major_flags") or
                        ", ".join(meta.get("major_flags") or []) or ""),
        "lenses": _lens_membership(code),       # 冻结当时命中的价值镜头(供按镜头成绩单)
    }
    new = pd.DataFrame([row])
    out = (new if df.empty else pd.concat([df, new], ignore_index=True))[SIGNAL_COLS]
    out.to_csv(signals_csv, index=False, encoding="utf-8-sig")
    print(f"  [信号] {code}@{signal_date} 已存档"
          f"(锚点价 {row['anchor_price']} / 隐含增速 {row['implied_g']})")
    return True


def _parse_date(s):
    return dt.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


# ================================================================
# 事后对照:前瞻价格表现(相对沪深300)
# ================================================================
def _ret(p0, p1):
    if not p0 or not p1 or p0 <= 0:
        return None
    return round((p1 / p0 - 1) * 100, 2)


def evaluate_signals(horizons=HORIZONS, dryrun=False, outcomes_csv=OUTCOMES_CSV):
    """对每条信号 × 每个到期窗口,算个股前瞻收益与沪深300同窗口收益(超额=个股-基准)。
    窗口未到期 → status=pending;价格取不到 → status=unable(诚实);算出 → completed。
    幂等:整表重算(信号档案是唯一真源),覆盖写 outcomes_csv。"""
    sig = _read_signals()
    if sig.empty:
        print("  无信号档案(signals.csv 为空),先发布几条简报再来。")
        return []

    bench = _price_history(BENCHMARK, is_index=True)
    if bench is None:
        print(f"  [警告] 基准 {BENCHMARK} 行情取不到,超额列将为空(不阻断个股收益)。")

    rows = []
    today = dt.date.today()
    for _, s in sig.iterrows():
        code = str(s["code"]).zfill(6)
        sdate = _parse_date(s["signal_date"])
        hist = _price_history(code)
        # 锚点价:优先档案里冻结的;缺失则用真实历史复原(发布日或之前最后一个收盘)
        p0, p0_d = (_num(s.get("anchor_price")), sdate)
        if p0 is None and hist is not None:
            p0, p0_d = _close_on_or_before(hist, sdate)
        b0, _ = _close_on_or_after(bench, sdate) if bench is not None else (None, None)

        for hname, ndays in horizons.items():
            target = sdate + dt.timedelta(days=int(ndays * 1.45))  # 交易日→自然日近似
            base = {"code": code, "name": s.get("name"),
                    "industry": s.get("industry"), "signal_date": s["signal_date"],
                    "horizon": hname}
            if target > today:
                rows.append({**base, "status": "pending", "as_of": str(target),
                             "stock_ret_pct": None, "bench_ret_pct": None,
                             "excess_pct": None})
                continue
            p1, p1_d = _close_on_or_after(hist, target) if hist is not None else (None, None)
            if p0 is None or p1 is None:
                rows.append({**base, "status": "unable",
                             "as_of": str(p1_d or target),
                             "stock_ret_pct": None, "bench_ret_pct": None,
                             "excess_pct": None})
                continue
            b1, _ = _close_on_or_after(bench, target) if bench is not None else (None, None)
            s_ret = _ret(p0, p1)
            b_ret = _ret(b0, b1)
            excess = (round(s_ret - b_ret, 2)
                      if (s_ret is not None and b_ret is not None) else None)
            rows.append({**base, "status": "completed", "as_of": str(p1_d),
                         "stock_ret_pct": s_ret, "bench_ret_pct": b_ret,
                         "excess_pct": excess})

    done = [r for r in rows if r["status"] == "completed"]
    pend = [r for r in rows if r["status"] == "pending"]
    unable = [r for r in rows if r["status"] == "unable"]
    print(f"  对照完成 {len(done)} / 未到期 {len(pend)} / 取价失败 {len(unable)} "
          f"(共 {len(rows)} 行,基于 {len(sig)} 条信号 × {len(horizons)} 窗口)")
    if not dryrun:
        pd.DataFrame(rows).to_csv(outcomes_csv, index=False, encoding="utf-8-sig")
        print(f"  → 已写 {outcomes_csv}")
    return rows


# ================================================================
# 给前端 data.js 用:信号档案 + 最新对照,合成一张可读表
# ================================================================
# ================================================================
# 假设兑现:新年报到来后,核对当时冻结的假设(隐含增速 / 业绩红旗)是否兑现
# ================================================================
def _parse_cn(x):
    """同花顺中文数字 → float:处理 亿/万 单位与 % 尾巴;'--'/空 → None。"""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        f = float(x)
        return None if (math.isnan(f) or math.isinf(f)) else f
    s = str(x).strip().replace(",", "")
    if s in ("", "--", "None", "nan", "NaN", "False"):
        return None
    s = s.rstrip("%")
    mult = 1.0
    if s.endswith("亿"):
        mult, s = 1e8, s[:-1]
    elif s.endswith("万"):
        mult, s = 1e4, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


_FIN_CACHE = {}


def _annual_financials(code):
    """同花顺年报摘要 → DataFrame[period(date), 营收, 净利润, 营收同比%, 净利润同比%]。
    只取年报(报告期 1231)。失败/无数据返回 None(诚实降级)。进程内缓存。"""
    code = str(code).zfill(6)
    if code in _FIN_CACHE:
        v = _FIN_CACHE[code]
        return None if isinstance(v, Exception) else v
    try:
        import akshare as ak
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        df["报告期"] = df["报告期"].astype(str)
        key = df["报告期"].str.replace(r"\D", "", regex=True)
        ann = df[key.str.endswith("1231")].copy()
        if ann.empty:
            raise ValueError("无年报期次")
        ann["period"] = pd.to_datetime(ann["报告期"]).dt.date
        out = pd.DataFrame({
            "period": ann["period"].values,
            "营收": ann["营业总收入"].map(_parse_cn).values,
            "净利润": ann["净利润"].map(_parse_cn).values,
            "营收同比": ann.get("营业总收入同比增长率", pd.Series()).map(_parse_cn).values
                       if "营业总收入同比增长率" in ann else None,
            "净利润同比": ann.get("净利润同比增长率", pd.Series()).map(_parse_cn).values
                        if "净利润同比增长率" in ann else None,
        }).sort_values("period").reset_index(drop=True)
        _FIN_CACHE[code] = out
        return out
    except Exception as e:
        _FIN_CACHE[code] = e
        return None


def _earnings_flag_check(flags_str, net_profit, profit_yoy):
    """对冻结的业绩类红旗,用实际年报核对兑现。返回 [{flag, expect, actual, verdict}]。
    亏损型(首亏/续亏/预亏)→ 看是否真亏(净利<0);下滑型(预减/略减)→ 看是否真降(同比<0)。
    其余红旗(监管/减持/大宗/ROIC失真)非业绩类,不自动核验,不列入。"""
    flags = [f.strip() for f in str(flags_str or "").replace(",", ",").split(",") if f.strip()]
    checks = []
    for f in flags:
        if any(k in f for k in _LOSS_FLAGS):
            actual_loss = (net_profit is not None and net_profit < 0)
            checks.append({"flag": f, "expect": "净利润为负",
                           "actual": ("亏损" if actual_loss else "未亏损") if net_profit is not None else "数据缺",
                           "verdict": "兑现" if actual_loss else ("未兑现" if net_profit is not None else "无法核")})
        elif any(k in f for k in _DECLINE_FLAGS):
            actual_decl = (profit_yoy is not None and profit_yoy < 0)
            checks.append({"flag": f, "expect": "净利润同比下滑",
                           "actual": (f"{profit_yoy:.1f}%" if profit_yoy is not None else "数据缺"),
                           "verdict": "兑现" if actual_decl else ("未兑现" if profit_yoy is not None else "无法核")})
    return checks


def evaluate_realization(dryrun=False, realization_csv=REALIZATION_CSV):
    """对每条信号:找发布日**之后**才结束的首个年报(零前视),核对当时冻结的假设是否兑现:
      · 隐含增速:实际净利润同比 vs 当时反向DCF 隐含增速(方向性核对,口径见 note)
      · 业绩红旗:亏损/下滑型红旗是否被实际年报证实
    无后续年报→pending;财务取不到→unable(诚实)。整表重算,覆盖写。"""
    sig = _read_signals()
    if sig.empty:
        print("  无信号档案,先发布几条简报再来。")
        return []

    rows = []
    for _, s in sig.iterrows():
        code = str(s["code"]).zfill(6)
        sdate = _parse_date(s["signal_date"])
        ig = _num(s.get("implied_g"))
        ig_pct = round(ig * 100, 1) if ig is not None else None
        base = {"code": code, "name": s.get("name"), "industry": s.get("industry"),
                "signal_date": s["signal_date"], "implied_g_pct": ig_pct,
                "frozen_flags": s.get("major_flags") or ""}

        fin = _annual_financials(code)
        if fin is None:
            rows.append({**base, "status": "unable", "forward_period": None,
                         "realized_rev_yoy_pct": None, "realized_profit_yoy_pct": None,
                         "net_profit_positive": None, "growth_verdict": None,
                         "flag_check": "", "note": "财务数据取不到(同花顺源)"})
            continue
        # 发布日严格之后结束的首个年报(保守:确保结果在发布时绝不可知,零前视)
        fwd = fin[fin["period"].apply(lambda d: d > sdate)]
        if fwd.empty:
            rows.append({**base, "status": "pending", "forward_period": None,
                         "realized_rev_yoy_pct": None, "realized_profit_yoy_pct": None,
                         "net_profit_positive": None, "growth_verdict": None,
                         "flag_check": "", "note": "发布后尚无新年报,待下个年报季"})
            continue
        r = fwd.iloc[0]
        rev_yoy = _num(r.get("营收同比"))
        profit_yoy = _num(r.get("净利润同比"))
        np_val = _num(r.get("净利润"))

        # 隐含增速兑现(方向性):实际净利润同比 vs 市场隐含增速
        if ig_pct is None or profit_yoy is None:
            gv = "不适用"
        elif profit_yoy < 0:
            gv = "落空(净利负增长,市场隐含增速未兑现)"
        elif profit_yoy >= ig_pct:
            gv = "达成/超越(实际增速≥市场隐含)"
        else:
            gv = "低于隐含(正增长但不及市场预期)"

        checks = _earnings_flag_check(base["frozen_flags"], np_val, profit_yoy)
        flag_str = "; ".join(f"{c['flag']}→{c['verdict']}" for c in checks)

        rows.append({**base, "status": "completed",
                     "forward_period": str(r["period"]),
                     "realized_rev_yoy_pct": rev_yoy,
                     "realized_profit_yoy_pct": profit_yoy,
                     "net_profit_positive": (None if np_val is None else bool(np_val >= 0)),
                     "growth_verdict": gv, "flag_check": flag_str,
                     "note": "净利润同比为单年口径,与反向DCF的多年FCF隐含增速仅作方向性对照"})

    done = [r for r in rows if r["status"] == "completed"]
    pend = [r for r in rows if r["status"] == "pending"]
    unable = [r for r in rows if r["status"] == "unable"]
    print(f"  假设兑现:已核 {len(done)} / 待新年报 {len(pend)} / 取数失败 {len(unable)} "
          f"(共 {len(rows)} 条信号)")
    if not dryrun:
        pd.DataFrame(rows).to_csv(realization_csv, index=False, encoding="utf-8-sig")
        print(f"  → 已写 {realization_csv}")
    return rows


def _agg(excesses):
    """一组超额 → {n, win_rate_pct, avg_excess_pct}。空则 None。"""
    xs = [x for x in excesses if x is not None]
    if not xs:
        return None
    wins = sum(1 for x in xs if x > 0)
    return {"n": len(xs),
            "win_rate_pct": round(wins / len(xs) * 100, 1),
            "avg_excess_pct": round(sum(xs) / len(xs), 2)}


def lens_scorecard(signals, outcomes):
    """按"发布时冻结的镜头归属"聚合前瞻超额 → 每个镜头的诚实成绩单。
    用冻结的 lenses 标签(非当前归属)避免事后会员漂移造成的前视偏差。
    返回 {lens_name: {overall:{...}, by_horizon:{hz:{...}}}}。"""
    # (code, signal_date) → 冻结的镜头标签列表
    tag = {}
    for s in signals:
        key = (str(s.get("code")).zfill(6), str(s.get("signal_date")))
        raw = s.get("lenses") or ""
        tag[key] = [t for t in str(raw).split(",") if t.strip()]

    # lens → list[(horizon, excess)],仅 completed
    bucket = {}
    for o in outcomes:
        if o.get("status") != "completed" or o.get("excess_pct") is None:
            continue
        key = (str(o.get("code")).zfill(6), str(o.get("signal_date")))
        for lz in tag.get(key, []):
            bucket.setdefault(lz, []).append((o.get("horizon"), o["excess_pct"]))

    out = {}
    for lz, pairs in bucket.items():
        overall = _agg([x for _, x in pairs])
        by_h = {}
        for hname in HORIZONS:
            agg = _agg([x for h, x in pairs if h == hname])
            if agg:
                by_h[hname] = agg
        out[lz] = {"overall": overall, "by_horizon": by_h}
    return out


def load_for_datajs():
    """返回 {signals, outcomes, summary, lens_scorecard} 供 push_to_sheets 注入 data.js。
    summary:已到期窗口的胜率(超额>0 占比)与平均超额——诚实成绩单,无数据则为空。
    lens_scorecard:按发布时冻结的镜头归属拆分的同口径成绩单。"""
    sig = _read_signals()
    signals = sig.where(pd.notna(sig), None).to_dict("records") if not sig.empty else []

    outcomes = []
    if os.path.exists(OUTCOMES_CSV):
        odf = pd.read_csv(OUTCOMES_CSV, dtype={"code": str})
        odf["code"] = odf["code"].str.zfill(6)
        outcomes = odf.where(pd.notna(odf), None).to_dict("records")

    comp = [o for o in outcomes if o.get("status") == "completed"
            and o.get("excess_pct") is not None]
    summary = {}
    if comp:
        agg = _agg([o["excess_pct"] for o in comp])
        summary = {
            "n_completed": agg["n"],
            "win_rate_pct": agg["win_rate_pct"],
            "avg_excess_pct": agg["avg_excess_pct"],
            "note": "胜率=相对沪深300超额为正的窗口占比;简报本身不给买卖建议,此为客观观察",
        }
    realization = []
    if os.path.exists(REALIZATION_CSV):
        rdf = pd.read_csv(REALIZATION_CSV, dtype={"code": str})
        rdf["code"] = rdf["code"].str.zfill(6)
        realization = rdf.where(pd.notna(rdf), None).to_dict("records")

    return {"signals": signals, "outcomes": outcomes, "summary": summary,
            "lens_scorecard": lens_scorecard(signals, outcomes),
            "realization": realization}


# ================================================================
# CLI
# ================================================================
if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    dryrun = "--dryrun" in args

    if "--evaluate" in args:
        evaluate_signals(dryrun=dryrun)
    elif "--realize" in args:
        evaluate_realization(dryrun=dryrun)
    elif "--show" in args:
        sig = _read_signals()
        print(f"信号档案 {len(sig)} 条(signals.csv):")
        if not sig.empty:
            print(sig.to_string(index=False))
        pack = load_for_datajs()
        if pack["summary"]:
            print("\n价格成绩单:", pack["summary"])
        if pack["lens_scorecard"]:
            print("按镜头:", {k: v["overall"] for k, v in pack["lens_scorecard"].items()})
        if pack["realization"]:
            done = [r for r in pack["realization"] if r["status"] == "completed"]
            print(f"假设兑现:{len(done)} 条已核(共 {len(pack['realization'])})")
    else:
        print("用法:")
        print("  python signal_tracker.py --evaluate     前瞻价格对照(沪深300超额),写 signal_outcomes.csv")
        print("  python signal_tracker.py --realize      假设兑现核对(隐含增速/业绩红旗),写 signal_realization.csv")
        print("  python signal_tracker.py --show         打印信号档案与成绩单概览")
        print("  (快照由 push_to_sheets 发布简报时自动调用,无需手动)")
