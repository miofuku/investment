#!/usr/bin/env python3
# ============================================================================
# bank_scorecard.py — 金融股(银行/券商/保险)备查评分卡
# ----------------------------------------------------------------------------
# 背景:主因子管线(负债率/现金流/ROIC/反向DCF)对金融股不适用,所以银行等被
#       step1 按名称隔离进 universe_financials.csv,从不算因子 → 看板里净资产
#       收益率一直是空的。本脚本为这批票补上一组「适用于金融股」的可靠指标。
#
# 数据源:东方财富财务摘要 ak.stock_financial_abstract(一次调用拿全量指标)。
#   能可靠取到:净资产收益率(ROE)、总资产净利率(银行口径的 ROA)、资产负债率、
#             归母净利润增速。
#   取不到:不良贷款率 / 拨备覆盖率 / 资本充足率 / 净息差 —— akshare 的免费财务
#           接口不含这些银行监管指标(需另接数据源或解析年报),本脚本不涉及。
#
# 输出:bank_scorecard.csv(code,name,industry,roe_3y,roe_latest,roa,
#       debt_ratio,profit_growth)。push_to_sheets.prepare_financials 会按 code
#       合并它,使金融股在看板「金融股(备查)」页显示净资产收益率等。
#
# 用法:
#   python3 bank_scorecard.py            # 跑 universe_financials.csv 全部金融股
#   python3 bank_scorecard.py --limit 5  # 只跑前 5 只(调试)
#   python3 bank_scorecard.py --only 银行 # 只跑某 sector
# ============================================================================
import argparse
import os
import sys
import time

import akshare as ak
import pandas as pd

UNIVERSE = "universe_financials.csv"   # step1 按名称隔离的银行/券商/保险
FACTORS = "factor_financials.csv"      # step4c 按行业分流的金融股(资本市场服务/租赁业等)
OUT = "bank_scorecard.csv"


def _annual_cols(df):
    """财务摘要里挑出年报列(YYYY1231),最新在前。"""
    cols = [c for c in df.columns if str(c).isdigit() and str(c).endswith("1231")]
    return sorted(cols, reverse=True)


def _row_values(df, indicator, cols):
    """取某指标在给定年份列上的数值(转 float,缺失为 NaN)。同名指标取第一行。"""
    m = df[df["指标"] == indicator]
    if not len(m):
        return [float("nan")] * len(cols)
    r = m.iloc[0]
    return [pd.to_numeric(r.get(c), errors="coerce") for c in cols]


def _avg(vals):
    s = [v for v in vals if pd.notna(v)]
    return round(sum(s) / len(s), 2) if s else None


def _first(vals):
    for v in vals:
        if pd.notna(v):
            return round(float(v), 2)
    return None


def fetch_one(code):
    """返回一只金融股的评分卡 dict,失败返回 None。"""
    df = ak.stock_financial_abstract(symbol=code)
    cols = _annual_cols(df)[:3]
    if not cols:
        return None
    roe = _row_values(df, "净资产收益率(ROE)", cols)
    roa = _row_values(df, "总资产净利率_平均", cols)
    debt = _row_values(df, "资产负债率", cols)
    growth = _row_values(df, "归属母公司净利润增长率", cols)
    return {
        "code": code,
        "roe_3y": _avg(roe),          # 近三年净资产收益率均值
        "roe_latest": _first(roe),    # 最新年度净资产收益率
        "roa": _first(roa),           # 总资产净利率(银行口径 ROA)
        "debt_ratio": _first(debt),   # 资产负债率
        "profit_growth": _first(growth),  # 归母净利润增速
    }


def _load_universe():
    """金融股代码清单 = step1 名称隔离 ∪ step4c 行业分流,按 code 去重。"""
    frames = []
    if os.path.exists(UNIVERSE):
        u = pd.read_csv(UNIVERSE, dtype={"code": str})
        frames.append(u[["code", "name", "sector"]])
    if os.path.exists(FACTORS):
        f = pd.read_csv(FACTORS, dtype={"code": str})
        f = f.rename(columns={"行业": "sector"})
        cols = [c for c in ["code", "name", "sector"] if c in f.columns]
        frames.append(f[cols])
    if not frames:
        return None
    uni = pd.concat(frames, ignore_index=True)
    uni["code"] = uni["code"].str.zfill(6)
    return uni.drop_duplicates(subset="code", keep="first").reset_index(drop=True)


def run(limit=None, only=None):
    uni = _load_universe()
    if uni is None:
        sys.exit(f"✗ 找不到 {UNIVERSE} / {FACTORS},先跑 step1 / step4c 生成金融股清单。")
    if only:
        uni = uni[uni["sector"] == only]
    if limit:
        uni = uni.head(limit)

    rows, fail = [], []
    total = len(uni)
    for i, rec in enumerate(uni.itertuples(index=False), 1):
        code, name = rec.code, rec.name
        try:
            sc = fetch_one(code)
            if sc is None:
                fail.append(code)
                print(f"  [{i}/{total}] {code} {name}  ✗ 无年报数据")
            else:
                sc["name"] = name
                sc["industry"] = rec.sector
                rows.append(sc)
                print(f"  [{i}/{total}] {code} {name}  净资产收益率3年={sc['roe_3y']} "
                      f"ROA={sc['roa']} 增速={sc['profit_growth']}")
        except Exception as e:  # 网络/接口偶发失败,跳过不中断
            fail.append(code)
            print(f"  [{i}/{total}] {code} {name}  ✗ {repr(e)[:70]}")
        time.sleep(0.6)  # 限速,避免被东财封

    if not rows:
        sys.exit("✗ 一条都没取到,检查网络或接口。")
    out = pd.DataFrame(rows)[
        ["code", "name", "industry", "roe_3y", "roe_latest",
         "roa", "debt_ratio", "profit_growth"]
    ]
    out.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n✓ 写入 {OUT}:{len(out)} 只金融股("
          f"成功 {len(rows)} / 失败 {len(fail)})。")
    if fail:
        print("  失败代码:", " ".join(fail))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="只跑某 sector,如 银行")
    args = ap.parse_args()
    run(limit=args.limit, only=args.only)
