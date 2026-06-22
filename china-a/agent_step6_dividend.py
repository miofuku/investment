# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 深度层 步骤6:分红历史(真金白银的股东回报)
================================================================
信号价值:分红要拿真金白银出去,比利润表更难造假。常年稳定、
分红率不低 → 现金流大概率真实,管理层善待股东。契合"活得久"取向。

工具:get_dividend_history(code) —— 同花顺 stock_fhps_detail_ths
  取近N年:连续分红年数 / 平均股利支付率(分红÷净利润) / 近年股息率 /
           是否有"不分配"的铁公鸡年份。只报事实。

挂进 agent 当第 11 个工具。
前置:需 step1-5 与 step4b 同目录。
"""

import re
import json
import datetime as dt
import akshare as ak
import pandas as pd

from agent_step1_toolloop import client, MODEL, TEMPERATURE, _clean
from agent_step5_capital_flow import (
    DISPATCH as BASE_DISPATCH, TOOLS as BASE_TOOLS, chat_with_retry,
)


def _pct(x):
    """'37.74%' / '--' / NaN → 数值(百分点)或 None。"""
    if x is None:
        return None
    s = str(x).strip().rstrip("%")
    if s in ("--", "", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def get_dividend_history(code, years=10):
    code = str(code).zfill(6)
    try:
        df = ak.stock_fhps_detail_ths(symbol=code)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    need = {"报告期", "分红方案说明", "股利支付率", "税前分红率"}
    if not need.issubset(df.columns):
        return {"error": "字段不符", "available": list(df.columns)}

    # 只看年报分红(报告期含'年报'),近 years 年
    cur_year = dt.date.today().year
    rows = []
    for _, r in df.iterrows():
        period = str(r["报告期"])
        if "年报" not in period:
            continue
        m = re.match(r"(\d{4})", period)
        if not m or int(m.group(1)) < cur_year - years:
            continue
        说明 = str(r["分红方案说明"])
        派现 = ("不分配" not in 说明) and ("派" in 说明 or "分红总额" in df.columns)
        rows.append({
            "年度": m.group(1),
            "方案": 说明,
            "分红": 派现,
            "股利支付率": _pct(r["股利支付率"]),     # 分红/净利润
            "股息率": _pct(r["税前分红率"]),         # 税前股息率
        })

    rows.sort(key=lambda x: x["年度"])
    paid_years = [x for x in rows if x["分红"]]
    no_pay = [x["年度"] for x in rows if not x["分红"]]
    payouts = [x["股利支付率"] for x in paid_years if x["股利支付率"] is not None]
    yields_ = [x["股息率"] for x in paid_years if x["股息率"] is not None]

    return _clean({
        "code": code,
        "统计区间": f"近{years}年年报",
        "分红年数": len(paid_years),
        "未分配年份": no_pay,                       # 空=年年分红
        "连续分红": len(no_pay) == 0 and len(paid_years) >= 3,
        "平均股利支付率": round(sum(payouts) / len(payouts), 1) if payouts else None,
        "近年股息率": yields_[-1] if yields_ else None,
        "逐年明细": rows[-years:],
        "口径说明": "客观分红披露;股利支付率=分红/净利润,越稳越高=股东回报越实",
    })


# ---- 工具集:step5 的 10 个 + 1 = 11 个 ----
DISPATCH = dict(BASE_DISPATCH)
DISPATCH["get_dividend_history"] = get_dividend_history

TOOLS = list(BASE_TOOLS) + [
    {"type": "function", "function": {"name": "get_dividend_history",
        "description": "近10年分红历史:连续分红年数、是否有不分配的'铁公鸡'年份、平均股利支付率(分红÷净利润)、近年股息率。分红是真金白银,常年稳定高分红→现金流真实、善待股东。只报客观事实。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
]

SYSTEM_PROMPT = """你是一位严谨的A股价值投资分析师,产出供人决策的结构化论证,不替人做决定。
铁律:
1. 数字必须来自工具,严禁估算/编造。隐含增速必调reverse_dcf(r、g_perp用小数);null/error代表算不出,不可据此判高估低估。
2. 定性结论必须基于工具返回的原文/信号,引用点明依据;严禁脱离文本编造。
3. 资金面只陈述客观事实,严禁解读为资金"意图"/宏观叙事。
4. 分红:常年稳定分红+合理股利支付率是现金流真实与股东回报的客观佐证;但高分红≠便宜,需与估值、再投资需求结合看(高分红也可能是缺乏成长再投资机会)。
5. 简报分九节:【公司快照】【质量】【估值PB/行业】【反向DCF】【深度排雷】【定性:生意/护城河/监管/互动易】【资金面:质押/增减持/解禁/股东数】【分红:股东回报】【综合:摊开假设,不下买卖结论】。
6. 不下买卖结论,不给目标价。每个数字对应到某次工具返回。
"""


def run_agent(code, max_steps=18):
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请为 {code} 生成完整价值投资分析简报(含分红与资金面)。"}]
    for _ in range(max_steps):
        resp = chat_with_retry(model=MODEL, messages=messages, tools=TOOLS,
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
            print(f"  [工具] {tc.function.name}({args}) → {json.dumps(result, ensure_ascii=False)[:220]}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    return "(达到最大步数)"


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"=== 分红工具单测:{code} ===")
    print(json.dumps(get_dividend_history(code), ensure_ascii=False, indent=2))
    print(f"\n=== 完整简报(含分红):{code} ===\n")
    print(run_agent(code))
