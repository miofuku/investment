# -*- coding: utf-8 -*-
"""
lens_screen.py — 价值镜头(声明式筛选)
================================================================
把"价值投资视角"做成 lenses/*.yaml 声明式配置:加一种新视角只写 YAML、不写代码。
思路借鉴 daily_stock_analysis 的"策略即文件",但严守本系统纪律:
  · **算术全在确定性 Python 里**(本引擎),YAML 只声明阈值与排序,不含任何计算逻辑。
  · 字段必须是母清单真实列或本引擎显式派生列;写错字段**当场报错**,绝不静默放过。
  · 只做广度缩小、产候选清单,**不下买卖结论**;真伪(分红/ROIC/三表排雷)留给 agent 深挖。

输入:factor_all_market_magic.csv(母清单,step4c 产)
镜头:lenses/*.yaml(`_` 开头的为配置非镜头,如 _groups.yaml)
输出:factor_lens_<name>.csv(每镜头一份候选)+ factor_lenses.json(合并,供前端/复用)

用法:
  python lens_screen.py                # 跑全部镜头,打印 + 落盘
  python lens_screen.py deep_value     # 只跑一个
  python lens_screen.py --list         # 列出可用镜头
"""

import os
import sys
import json
import glob

import numpy as np
import pandas as pd
import yaml

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 240)

MASTERLIST = "factor_all_market_magic.csv"
LENS_DIR = "lenses"
GROUPS_FILE = os.path.join(LENS_DIR, "_groups.yaml")

# 母清单真实列(step4c 产)。镜头 filters/rank_by 只能引用这些 + 下方 DERIVED。
BASE_FIELDS = {"code", "name", "行业", "pb", "ROE_3y", "ROE_adj", "综合分",
               "便宜排名", "质量排名", "CFQ_w", "负债率", "红旗", "排名可信度"}
# 引擎派生列(算术在此,YAML 只引用名字)
DERIVED_FIELDS = {"flag_count", "roe_leverage_ratio"}
ALLOWED_FIELDS = BASE_FIELDS | DERIVED_FIELDS

# 与 screen_traditional.py 口径一致的"重红旗"
SEVERE_FLAGS = ["现金流持续弱", "营收骤降"]

OPS = {
    "ge": lambda s, v: s >= v,
    "le": lambda s, v: s <= v,
    "gt": lambda s, v: s > v,
    "lt": lambda s, v: s < v,
    "eq": lambda s, v: s == v,
    "ne": lambda s, v: s != v,
}

SHOW_COLS = ["code", "name", "行业", "pb", "ROE_3y", "ROE_adj",
             "roe_leverage_ratio", "综合分", "CFQ_w", "负债率", "红旗", "排名可信度"]


