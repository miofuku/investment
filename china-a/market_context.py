# -*- coding: utf-8 -*-
"""
market_context.py — 全市场估值温度计(给"便宜"一个坐标系)
================================================================
单只票"便宜"是相对行业的;但整个市场本身可能正贵或正便宜。本模块产一个**客观**的
全A股估值分位:PE/PB 的中位数 + 它在近10年历史里的百分位,让"这只票便宜"多一层
"而且现在整个市场也不贵/已经很贵"的背景判断。

数据源:乐咕乐股(legulegu,经 akshare),**非东财**;自带历史分位,确定性、可查证。
  · stock_a_ttm_lyr  全A滚动市盈率(中位数)+ 历史/近10年分位
  · stock_a_all_pb   全A市净率(中位数)+ 历史/近10年分位
取不到→诚实返回空,不编造。

纪律:只报客观分位,不喊"该抄底/该清仓";让数据决定信什么,判断留给人。

用法:
  python market_context.py          # 计算并写 market_context.json,打印概览
  python market_context.py --show   # 只打印现有 market_context.json
"""

import os
import json
import math
import datetime as dt

import pandas as pd

OUT_JSON = "market_context.json"


def _num(v, nd=2):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, nd)
    except (TypeError, ValueError):
        return None


def _last_valid(df, value_col, q_col):
    """取最后一个 分位(q_col)非空 的行(最新一两天的分位常未算出)→ (date, value, pctile%)。"""
    sub = df.dropna(subset=[q_col])
    if sub.empty:
        return None, None, None
    r = sub.iloc[-1]
    pct = _num(r[q_col], 4)
    return (str(r.get("date") or r.get("日期"))[:10],
            _num(r.get(value_col)),
            round(pct * 100, 1) if pct is not None else None)


def compute(write=True, out_json=OUT_JSON):
    """拉全A PE/PB 中位数及近10年分位,产 market_context.json。失败的指标留空,不阻断另一个。"""
    import akshare as ak
    ctx = {"as_of": dt.date.today().strftime("%Y-%m-%d")}

    try:
        pe = ak.stock_a_ttm_lyr()
        d, val, pct = _last_valid(pe, "middlePETTM", "quantileInRecent10YearsMiddlePeTtm")
        ctx["pe_ttm_median"] = val
        ctx["pe_pctile_10y"] = pct
        ctx["pe_as_of"] = d
    except Exception as e:
        print(f"  [估值温度计] 全A PE 取不到:{type(e).__name__}: {e}")
        ctx["pe_ttm_median"] = ctx["pe_pctile_10y"] = None

    try:
        pb = ak.stock_a_all_pb()
        d, val, pct = _last_valid(pb, "middlePB", "quantileInRecent10YearsMiddlePB")
        ctx["pb_median"] = val
        ctx["pb_pctile_10y"] = pct
        ctx["pb_as_of"] = d
    except Exception as e:
        print(f"  [估值温度计] 全A PB 取不到:{type(e).__name__}: {e}")
        ctx["pb_median"] = ctx["pb_pctile_10y"] = None

    ctx["note"] = ("全A股中位数 PE/PB 及其近10年历史百分位(分位越低=整体越便宜)。"
                   "客观参照,不构成买卖建议。")

    if write:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(ctx, f, ensure_ascii=False)
        print(f"  → 已写 {out_json}")
    return ctx


def load(out_json=OUT_JSON):
    """读 market_context.json 供 data.js。缺失/异常返回空 dict。"""
    if not os.path.exists(out_json):
        return {}
    try:
        with open(out_json, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _fmt(ctx):
    def line(name, val, pct):
        if val is None:
            return f"  {name}: 取不到"
        p = f"{pct}% 分位(近10年)" if pct is not None else "分位未知"
        return f"  {name} 中位数 {val} · {p}"
    return ("全A估值温度计 @ " + ctx.get("as_of", "?") + "\n"
            + line("PE(TTM)", ctx.get("pe_ttm_median"), ctx.get("pe_pctile_10y")) + "\n"
            + line("PB", ctx.get("pb_median"), ctx.get("pb_pctile_10y")))


if __name__ == "__main__":
    import sys
    if "--show" in sys.argv[1:]:
        print(_fmt(load()))
    else:
        print(_fmt(compute()))
