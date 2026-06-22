# -*- coding: utf-8 -*-
"""
母清单可信度体检 —— 从 factor_all_market_magic.csv 自动抽出"最该被质疑"的票
================================================================
不是给母清单打分,而是抽出几类典型,供你我用领域常识对照判断:
  A. 各行业榜首(综合分最优):它们该是"质优价合理",符合直觉吗?
  B. 排名靠前却带红旗:综合分进前列、却有现金流/营收/杠杆红旗 → 重点存疑
  C. 超低PB+高综合分:疑似价值陷阱(便宜但可能有隐忧),低PB是真便宜还是困境?
  D. 体检统计:覆盖多少行业/票、红旗分布、小行业占比 → 看整体是否健康

用法:python check_masterlist.py
输出抽样清单,把打印结果发我,一起判读。
"""

import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 240)

F = "factor_all_market_magic.csv"

df = pd.read_csv(F, dtype={"code": str})
df["code"] = df["code"].astype(str).str.zfill(6)
ranked = df[df["综合分"].notna()].copy()      # 只看进入排名的(正盈利)

print("=" * 70)
print("D. 整体体检")
print("=" * 70)
print(f"母清单总票数: {len(df)}")
print(f"  其中可排名(正盈利): {len(ranked)}")
print(f"  覆盖行业数: {df['行业'].nunique()}")
if "排名可信度" in df.columns:
    print(f"  排名可信度分布:\n{df['排名可信度'].value_counts().to_string()}")
flag_col = "红旗" if "红旗" in df.columns else None
if flag_col:
    has_flag = ranked[ranked[flag_col].fillna("").str.len() > 0]
    print(f"  可排名中带红旗的: {len(has_flag)} ({len(has_flag)/max(len(ranked),1)*100:.0f}%)")

# 每个行业取综合分最优的一只
print("\n" + "=" * 70)
print("A. 各行业榜首(综合分最优)—— 抽20个行业看是否'质优价合理'")
print("=" * 70)
tops = ranked.sort_values("综合分").groupby("行业").head(1)
cols = [c for c in ["name", "行业", "pb", "ROE_3y", "综合分", "CFQ_w", "负债率", flag_col] if c and c in ranked.columns]
print(tops.sort_values("综合分")[cols].head(20).to_string(index=False))

# 排名靠前(综合分在各行业前30%)却带红旗
print("\n" + "=" * 70)
print("B. 排名靠前却带红旗 —— 最该存疑的票")
print("=" * 70)
if flag_col:
    ranked["行业内分位"] = ranked.groupby("行业")["综合分"].rank(pct=True)
    suspect = ranked[(ranked["行业内分位"] <= 0.30) &
                     (ranked[flag_col].fillna("").str.len() > 0)]
    print(f"(共 {len(suspect)} 只,列前15)")
    print(suspect.sort_values("综合分")[cols].head(15).to_string(index=False))

# 超低PB + 高综合分:疑似价值陷阱
print("\n" + "=" * 70)
print("C. 超低PB(<0.7)但综合分靠前 —— 真便宜 or 价值陷阱?")
print("=" * 70)
trap = ranked[(pd.to_numeric(ranked["pb"], errors="coerce") < 0.7)]
trap = trap.sort_values("综合分")
print(f"(共 {len(trap)} 只低PB,按综合分列前15)")
print(trap[cols].head(15).to_string(index=False))

print("\n\n>>> 把以上四块打印结果发我。我们一起判读:")
print("    A 的榜首是不是你认可的好公司?B 的存疑票红旗合不合理?")
print("    C 的低PB是真便宜还是困境?D 的红旗占比/行业覆盖是否健康?")
print("    然后从中挑3-4只典型,用 agent 深挖对照。")