# ================================================================
# 加载
# ================================================================
def load_groups():
    if not os.path.exists(GROUPS_FILE):
        return {}
    with open(GROUPS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_lenses():
    """读 lenses/*.yaml(跳过 _ 开头)。返回 {name: lens_dict}。"""
    out = {}
    for path in sorted(glob.glob(os.path.join(LENS_DIR, "*.yaml"))):
        if os.path.basename(path).startswith("_"):
            continue
        with open(path, encoding="utf-8") as f:
            lens = yaml.safe_load(f)
        if not lens or "name" not in lens:
            print(f"  [跳过] {path}:缺 name 字段")
            continue
        lens["_path"] = path
        out[lens["name"]] = lens
    return out


def _add_derived(df):
    """派生列(算术在代码里,YAML 只引用):
    flag_count        红旗个数
    roe_leverage_ratio 去杠杆ROE / 原始ROE ≈ 权益占比;越接近1 回报越非靠杠杆,越低越是杠杆撑的。"""
    d = df.copy()
    d["flag_count"] = d["红旗"].fillna("").astype(str).apply(
        lambda s: 0 if not s.strip() else len([x for x in s.split(",") if x.strip()]))
    roe = pd.to_numeric(d["ROE_3y"], errors="coerce")
    roe_adj = pd.to_numeric(d["ROE_adj"], errors="coerce")
    # 仅在 ROE>0 时有意义(母清单已要求正盈利);否则置 NaN,filter 会自然排除
    d["roe_leverage_ratio"] = np.where(roe > 0, (roe_adj / roe).round(3), np.nan)
    return d


# ================================================================
# 应用单个镜头
# ================================================================
def _validate_lens(lens):
    name = lens.get("name", "?")
    for flt in lens.get("filters", []):
        fld, op = flt.get("field"), flt.get("op")
        if fld not in ALLOWED_FIELDS:
            raise ValueError(f"镜头『{name}』filters 引用了未知字段『{fld}』。"
                             f"可用:{sorted(ALLOWED_FIELDS)}")
        if op not in OPS:
            raise ValueError(f"镜头『{name}』filters 用了未知操作符『{op}』。可用:{sorted(OPS)}")
    rb = lens.get("rank_by")
    if rb and rb not in ALLOWED_FIELDS:
        raise ValueError(f"镜头『{name}』rank_by 引用了未知字段『{rb}』。可用:{sorted(ALLOWED_FIELDS)}")
    ef = lens.get("exclude_flags")
    if ef not in (None, "none", "any", "severe") and not isinstance(ef, list):
        raise ValueError(f"镜头『{name}』exclude_flags 取值非法『{ef}』:"
                         f"应为 none/any/severe 或关键词列表")


def apply_lens(df, lens, groups):
    """对母清单应用一个镜头,返回候选 DataFrame(已排序、截 top_n)。"""
    _validate_lens(lens)
    d = df

    # 行业组限定
    grp = lens.get("industry_group")
    if grp:
        if grp not in groups:
            raise ValueError(f"镜头『{lens['name']}』引用未知行业组『{grp}』,"
                             f"_groups.yaml 里有:{sorted(groups)}")
        d = d[d["行业"].isin(groups[grp])]

    # 只在可排名(正盈利→综合分非空)的票里筛
    d = d[d["综合分"].notna()]

    # filters(全部 AND)
    for flt in lens.get("filters", []):
        col = pd.to_numeric(d[flt["field"]], errors="coerce")
        mask = OPS[flt["op"]](col, flt["value"])
        d = d[mask.fillna(False)]   # 阈值比较中 NaN → 不通过(诚实:算不出不放行)

    # 红旗过滤
    ef = lens.get("exclude_flags")
    flags = d["红旗"].fillna("").astype(str)
    if ef == "any":
        d = d[d["flag_count"] == 0]
    elif ef == "severe":
        d = d[~flags.apply(lambda s: any(k in s for k in SEVERE_FLAGS))]
    elif isinstance(ef, list):
        d = d[~flags.apply(lambda s: any(k in s for k in ef))]

    # 排序 + 截断
    rb = lens.get("rank_by", "综合分")
    asc = bool(lens.get("ascending", True))
    d = d.sort_values(rb, ascending=asc, na_position="last")
    top_n = lens.get("top_n")
    if top_n:
        d = d.head(int(top_n))
    return d


# ================================================================
# 输出
# ================================================================
def _records(df):
    cols = [c for c in SHOW_COLS if c in df.columns]
    out = df[cols].copy()
    return out.where(pd.notna(out), None).to_dict("records")


def run(only=None):
    if not os.path.exists(MASTERLIST):
        print(f"✗ 未找到母清单 {MASTERLIST}。先跑 step4c_magic_formula.py 生成。")
        sys.exit(1)

    df = pd.read_csv(MASTERLIST, dtype={"code": str})
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = _add_derived(df)

    groups = load_groups()
    lenses = load_lenses()
    if only:
        if only not in lenses:
            print(f"✗ 无镜头『{only}』。可用:{sorted(lenses)}")
            sys.exit(1)
        lenses = {only: lenses[only]}

    combined = {}
    for name, lens in lenses.items():
        cand = apply_lens(df, lens, groups)
        recs = _records(cand)
        combined[name] = {
            "display_name": lens.get("display_name", name),
            "description": lens.get("description", ""),
            "count": len(recs),
            "candidates": recs,
        }
        out_csv = f"factor_lens_{name}.csv"
        cand.to_csv(out_csv, index=False, encoding="utf-8-sig")

        print("=" * 78)
        print(f"【{lens.get('display_name', name)}】({name})  命中 {len(recs)} 只  → {out_csv}")
        print(f"  {lens.get('description','')}")
        print("=" * 78)
        if recs:
            show = cand[[c for c in SHOW_COLS if c in cand.columns]].head(20)
            print(show.round(3).to_string(index=False))
        else:
            print("  (无命中:阈值偏严或母清单尚未覆盖该类)")
        print()

    with open("factor_lenses.json", "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False)
    print(f"→ 已写 factor_lenses.json(合并 {len(combined)} 个镜头,供前端/复用)")

    # 深挖命令(取并集前8,沿用 screen_traditional 的 UX)
    union = []
    seen = set()
    for v in combined.values():
        for r in v["candidates"]:
            if r["code"] not in seen:
                seen.add(r["code"])
                union.append(r)
    if union:
        print("\n下一步:用完整 agent 逐只深挖(分红/ROIC/三表排雷都在 agent 里)。复制运行:")
        for r in union[:8]:
            print(f"python agent_step8_block_trade.py {r['code']}    # {r.get('name','')} ({r.get('行业','')})")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        for name, lens in load_lenses().items():
            print(f"  {name:20s} {lens.get('display_name','')} — {lens.get('description','')}")
    else:
        run(only=args[0] if args and not args[0].startswith("-") else None)
