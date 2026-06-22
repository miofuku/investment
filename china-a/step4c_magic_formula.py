# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 步骤4c:神奇公式式打分(便宜50% + 质量50%)
================================================================
相对 4b 的改动(依据用户取向:均衡/格林布拉特):
  · 综合分 = (便宜排名 + 质量排名)/2   ← 两支柱各半
        便宜 = PB(升序);质量 = 去杠杆ROE [ROE×(1−负债率)] ≈ ROA(降序)
        —— 用去杠杆ROE 而非原始ROE,避免靠高负债撑起的高ROE 被当真质量
           (正邦重整虚高ROE 即此类);全市场层拿不到 ROIC,用负债率做杠杆代理。
  · CFQ(缩尾[-1,3])、负债率、红旗 = 风险叠加层,只惩罚/标记,不加分
  · 入选仍只需 PB>0 + 3年ROE>0(正盈利门槛);排序用去杠杆ROE,改善覆盖率
  · 新增覆盖率诊断:看掉票到底是缺PB、缺ROE还是拉取失败

复用 4b 的缓存(sina_sector.csv / ths_quality_cache.csv),房地产秒出。
RUN_ALL=True 跑全市场(行业内分别打分)。

依赖:pip install akshare pandas --upgrade
"""

import os
import numpy as np
import pandas as pd

# 复用 4b 的数据获取函数(同目录)
from step4b_market_factors import (
    build_sector_table, pull_quality, parse_cn,
    UNIVERSE_IN,
)

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

RUN_ALL = True
SMOKE = False               # RUN_ALL=True时:True=只跑少数行业冒烟测试,确认无误再改False跑全市场
SMOKE_KEYWORDS = ["酒、饮料", "房地产", "林业", "渔业"]   # 大健康行业+承压行业+两个小行业,覆盖各代码路径
TARGET_KEYWORD = "房地产"
MIN_GROUP = 5
HIGH_DEBT = 70.0          # 资产负债率红旗阈值(%)

# 金融类行业:现金流/负债率排雷口径对它们不适用(参考江苏金租案例),
# 从母清单分流到 factor_financials.csv 备查,不参与神奇公式排名。
# (银行已在 step1 隔离;此处剔除新浪行业分类里的券商/信托/金租/AMC等)
FINANCIAL_INDUSTRIES = ["资本市场服务", "其他金融业", "货币金融服务", "租赁业", "保险业"]


def add_flags(df):
    """在缓存红旗基础上,补充'高杠杆'。风险层只标记,不加分。"""
    extra = []
    for _, r in df.iterrows():
        f = str(r.get("红旗", "")) if pd.notna(r.get("红旗", "")) else ""
        f = "" if f in ("nan", "None") else f
        parts = [p for p in f.split(",") if p]
        if pd.notna(r.get("负债率")) and r["负债率"] > HIGH_DEBT:
            parts.append("高杠杆")
        extra.append(",".join(dict.fromkeys(parts)))   # 去重保序
    df = df.copy()
    df["红旗"] = extra
    return df


def magic_rank(df):
    """神奇公式:便宜(PB)与质量各半。质量用『去杠杆ROE』(≈ROA)而非原始ROE,
    避免靠高负债撑起的高ROE 被当真质量(参考正邦重整虚高ROE)。"""
    d = df.copy()
    d["CFQ_w"] = pd.to_numeric(d["CFQ_3y"], errors="coerce").clip(-1, 3)

    # 去杠杆ROE:ROE × 权益占比 ≈ ROA = ROE×(1−负债率)。负债率缺失则不调整(×1)。
    # 权益占比封底0.1:负债率>90%(含资不抵债)时仍保留10%权重,重罚但不归零。
    roe = pd.to_numeric(d["ROE_3y"], errors="coerce")
    debt = pd.to_numeric(d["负债率"], errors="coerce")
    eq_w = (1 - debt / 100).clip(lower=0.1).where(debt.notna(), 1.0)
    d["ROE_adj"] = (roe * eq_w).round(2)

    # 入选资格:PB>0 且 3年ROE>0(格林布拉特要求正盈利;负回报=困境,非便宜)
    rankable = d[(pd.to_numeric(d["pb"], errors="coerce") > 0) &
                 (d["ROE_3y"] > 0)].copy()
    n = len(rankable)
    rankable["样本数"] = n
    if n >= 2:
        rankable["便宜排名"] = rankable["pb"].rank(ascending=True)
        rankable["质量排名"] = rankable["ROE_adj"].rank(ascending=False)   # 用去杠杆ROE排序
        rankable["综合分"] = (rankable["便宜排名"] + rankable["质量排名"]) / 2
        rankable["排名可信度"] = "正常" if n >= MIN_GROUP else "低(样本<5)"
        rankable = rankable.sort_values("综合分")
    else:
        rankable["综合分"] = np.nan
        rankable["排名可信度"] = "单票无可比" if n == 1 else "无合格样本"
    return add_flags(rankable)


def coverage_report(merged):
    n = len(merged)
    pb = pd.to_numeric(merged["pb"], errors="coerce")
    eligible = ((pb > 0) & (merged["ROE_3y"] > 0)).sum()
    neg_roe = (merged["ROE_3y"] <= 0).sum()
    bad_pb = ((pb <= 0) | pb.isna()).sum()
    na_roe = merged["ROE_3y"].isna().sum()
    err = merged["报告期"].astype(str).eq("ERR").sum() if "报告期" in merged else 0
    print(f"\n--- 覆盖率/资格诊断 ---")
    print(f"  样本 {n} | 合格可排名 {eligible} | 回报为负(排除) {neg_roe} "
          f"| 无效PB {bad_pb} | 缺ROE {na_roe} | ERR {err}")


def run_grouped(base, out_name):
    """全市场/子集:逐行业内做神奇公式排序;负盈利/无效PB 单独存档备查。"""
    q = pull_quality(base["code"].tolist())
    merged = base.merge(q, on="code", how="left")
    pb = pd.to_numeric(merged["pb"], errors="coerce")
    excl = merged[~((pb > 0) & (merged["ROE_3y"] > 0))].copy()      # 负盈利/无效PB
    parts = [magic_rank(g) for _, g in merged.groupby("行业")]
    allout = pd.concat(parts, ignore_index=True).sort_values(
        ["行业", "综合分"], na_position="last")
    allout.to_csv(out_name, index=False, encoding="utf-8-sig")
    excl_name = out_name.replace(".csv", "_excluded.csv")
    excl.to_csv(excl_name, index=False, encoding="utf-8-sig")

    ranked = allout[allout["综合分"].notna()]
    lowconf = (allout.get("排名可信度") == "低(样本<5)").sum() if "排名可信度" in allout else 0
    print(f"\n=== 完成:{out_name} ===")
    print(f"  行业 {merged['行业'].nunique()} 个 | 进母清单 {len(allout)} 只 "
          f"| 可排名(正盈利) {len(ranked)} 只 | 小行业低可信度 {lowconf} 只")
    print(f"  负盈利/无效PB 另存 {excl_name}:{len(excl)} 只")
    return allout


def main():
    uni = pd.read_csv(UNIVERSE_IN, dtype={"code": str})
    uni["code"] = uni["code"].str.zfill(6)
    sector = build_sector_table()
    base = uni.merge(sector[["code", "行业", "pb", "mktcap", "nmc", "trade"]],
                     on="code", how="inner")

    # 金融股分流:口径不适用,从母清单剔除并单独存档备查
    fin_mask = base["行业"].isin(FINANCIAL_INDUSTRIES)
    if fin_mask.any():
        base[fin_mask].to_csv("factor_financials.csv", index=False, encoding="utf-8-sig")
        print(f"金融股分流:{fin_mask.sum()} 只 → factor_financials.csv"
              f"(行业:{sorted(base[fin_mask]['行业'].unique().tolist())})")
        base = base[~fin_mask].copy()

    if RUN_ALL and SMOKE:
        mask = base["行业"].apply(lambda x: any(k in str(x) for k in SMOKE_KEYWORDS))
        sub = base[mask]
        print(f"=== 冒烟测试:{sub['行业'].nunique()} 个行业,{len(sub)} 只 ===")
        print(f"  行业:{sorted(sub['行业'].unique().tolist())}")
        run_grouped(sub, "factor_smoke.csv")
    elif RUN_ALL:
        run_grouped(base, "factor_all_market_magic.csv")
    else:
        target = base[base["行业"].str.contains(TARGET_KEYWORD, na=False)]
        print(f"验证行业:{target['行业'].unique().tolist()},{len(target)} 只")
        q = pull_quality(target["code"].tolist())     # 缓存命中→秒出
        merged = target.merge(q, on="code", how="left")
        ranked = magic_rank(merged)
        coverage_report(merged)
        cols = ["name", "pb", "ROE_3y", "ROE_adj", "便宜排名", "质量排名",
                "综合分", "CFQ_w", "负债率", "红旗"]
        cols = [c for c in cols if c in ranked.columns]
        print("\n=== 神奇公式排序(仅正盈利,综合分越小越好)===\n")
        print(ranked[cols].round(2).to_string(index=False))
        excl = merged[merged["ROE_3y"] <= 0].sort_values("ROE_3y")
        if len(excl):
            print(f"\n--- 排除:3年回报为负 {len(excl)} 只(重点回避),最差5只 ---")
            print(excl[["name", "pb", "ROE_3y", "负债率"]].head(5).round(2).to_string(index=False))
        ranked.to_csv(f"factor_{TARGET_KEYWORD}_magic.csv", index=False, encoding="utf-8-sig")
        print(f"\n已保存 factor_{TARGET_KEYWORD}_magic.csv")


if __name__ == "__main__":
    main()
