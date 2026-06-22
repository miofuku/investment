# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 步骤4b:全市场因子 + 行业内排序
================================================================
数据源(均非东财):
  · 行业 + PB + 市值  ← 新浪行业板块(stock_sector_spot/detail,约100次,横截面)
  · 3年质量因子        ← 同花顺财务摘要(逐只,带断点续传缓存)

因子(沿用 step3b 稳健口径):
  PB(新浪现成) | ROE_3y均 | CFQ_3y(3年现金流/3年利润) | 资产负债率
红旗:营收骤降 / 单年现金流负 / 现金流持续弱

用法:
  RUN_ALL=False → 只在 TARGET_KEYWORD 行业上验证(默认房地产)
  RUN_ALL=True  → 全市场(行业内分别排序),逐只缓存,可断点续传

输入:universe_normal.csv(step1)
缓存:sina_sector.csv(行业/PB) + ths_quality_cache.csv(质量因子,可续传)
输出:factor_<行业>.csv 或 factor_all_market.csv

依赖:pip install akshare pandas --upgrade
"""

import os
import re
import time
import random
import numpy as np
import pandas as pd
import akshare as ak

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

# ====== 开关 ======
RUN_ALL = False                 # False=只验证一个行业;True=全市场
TARGET_KEYWORD = "房地产"        # RUN_ALL=False 时,匹配板块名的关键词
N_YEARS = 3
MIN_GROUP = 5                   # 行业内排序的最小样本(小于此只列因子不排名)

UNIVERSE_IN = "universe_normal.csv"
SECTOR_CACHE = "sina_sector.csv"
QUALITY_CACHE = "ths_quality_cache.csv"


def robust(fn, *a, retries=3, base=2.0, label="", **kw):
    err = None
    for i in range(1, retries + 1):
        try:
            return fn(*a, **kw)
        except Exception as e:
            err = e
            if i == retries:
                break
            time.sleep(base * (2 ** (i - 1)) + random.uniform(0, 1))
    raise err


def parse_cn(x):
    if x is None:
        return np.nan
    if isinstance(x, (int, float, np.floating)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if s in ("False", "--", "", "None", "nan", "NaN"):
        return np.nan
    s = s.rstrip("%")
    mult = 1.0
    if s.endswith("亿"):
        mult, s = 1e8, s[:-1]
    elif s.endswith("万"):
        mult, s = 1e4, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return np.nan


# ----------------------------------------------------------------------
# 第一趟:新浪行业 → 全市场 code -> {行业, PB, 市值, 价格}
# ----------------------------------------------------------------------
def build_sector_table():
    if os.path.exists(SECTOR_CACHE):
        df = pd.read_csv(SECTOR_CACHE, dtype={"code": str})
        if "mktcap" in df.columns:                 # 旧缓存缺总市值则重建
            print(f"复用 {SECTOR_CACHE}")
            df["code"] = df["code"].str.zfill(6)
            return df
        print(f"{SECTOR_CACHE} 缺 mktcap 列,重建以补总市值...")

    print("拉取新浪行业列表...")
    spot = robust(ak.stock_sector_spot, indicator="行业", label="sector_spot")
    rows = []
    labels = spot["label"].tolist()
    names = spot["板块"].tolist()
    print(f"共 {len(labels)} 个行业,逐个取成分...")
    for i, (lab, nm) in enumerate(zip(labels, names), 1):
        try:
            d = robust(ak.stock_sector_detail, sector=str(lab), label=nm)
            d = d[["code", "name", "pb", "mktcap", "nmc", "trade"]].copy()
            d["行业"] = nm
            rows.append(d)
            print(f"  [{i}/{len(labels)}] {nm}: {len(d)} 只")
        except Exception as e:
            print(f"  [{i}/{len(labels)}] {nm} 失败: {type(e).__name__}")
        time.sleep(0.6)

    df = pd.concat(rows, ignore_index=True)
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["pb"] = pd.to_numeric(df["pb"], errors="coerce")
    df = df.drop_duplicates(subset="code")          # 一只票挂多行业,取首个
    df.to_csv(SECTOR_CACHE, index=False, encoding="utf-8-sig")
    print(f"已缓存 {SECTOR_CACHE}:{len(df)} 只")
    return df


# ----------------------------------------------------------------------
# 第二趟:同花顺逐只 → 3年质量因子(带缓存续传)
# ----------------------------------------------------------------------
def annual_series(code):
    df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
    df["报告期"] = df["报告期"].astype(str)
    key = df["报告期"].str.replace(r"\D", "", regex=True)
    ann = df[key.str.endswith("1231")].sort_values("报告期")
    if ann.empty:
        raise ValueError("无年报期次")
    return pd.DataFrame({
        "报告期": ann["报告期"].values,
        "营收": ann["营业总收入"].map(parse_cn).values,
        "EPS": ann["基本每股收益"].map(parse_cn).values,
        "CFPS": ann["每股经营现金流"].map(parse_cn).values,
        "ROE": ann["净资产收益率"].map(parse_cn).values,
        "负债率": ann["资产负债率"].map(parse_cn).values,
    }).tail(N_YEARS + 1).reset_index(drop=True)


def quality_of(code):
    s = annual_series(code)
    last_n = s.tail(N_YEARS)
    latest, prev = s.iloc[-1], (s.iloc[-2] if len(s) >= 2 else None)
    roe_avg = last_n["ROE"].mean()
    eps_sum = last_n["EPS"].sum()
    cfq = last_n["CFPS"].sum() / eps_sum if eps_sum > 0 else np.nan
    flags = []
    if prev is not None and prev["营收"] and prev["营收"] > 0:
        if latest["营收"] / prev["营收"] - 1 < -0.40:
            flags.append("营收骤降")
    if pd.notna(latest["CFPS"]) and latest["CFPS"] < 0:
        flags.append("单年现金流负")
    if pd.notna(cfq) and cfq < 0.5:
        flags.append("现金流持续弱")
    return {"报告期": latest["报告期"], "ROE_3y": roe_avg,
            "CFQ_3y": cfq, "负债率": latest["负债率"], "红旗": ",".join(flags)}


def pull_quality(codes):
    """带缓存续传:已在缓存里的跳过,新拉的逐只追加。"""
    cache = {}
    if os.path.exists(QUALITY_CACHE):
        c = pd.read_csv(QUALITY_CACHE, dtype={"code": str})
        c["code"] = c["code"].str.zfill(6)
        cache = {r["code"]: r.to_dict() for _, r in c.iterrows()}
        print(f"缓存命中 {len(cache)} 只")

    todo = [c for c in codes if c not in cache]
    print(f"需拉取 {len(todo)} 只(共 {len(codes)})")
    for i, code in enumerate(todo, 1):
        try:
            q = quality_of(code)
            q["code"] = code
            cache[code] = q
            tag = "✓"
        except Exception as e:
            cache[code] = {"code": code, "报告期": "ERR", "ROE_3y": np.nan,
                           "CFQ_3y": np.nan, "负债率": np.nan,
                           "红旗": f"拉取失败:{type(e).__name__}"}
            tag = "✗"
        if i % 10 == 0 or i == len(todo):
            pd.DataFrame(cache.values()).to_csv(
                QUALITY_CACHE, index=False, encoding="utf-8-sig")   # 落盘=可续传
            print(f"  {tag} [{i}/{len(todo)}] {code}  (已落盘)")
        time.sleep(1.1)

    pd.DataFrame(cache.values()).to_csv(QUALITY_CACHE, index=False, encoding="utf-8-sig")
    return pd.DataFrame(cache.values())


# ----------------------------------------------------------------------
# 行业内排序
# ----------------------------------------------------------------------
def rank_within(df):
    d = df.dropna(subset=["pb", "ROE_3y", "CFQ_3y", "负债率"]).copy()
    if len(d) < MIN_GROUP:
        d["综合分"] = np.nan
        return d
    d["r_PB"] = d["pb"].rank(ascending=True)
    d["r_ROE"] = d["ROE_3y"].rank(ascending=False)
    d["r_CFQ"] = d["CFQ_3y"].rank(ascending=False)
    d["r_DEBT"] = d["负债率"].rank(ascending=True)
    d["综合分"] = d[["r_PB", "r_ROE", "r_CFQ", "r_DEBT"]].mean(axis=1)
    return d.sort_values("综合分")


def main():
    uni = pd.read_csv(UNIVERSE_IN, dtype={"code": str})
    uni["code"] = uni["code"].str.zfill(6)

    sector = build_sector_table()
    base = uni.merge(sector[["code", "行业", "pb", "nmc", "trade"]], on="code", how="inner")
    print(f"\n普通池∩新浪行业:{len(base)} 只,覆盖 {base['行业'].nunique()} 个行业")

    if not RUN_ALL:
        mask = base["行业"].str.contains(TARGET_KEYWORD, na=False)
        target = base[mask]
        inds = target["行业"].unique().tolist()
        print(f"\n验证行业(含'{TARGET_KEYWORD}'):{inds},共 {len(target)} 只")
        q = pull_quality(target["code"].tolist())
        merged = target.merge(q, on="code", how="left")
        out = rank_within(merged)
        cols = ["name", "行业", "pb", "ROE_3y", "CFQ_3y", "负债率", "综合分", "红旗"]
        cols = [c for c in cols if c in out.columns]
        print("\n=== 行业内排序 ===\n")
        print(out[cols].round(2).to_string(index=False))
        out.to_csv(f"factor_{TARGET_KEYWORD}.csv", index=False, encoding="utf-8-sig")
        print(f"\n已保存 factor_{TARGET_KEYWORD}.csv")
    else:
        q = pull_quality(base["code"].tolist())
        merged = base.merge(q, on="code", how="left")
        parts = [rank_within(g) for _, g in merged.groupby("行业")]
        allout = pd.concat(parts, ignore_index=True)
        allout.to_csv("factor_all_market.csv", index=False, encoding="utf-8-sig")
        print(f"\n全市场完成,已保存 factor_all_market.csv:{len(allout)} 只")


if __name__ == "__main__":
    main()
