# -*- coding: utf-8 -*-
"""
传统行业选股 —— 从干净母清单 factor_all_market_magic.csv 圈候选
================================================================
目标(用户取向):传统行业里找"低估值 + 活得久"的票。
两种筛法并出,供对照:
  A 偏便宜:先按估值便宜,只剔除明显价值陷阱(深度负现金流/持续弱)
  B 偏稳健:先卡"活得久"硬指标(正现金流+低负债+无红旗+稳定盈利),再挑相对便宜
  交集(A∩B)= 又便宜又稳 = 最甜的点

注意:分红/ROIC/三表排雷不在母清单,本脚本只做广度缩小;
     真正的"活得久"验证(尤其分红)留给 agent 逐只深挖。

用法:python screen_traditional.py
输出:factor_trad_value.csv(A)/ factor_trad_stable.csv(B),打印两表+交集+深挖命令
"""

import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 240)

F = "factor_all_market_magic.csv"

# ===== 传统行业清单(可编辑:嫌哪个不算"传统"就删)=====
TRADITIONAL = {
    # 公用事业
    "电力、热力生产和供应业", "燃气生产和供应业", "水的生产和供应业",
    # 资源/周期
    "煤炭开采和洗选业", "石油加工、炼焦和核燃料加工业", "石油和天然气开采业",
    "黑色金属冶炼和压延加工业", "有色金属冶炼和压延加工业",
    "有色金属矿采选业", "黑色金属矿采选业", "非金属矿采选业", "开采辅助活动",
    # 材料
    "非金属矿物制品业", "化学原料和化学制品制造业", "化学纤维制造业",
    "造纸和纸制品业", "橡胶和塑料制品业", "金属制品业",
    # 交运/基建
    "铁路运输业", "道路运输业", "水上运输业", "航空运输业",
    "装卸搬运和运输代理业", "仓储业", "邮政业",
    # 建筑
    "土木工程建筑业", "房屋建筑业", "建筑安装业", "建筑装饰和其他建筑业",
    # 环保/公用
    "生态保护和环境治理业", "公共设施管理业", "水利管理业",
}

# ===== 阈值(可调)=====
TOPN = 30
A_CFQ_MIN = -0.5         # A:现金流质量不能深度为负(剔最差陷阱)
A_DEBT_MAX = 78.0
B_CFQ_MIN = 0.3          # B:正现金流
B_DEBT_MAX = 60.0        # B:低负债=活得久
B_ROE_MIN = 6.0          # B:稳定盈利底线
SEVERE = ["现金流持续弱", "营收骤降"]   # 视为较重的红旗


def flag_count(s):
    s = "" if pd.isna(s) else str(s)
    return 0 if not s.strip() else len([x for x in s.split(",") if x.strip()])


def has_severe(s):
    s = "" if pd.isna(s) else str(s)
    return any(k in s for k in SEVERE)


def show(df, cols, n=20):
    cols = [c for c in cols if c in df.columns]
    print(df[cols].head(n).to_string(index=False))


df = pd.read_csv(F, dtype={"code": str})
df["code"] = df["code"].astype(str).str.zfill(6)
df["pb"] = pd.to_numeric(df["pb"], errors="coerce")

# 限定传统行业 + 可排名(正盈利)
trad = df[df["行业"].isin(TRADITIONAL) & df["综合分"].notna()].copy()
trad["红旗数"] = trad["红旗"].apply(flag_count)
trad["重红旗"] = trad["红旗"].apply(has_severe)
print(f"传统行业可排名票数:{len(trad)}(覆盖 {trad['行业'].nunique()} 个行业)\n")

COLS = ["code", "name", "行业", "pb", "ROE_3y", "综合分", "CFQ_w", "负债率", "红旗", "排名可信度"]

# ---------- A 偏便宜:剔明显陷阱后,按综合分(行业内便宜+质量)取前N ----------
A = trad[(trad["CFQ_w"] >= A_CFQ_MIN) &
         (trad["负债率"] <= A_DEBT_MAX) &
         (~trad["重红旗"])].copy()
A = A.sort_values("综合分").head(TOPN)
A.to_csv("factor_trad_value.csv", index=False, encoding="utf-8-sig")
print("=" * 72)
print(f"【A 偏便宜】剔除深度负现金流/持续弱后,按综合分取前{TOPN}(综合分越小越好)")
print("=" * 72)
show(A, COLS)

# ---------- B 偏稳健:卡"活得久"硬指标,再按综合分取前N ----------
B = trad[(trad["CFQ_w"] >= B_CFQ_MIN) &
         (trad["负债率"] <= B_DEBT_MAX) &
         (trad["ROE_3y"] >= B_ROE_MIN) &
         (trad["红旗数"] == 0)].copy()
B = B.sort_values("综合分").head(TOPN)
B.to_csv("factor_trad_stable.csv", index=False, encoding="utf-8-sig")
print("\n" + "=" * 72)
print(f"【B 偏稳健】活得久门槛(CFQ≥{B_CFQ_MIN}/负债≤{B_DEBT_MAX}/ROE≥{B_ROE_MIN}/零红旗)后,按综合分取前{TOPN}")
print("=" * 72)
show(B, COLS)

# ---------- 交集:又便宜又稳 ----------
overlap = sorted(set(A["code"]) & set(B["code"]))
inter = B[B["code"].isin(overlap)].sort_values("综合分")
print("\n" + "=" * 72)
print(f"【A∩B 交集】又便宜又稳 = 最甜的点({len(overlap)} 只)")
print("=" * 72)
show(inter, COLS, n=len(inter))

# ---------- 生成 agent 深挖命令 ----------
pool = inter if len(inter) else B
print("\n" + "=" * 72)
print("下一步:用完整 agent 逐只深挖(优先交集,其次B)。复制命令运行:")
print("=" * 72)
for _, r in pool.head(8).iterrows():
    print(f"python agent_step8_block_trade.py {r['code']}    # {r['name']} ({r['行业']})")

print("\n>>> 把 A、B 两表 + 交集发我,我们一起判读:")
print("    哪些是真便宜、哪些可能是周期陷阱?然后逐只深挖验证(分红/ROIC/排雷都在agent里)。")
