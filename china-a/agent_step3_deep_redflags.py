# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 深度层 步骤3:三表深度排雷
================================================================
新增工具:
  · get_balance_items(code) → 资产负债表精确科目近3年(应收/存货/商誉/
        货币资金/有息负债四项/归母净资产)+ 利润表营业总收入
  · red_flags_deep(code)    → 排雷三件套 + EV精度:
        ① 应收账款增速 vs 营收增速 背离
        ② 存货增速 vs 营收增速 背离
        ③ 商誉 / 归母净资产 占比
        ④ 净有息负债(=有息负债−货币资金)→ 修正反向DCF的EV

字段用【精确等于】匹配,避开"差一字"陷阱:
  应收账款≠应收票据及应收账款;一年内到期的非流动负债≠…非流动资产;
  归属于母公司股东权益合计≠少数股东权益/总计;应付债券不含优先股/永续债。

前置:pip install openai akshare pandas;export ZAI_API_KEY=...
需 step1/step2/step4b 文件与 sina_sector.csv 同目录。
"""

import json
import akshare as ak
import pandas as pd

from agent_step1_toolloop import client, MODEL, TEMPERATURE, _clean
from agent_step2_reverse_dcf import (
    get_fcf_3y, reverse_dcf, _sina_code, _num, _market_cap_yuan,
    tool_get_stock_quality, tool_get_stock_basics,
)

# 精确科目名(经 probe3 确认)
BS_ITEMS = {
    "应收账款": "应收账款",
    "存货": "存货",
    "商誉": "商誉",
    "货币资金": "货币资金",
    "短期借款": "短期借款",
    "长期借款": "长期借款",
    "应付债券": "应付债券",
    "一年内到期的非流动负债": "一年内到期的非流动负债",
    "归母净资产": "归属于母公司股东权益合计",
}
PERIOD_COL = "报告日"
REV_COL = "营业总收入"


def _annual_last3(df):
    df = df.copy()
    df["_p"] = df[PERIOD_COL].astype(str).str.replace(r"\D", "", regex=True)
    df = df[df["_p"].str.endswith("1231")]
    df["_p"] = pd.to_numeric(df["_p"], errors="coerce")
    return df.sort_values("_p").tail(3)


def get_balance_items(code):
    code = str(code).zfill(6)
    bs = ak.stock_financial_report_sina(stock=_sina_code(code), symbol="资产负债表")
    il = ak.stock_financial_report_sina(stock=_sina_code(code), symbol="利润表")

    for label, col in BS_ITEMS.items():
        if col not in bs.columns:
            return {"error": f"资产负债表缺列『{col}』", "available": list(bs.columns)}
    if REV_COL not in il.columns:
        return {"error": f"利润表缺列『{REV_COL}』", "available": list(il.columns)}

    bs3 = _annual_last3(bs)
    il3 = _annual_last3(il)
    if bs3.empty or il3.empty:
        return {"error": "无足够年报数据"}

    out = {"code": code, "明细": []}
    rev_map = dict(zip(il3["_p"], il3[REV_COL].map(_num)))
    for _, r in bs3.iterrows():
        p = int(r["_p"])
        row = {"报告期": p, "营业总收入": rev_map.get(p)}
        for label, col in BS_ITEMS.items():
            row[label] = _num(r[col])
        out["明细"].append(row)
    return _clean(out)


def _yoy(series_new, series_old):
    if series_old and series_old != 0:
        return series_new / series_old - 1
    return None


def red_flags_deep(code):
    data = get_balance_items(code)
    if "error" in data:
        return data
    rows = sorted(data["明细"], key=lambda x: x["报告期"])
    latest, prev = rows[-1], rows[-2] if len(rows) >= 2 else None

    flags, metrics = [], {}

    # ①② 应收/存货 增速 vs 营收增速 背离
    if prev:
        g_rev = _yoy(latest["营业总收入"], prev["营业总收入"])
        g_ar = _yoy(latest["应收账款"], prev["应收账款"])
        g_inv = _yoy(latest["存货"], prev["存货"])
        metrics["营收增速"] = g_rev
        metrics["应收增速"] = g_ar
        metrics["存货增速"] = g_inv
        if g_rev is not None and g_ar is not None and g_ar - g_rev > 0.30:
            flags.append(f"应收增速({g_ar*100:.0f}%)远超营收({g_rev*100:.0f}%)")
        if g_rev is not None and g_inv is not None and g_inv - g_rev > 0.30:
            flags.append(f"存货增速({g_inv*100:.0f}%)远超营收({g_rev*100:.0f}%)")

    def _z(v):                       # NaN/None 安全取0(NaN 是 truthy,不能用 `or`)
        return 0.0 if (v is None or (isinstance(v, float) and pd.isna(v))) else float(v)

    # ③ 商誉 / 归母净资产
    eq = _z(latest["归母净资产"])
    gw = _z(latest["商誉"])
    if eq > 0:
        gw_ratio = gw / eq
        metrics["商誉占净资产"] = gw_ratio
        if gw_ratio > 0.30:
            flags.append(f"商誉占净资产{gw_ratio*100:.0f}%(减值风险)")

    # ④ 净有息负债 → EV 修正
    int_debt = sum(_z(latest.get(k)) for k in
                   ["短期借款", "长期借款", "应付债券", "一年内到期的非流动负债"])
    cash = _z(latest.get("货币资金"))
    net_debt = int_debt - cash
    metrics["有息负债"] = int_debt
    metrics["货币资金"] = cash
    metrics["净有息负债"] = net_debt

    # 用净有息负债修正 EV,重算隐含增速(更准)
    mc = _market_cap_yuan(code)
    ev_adj = (mc + net_debt) if mc is not None else None
    metrics["EV修正_市值加净负债"] = ev_adj
    note_ev = ("净现金,修正后EV<市值,隐含增速会略低于EV≈市值的版本"
               if net_debt < 0 else "有净负债,修正后EV>市值")

    return _clean({"code": code, "深度红旗": flags, "指标": metrics,
                   "EV修正说明": note_ev})


# ---- 扩展工具集(在 step2 基础上再加两个)----
DISPATCH = {
    "get_stock_quality": tool_get_stock_quality,
    "get_stock_basics": tool_get_stock_basics,
    "get_fcf_3y": get_fcf_3y,
    "reverse_dcf": reverse_dcf,
    "get_balance_items": get_balance_items,
    "red_flags_deep": red_flags_deep,
}

TOOLS = [
    {"type": "function", "function": {"name": "get_stock_quality",
        "description": "3年质量因子:3年ROE/CFQ/负债率/红旗。数字来自财报,严禁估算。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "get_stock_basics",
        "description": "行业、PB、总市值、流通市值、最新价。估值用『总市值万元』。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "get_fcf_3y",
        "description": "近3年平均自由现金流(经营现金流−资本开支),元,含明细。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "reverse_dcf",
        "description": "反向DCF反推市场隐含增速。参数均为小数(r默认0.09即9%,g_perp默认0.03即3%,勿传9或3)。折现算术在工具内,严禁自行计算。",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}, "r": {"type": "number"},
            "g_perp": {"type": "number"}, "years": {"type": "integer"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "get_balance_items",
        "description": "资产负债表精确科目近3年:应收账款/存货/商誉/货币资金/有息负债四项/归母净资产 + 营业总收入。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "red_flags_deep",
        "description": "三表深度排雷:应收/存货增速vs营收背离、商誉占净资产、净有息负债(及EV修正)。返回深度红旗与指标,数字均来自财报。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
]

SYSTEM_PROMPT = """你是一位严谨的A股价值投资分析师。
规则:
1. 所有数字必须来自工具;严禁自行估算/编造。隐含增速必调 reverse_dcf,排雷必调 red_flags_deep。reverse_dcf 的 r、g_perp 用小数(如0.09、0.03),不要传9、3。
2. 工具返回 error/null/note 时如实说明,不填补。【特别重要】当 reverse_dcf 返回 implied_growth 为 null 或 error/note 时,这代表"无法计算",绝不可据此推断"高估/低估/极端乐观";只能如实说明反向DCF本轮未能给出有效隐含增速及原因,不就估值下任何方向性结论。
3. 简报分六节:【公司快照】【质量】【估值PB/行业】【反向DCF:市场隐含预期】【深度排雷:应收/存货/商誉/净负债】【风险红旗汇总】。
4. 【深度排雷】节:陈述 red_flags_deep 的红旗与指标;若净有息负债为负(净现金),指出真实EV低于市值、反向DCF隐含增速应略下修。
5. 不下买入/卖出/持有结论,不给目标价。每个数字对应到某次工具返回。
"""


def run_agent(code, max_steps=10):
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请为 {code} 生成含反向DCF与三表排雷的完整简报。"}]
    for _ in range(max_steps):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS,
            tool_choice="auto", temperature=TEMPERATURE)
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = DISPATCH[tc.function.name](**args)
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            print(f"  [工具] {tc.function.name}({args}) → {json.dumps(result, ensure_ascii=False)[:280]}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    return "(达到最大步数)"


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"=== Agent三表排雷验证:{code} ===\n")
    print(run_agent(code))
    print("\n>>> 验收:1) 资产负债表字段是否精确匹配(失败回传列名);"
          "\n    2) 排雷指标算得对不对(应收/存货/营收增速、商誉占比、净有息负债);"
          "\n    3) 茅台应为净现金(净有息负债为负)、无商誉雷;"
          "\n    4) 换一只有商誉/有应收存货问题的票,红旗能否真亮。")
