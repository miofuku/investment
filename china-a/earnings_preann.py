#!/usr/bin/env python3
# ============================================================================
# earnings_preann.py — 业绩预告(前瞻红旗层)
# ----------------------------------------------------------------------------
# 系统里所有因子都是「向后看」的历史数据,唯一盲点是前瞻。业绩预告是补这个盲点
# 最干净、最高信噪比的免费数据:它是公司自己依规披露的强制预披露(预增/预减/
# 扭亏/首亏 + 区间),不是券商的乐观预测 —— 与「硬数字 + 排雷」的价值纪律一致。
# 而且只有业绩发生重大变动才需披露,天然只筛出大变动的票,正适合做红旗/催化层。
#
# 数据源:东方财富 ak.stock_yjyg_em(date=报告期),一次取全市场。
# 输出:earnings_preann.csv(code,name,ptype,pct,pdate,direction,summary)
#   direction: neg=前瞻红旗(首亏/续亏/预减/略减) · pos=催化(扭亏/预增/略增/续盈/减亏)
#
# push_to_sheets.prepare_masterlist 会按 code 合并:
#   · neg → 作为前瞻红旗并入母清单 flags(自动进「红旗异动」digest)
#   · 全部 → 附 preann_* 字段(正向的在结论里作催化提示)
#
# 用法:python3 earnings_preann.py            # 自动选最近有数据的报告期
#       python3 earnings_preann.py 20260331  # 指定报告期
# ============================================================================
import sys
from datetime import date

import akshare as ak
import pandas as pd

OUT = "earnings_preann.csv"

# 预告类型 → 方向(neg=前瞻红旗 / pos=催化 / neutral)
NEG = {"首亏", "续亏", "预减", "略减"}
POS = {"扭亏", "预增", "略增", "续盈", "减亏"}


def _recent_periods(n=5):
    """从今天往回推最近 n 个季度末(YYYYMMDD),最近在前。"""
    y, m = date.today().year, date.today().month
    # 当前所处季度的上一个已结束季度末起算
    ends = [(y, 3, 31), (y, 6, 30), (y, 9, 30), (y, 12, 31)]
    cands = []
    for yy in (y, y - 1, y - 2):
        for mm, dd in [(12, 31), (9, 30), (6, 30), (3, 31)]:
            cands.append(f"{yy}{mm:02d}{dd:02d}")
    today = date.today().strftime("%Y%m%d")
    return [c for c in sorted(cands, reverse=True) if c <= today][:n]


def _direction(ptype):
    if ptype in NEG:
        return "neg"
    if ptype in POS:
        return "pos"
    return "neutral"


def fetch(period=None):
    periods = [period] if period else _recent_periods()
    for p in periods:
        try:
            df = ak.stock_yjyg_em(date=p)
        except Exception as e:
            print(f"  {p}: 拉取失败 {repr(e)[:60]}")
            continue
        if df is not None and len(df):
            print(f"✓ 报告期 {p}:{len(df)} 行原始预告")
            return df, p
    return None, None


def run(period=None):
    df, p = fetch(period)
    if df is None:
        sys.exit("✗ 最近几个报告期都没取到业绩预告数据。")

    df = df.rename(columns={
        "股票代码": "code", "股票简称": "name", "预测指标": "metric",
        "预告类型": "ptype", "业绩变动幅度": "pct", "公告日期": "pdate",
        "业绩变动": "summary",
    })
    # 每只票取「归属于上市公司股东的净利润」口径,缺则退「净利润」
    pref = df[df["metric"] == "归属于上市公司股东的净利润"]
    fallback = df[df["metric"] == "净利润"]
    head = pd.concat([pref, fallback]).drop_duplicates(subset="code", keep="first")

    head = head.copy()
    head["code"] = head["code"].astype(str).str.zfill(6)
    head["direction"] = head["ptype"].map(_direction)
    head["summary"] = head["summary"].astype(str).str.slice(0, 80)
    head["report_period"] = p

    out = head[["code", "name", "ptype", "pct", "pdate",
                "direction", "summary", "report_period"]]
    out.to_csv(OUT, index=False, encoding="utf-8-sig")

    vc = out["direction"].value_counts().to_dict()
    print(f"✓ 写入 {OUT}:{len(out)} 只 "
          f"(前瞻红旗 neg={vc.get('neg',0)} / 催化 pos={vc.get('pos',0)} / "
          f"neutral={vc.get('neutral',0)})")
    print("  红旗类型分布:",
          out[out.direction == "neg"]["ptype"].value_counts().to_dict())


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
