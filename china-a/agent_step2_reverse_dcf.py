# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 深度层 步骤2:反向 DCF
================================================================
新增两个工具(接进 step1 的 tool-loop):
  · get_fcf_3y(code)  → 近3年平均自由现金流(经营现金流 − 资本开支)
                        【自带字段发现】:匹配不到字段就回传可用列名
  · reverse_dcf(code, r, g_perp, years)
                      → 纯代码反推"市场当前隐含的高增长期增速"
                        单位/EV组装都在代码内,模型只调用+判断

反向DCF逻辑:解 g_implied,使
  EV ≈ Σ_{t=1..N} FCF0·(1+g)^t/(1+r)^t  +  [FCF0·(1+g)^N·(1+g_perp)/(r-g_perp)]/(1+r)^N
其中 EV 原型期简化为 ≈ 总市值(net debt 后续接资产负债表再补)。

前置:pip install openai akshare pandas;export ZAI_API_KEY=...
需 step1 / step4b 文件与 sina_sector.csv 同目录。

依赖文件同目录。
"""

import os
import re
import json
import akshare as ak
import pandas as pd

# 复用 step1 的客户端、低层工具与 loop 配置
from agent_step1_toolloop import (
    client, MODEL, TEMPERATURE, _clean,
    tool_get_stock_quality, tool_get_stock_basics,
)
from step4b_market_factors import SECTOR_CACHE


# ----------------------------------------------------------------------
# 工具:近3年平均自由现金流(自带字段发现)
# ----------------------------------------------------------------------
def _sina_code(code):
    code = str(code).zfill(6)
    return ("sh" if code[0] == "6" else "sz") + code


def _find_col(cols, keys):
    for c in cols:
        if any(k in str(c) for k in keys):
            return c
    return None


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return float("nan")


def get_fcf_3y(code):
    code = str(code).zfill(6)
    df = ak.stock_financial_report_sina(stock=_sina_code(code), symbol="现金流量表")
    cols = list(df.columns)
    cfo_col = _find_col(cols, ["经营活动产生的现金流量净额"])
    capex_col = _find_col(cols, ["购建固定资产"])
    period_col = _find_col(cols, ["报表日期", "报告日", "截止", "报告期", "日期"]) or cols[0]
    if not (cfo_col and capex_col):
        # 字段发现失败 → 回传列名,供修正
        return {"error": "未找到现金流字段",
                "found": {"cfo": cfo_col, "capex": capex_col, "period": period_col},
                "available_columns": cols}

    d = df[[period_col, cfo_col, capex_col]].copy()
    d["_p"] = d[period_col].astype(str).str.replace(r"\D", "", regex=True)
    d = d[d["_p"].str.endswith("1231")].copy()          # 年报
    d["_p"] = pd.to_numeric(d["_p"], errors="coerce")
    d = d.sort_values("_p").tail(3)                     # 近3年
    if d.empty:
        return {"error": "无年报现金流数据"}

    d["cfo"] = d[cfo_col].map(_num)
    d["capex"] = d[capex_col].map(_num)
    d["fcf"] = d["cfo"] - d["capex"]
    detail = [{"报告期": int(p), "经营现金流": c, "资本开支": cx, "FCF": f}
              for p, c, cx, f in zip(d["_p"], d["cfo"], d["capex"], d["fcf"])]
    return _clean({"code": code,
                   "fcf_3y_avg_yuan": float(d["fcf"].mean()),
                   "明细": detail})


# ----------------------------------------------------------------------
# 工具:反向 DCF(纯代码)
# ----------------------------------------------------------------------
def _market_cap_yuan(code):
    code = str(code).zfill(6)
    if not os.path.exists(SECTOR_CACHE):
        return None
    df = pd.read_csv(SECTOR_CACHE, dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)
    hit = df[df["code"] == code]
    if not len(hit) or pd.isna(hit.iloc[0].get("mktcap")):
        return None
    return float(hit.iloc[0]["mktcap"]) * 1e4          # 万元 → 元


def _net_debt_yuan(code):
    """近一年净有息负债 = 有息负债(短期借款+长期借款+应付债券+一年内到期非流动负债)
    − 货币资金,单位元。净现金公司为负。取数失败/无年报返回 None。
    用于把反向DCF的 EV 从『≈总市值』修正为『总市值 + 净有息负债』(FCFF 应对 EV 而非股权)。"""
    code = str(code).zfill(6)
    try:
        bs = ak.stock_financial_report_sina(stock=_sina_code(code), symbol="资产负债表")
    except Exception:
        return None
    period_col = "报告日" if "报告日" in bs.columns else bs.columns[0]
    d = bs.copy()
    d["_p"] = d[period_col].astype(str).str.replace(r"\D", "", regex=True)
    d = d[d["_p"].str.endswith("1231")]
    if d.empty:
        return None
    d["_p"] = pd.to_numeric(d["_p"], errors="coerce")
    latest = d.sort_values("_p").iloc[-1]

    def _g(col):
        if col not in d.columns:
            return 0.0
        v = _num(latest[col])
        return 0.0 if pd.isna(v) else v

    int_debt = sum(_g(k) for k in ["短期借款", "长期借款", "应付债券", "一年内到期的非流动负债"])
    return int_debt - _g("货币资金")


def _dcf_ev(fcf0, g, r, gp, N):
    pv = sum(fcf0 * (1 + g) ** t / (1 + r) ** t for t in range(1, N + 1))
    fcf_N = fcf0 * (1 + g) ** N
    tv = fcf_N * (1 + gp) / (r - gp)
    return pv + tv / (1 + r) ** N


def reverse_dcf(code, r=0.09, g_perp=0.03, years=10):
    # 入参归一化:模型常把百分数(9)误当小数,这里把 >1 的折现率/增长率按百分数处理
    if r is not None and r > 1:
        r = r / 100.0
    if g_perp is not None and g_perp > 1:
        g_perp = g_perp / 100.0
    # 硬性合理区间校验:超出即拒绝,不返回会被误读成"高估/低估"的废结果
    if not (0.03 <= r <= 0.30):
        return {"error": f"折现率 r={r} 不在合理区间[3%,30%],请用小数(如0.09)"}
    if not (-0.05 <= g_perp <= 0.08):
        return {"error": f"永续增长 g_perp={g_perp} 不在合理区间[-5%,8%],请用小数(如0.03)"}
    if not (3 <= years <= 20):
        return {"error": f"高增长年数 years={years} 不在合理区间[3,20]"}

    fcf = get_fcf_3y(code)
    if "error" in fcf:
        return fcf
    fcf0 = fcf["fcf_3y_avg_yuan"]
    mc = _market_cap_yuan(code)
    if mc is None:
        return {"error": "无总市值(请先生成 sina_sector.csv)"}
    # EV = 总市值 + 净有息负债(FCFF 应折现到企业价值,而非股权市值)。
    # 净现金公司净有息负债为负 → EV<市值 → 隐含增速略下修;取数失败则回退 EV≈市值。
    net_debt = _net_debt_yuan(code)
    ev = mc + net_debt if net_debt is not None else mc
    ev_basis = "总市值+净有息负债" if net_debt is not None else "总市值(净有息负债取数失败,回退)"
    if r <= g_perp:
        return {"error": "折现率 r 必须大于永续增长率 g_perp"}
    if fcf0 <= 0:
        return _clean({"note": "近3年平均自由现金流为负,反向DCF不适用(公司净烧现金,无法反推增速)",
                       "fcf_3y_avg_yuan": fcf0, "总市值_yuan": mc,
                       "净有息负债_yuan": net_debt, "EV_yuan": ev})

    f = lambda g: _dcf_ev(fcf0, g, r, g_perp, years) - ev
    lo, hi = -0.90, 2.00
    if f(lo) > 0:
        return _clean({"implied_growth": None,
                       "note": "即使增速为-90%,模型估值仍高于EV→市场隐含预期极低/可能低估",
                       "fcf_3y_avg_yuan": fcf0, "EV_yuan": ev,
                       "总市值_yuan": mc, "净有息负债_yuan": net_debt,
                       "参数": {"r": r, "g_perp": g_perp, "years": years}})
    if f(hi) < 0:
        return _clean({"implied_growth": None,
                       "note": "增速达200%仍撑不起当前EV→市场隐含预期极端乐观/可能高估",
                       "fcf_3y_avg_yuan": fcf0, "EV_yuan": ev,
                       "总市值_yuan": mc, "净有息负债_yuan": net_debt,
                       "参数": {"r": r, "g_perp": g_perp, "years": years}})
    for _ in range(100):                                # 二分求隐含增速
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    g_impl = (lo + hi) / 2
    return _clean({
        "code": code,
        "implied_growth": round(g_impl, 4),
        "implied_growth_pct": f"{g_impl*100:.1f}%",
        "fcf_3y_avg_yuan": fcf0,
        "EV_yuan": ev,
        "总市值_yuan": mc,
        "净有息负债_yuan": net_debt,
        "参数": {"折现率r": r, "永续增长g_perp": g_perp, "高增长年数N": years},
        "口径说明": f"EV={ev_basis};FCFF=近3年均值(经营现金流−资本开支)",
    })


# ----------------------------------------------------------------------
# 扩展工具集 + 调度
# ----------------------------------------------------------------------
DISPATCH = {
    "get_stock_quality": tool_get_stock_quality,
    "get_stock_basics": tool_get_stock_basics,
    "get_fcf_3y": get_fcf_3y,
    "reverse_dcf": reverse_dcf,
}

TOOLS = [
    {"type": "function", "function": {
        "name": "get_stock_quality",
        "description": "3年质量因子:3年平均ROE(%)、3年现金流质量CFQ、最新资产负债率(%)、风险红旗。数字来自财报,严禁自行估算。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "get_stock_basics",
        "description": "所属行业、PB、总市值、流通市值、最新价。做估值/EV用『总市值万元』。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "get_fcf_3y",
        "description": "近3年平均自由现金流(经营现金流−资本开支),单位元,含逐年明细。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "reverse_dcf",
        "description": "反向DCF:给定当前市值反推市场隐含的高增长期年增速。返回 implied_growth(小数)。参数均为小数:r折现率默认0.09(即9%),g_perp永续增长默认0.03(即3%),不要传9或3。折现的算术全在此工具内完成,严禁自行计算或编造增速。",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"},
            "r": {"type": "number", "description": "折现率,默认0.09"},
            "g_perp": {"type": "number", "description": "永续增长率,默认0.03"},
            "years": {"type": "integer", "description": "高增长年数,默认10"}},
            "required": ["code"]}}},
]

SYSTEM_PROMPT = """你是一位严谨的A股价值投资分析师。
规则(必须严格遵守):
1. 所有财务、估值、增速数字必须来自工具调用;严禁自行估算、推测或编造任何数字。反向DCF的隐含增速必须调用 reverse_dcf 获得,不得自己心算。
2. 工具返回 error/null 或 note 时,如实说明,不要填补或绕过。
3. 本轮简报分五节:【公司快照】【质量】【估值(PB/行业)】【反向DCF:市场隐含预期】【风险红旗】。
4. 在【反向DCF】节:陈述 reverse_dcf 返回的隐含增速,并结合【质量】节的ROE/现金流,做"市场隐含预期 vs 公司质地是否够得着"的判断性讨论(例如:隐含增速远低于其历史成长→市场预期保守;远高→市场乐观)。但这是对"市场在price什么"的分析,不是买卖结论。
5. 不下"买入/卖出/持有"结论,不给目标价。每个数字都应能对应到某次工具返回值。
"""


def run_agent(code, max_steps=8):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"请为股票 {code} 生成含反向DCF的结构化基本面简报。"},
    ]
    for _ in range(max_steps):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS,
            tool_choice="auto", temperature=TEMPERATURE,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = DISPATCH[name](**args)
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            print(f"  [工具] {name}({args}) → {json.dumps(result, ensure_ascii=False)[:300]}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    return "(达到最大步数仍未收尾)"


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"=== Agent反向DCF验证:{code} ===\n")
    print(run_agent(code))
    print("\n>>> 验收:1) get_fcf_3y 字段是否匹配成功(失败会回传列名);"
          "\n    2) 茅台隐含增速数量级是否合理(应是中等正数,不该是50%或负);"
          "\n    3) 隐含增速是否来自reverse_dcf而非模型心算;4) 有没有偷下买卖结论。")
