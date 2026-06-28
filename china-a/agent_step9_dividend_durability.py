# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 深度层 步骤9:分红可持续性(大秦教训的确定性护栏)
================================================================
为何加:高股息"现金奶牛"故事最容易骗人。大秦铁路就是经典反例——
ROIC腰斩 + 资本开支暴增 + 分红被砍71%,三信号合起来证伪了"稳定高分红"叙事。
本工具把这三条线索做成**确定性交叉核对**:不替人下结论,只把"分红是不是真有
自由现金流和回报支撑"的客观信号摊开。数字全在代码里算,失真/缺数据诚实返回 null。

工具:get_dividend_durability(code) —— 交叉核对四条线索:
  1. ROIC 趋势:回报是否在下滑(单年ROIC 早 vs 近;失真时不用)
  2. 资本开支趋势:是否在大幅上升(挤占分红的钱)
  3. FCF 覆盖:自由现金流(经营现金流−资本开支)能否覆盖"分配股利+利息"现金流出
  4. 分红趋势:支付率是否下滑、近年是否出现"不分配"
数据源:新浪现金流量表(自有)+ 复用 get_dividend_history / get_roic_3y。

挂进 agent 当第 14 个工具(质量与股东回报的可持续性交叉验证)。
"""

import json
import akshare as ak
import pandas as pd

from agent_step1_toolloop import _clean, MODEL, TEMPERATURE
from agent_step2_reverse_dcf import _sina_code, _num, _find_col
from agent_step6_dividend import get_dividend_history
from agent_step7_roic import get_roic_3y
from agent_step8_block_trade import (
    DISPATCH as BASE_DISPATCH, TOOLS as BASE_TOOLS, chat_with_retry,
    SYSTEM_PROMPT as BASE_SYSTEM_PROMPT, _build_meta as base_build_meta,
)

# ── 阈值(大秦式信号的判定线,可调)──────────────────────────────────────
ROIC_DECLINE = 0.6     # 近年单年ROIC ≤ 早年 × 0.6(下滑>40%)→ 显著下滑
CAPEX_SURGE = 1.5      # 近年资本开支 ≥ 早年 × 1.5(上升>50%)→ 大幅上升
PAYOUT_CUT = 0.5       # 近年支付率 < 早年峰值 × 0.5 → 分红明显下滑
COVER_MIN = 0.7        # FCF 覆盖(分红+利息)< 0.7 才算"缺口"(近1.0=几乎全额分配,健康,不报)


def _annual_cashflow(code):
    """新浪现金流量表 → 近4年年报:经营现金流 / 资本开支 / 分配股利及利息现金。
    返回按年升序的 list[dict],取数失败返回 (None, err)。"""
    try:
        df = ak.stock_financial_report_sina(stock=_sina_code(code), symbol="现金流量表")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    cols = list(df.columns)
    cfo_c = _find_col(cols, ["经营活动产生的现金流量净额"])
    capex_c = _find_col(cols, ["购建固定资产"])
    div_c = _find_col(cols, ["分配股利"])          # 分配股利、利润或偿付利息支付的现金
    period_c = _find_col(cols, ["报告日", "报表日期", "报告期", "日期"]) or cols[0]
    if not (cfo_c and capex_c):
        return None, "未找到现金流字段"

    d = df[[period_c, cfo_c, capex_c] + ([div_c] if div_c else [])].copy()
    d["_p"] = d[period_c].astype(str).str.replace(r"\D", "", regex=True)
    d = d[d["_p"].str.endswith("1231")].copy()
    d["_p"] = pd.to_numeric(d["_p"], errors="coerce")
    d = d.sort_values("_p").tail(4)
    if d.empty:
        return None, "无年报现金流数据"
    out = []
    for _, r in d.iterrows():
        cfo = _num(r[cfo_c]); capex = _num(r[capex_c])
        divcash = _num(r[div_c]) if div_c else float("nan")
        out.append({"年度": int(r["_p"]),
                    "经营现金流": None if pd.isna(cfo) else cfo,
                    "资本开支": None if pd.isna(capex) else capex,
                    "FCF": None if (pd.isna(cfo) or pd.isna(capex)) else cfo - capex,
                    "分配股利及利息现金": None if pd.isna(divcash) else divcash})
    return out, None


def _trend(vals):
    """非空序列的 (早, 近);不足两点返回 (None, None)。"""
    xs = [v for v in vals if v is not None]
    if len(xs) < 2:
        return None, None
    return xs[0], xs[-1]


def get_dividend_durability(code):
    code = str(code).zfill(6)
    flags = []

    # 1) 分红历史(复用)
    div = get_dividend_history(code)
    payout_avg = div.get("平均股利支付率") if isinstance(div, dict) else None
    consec = div.get("连续分红") if isinstance(div, dict) else None
    yld = div.get("近年股息率") if isinstance(div, dict) else None
    payout_series = [r.get("股利支付率") for r in (div.get("逐年明细") or [])] if isinstance(div, dict) else []
    recent_no_pay = [r["年度"] for r in (div.get("逐年明细") or [])[-3:]
                     if isinstance(div, dict) and not r.get("分红")]

    # 2) ROIC 趋势(复用;失真则不用)
    roic = get_roic_3y(code)
    roic_trend = None
    if isinstance(roic, dict) and roic.get("可信") is not False:
        singles = [d.get("单年ROIC_pct") for d in (roic.get("逐年明细") or [])]
        e, l = _trend(singles)
        if e is not None and l is not None:
            roic_trend = {"早": e, "近": l,
                          "下滑": (e > 0 and l <= e * ROIC_DECLINE)}
            if roic_trend["下滑"]:
                flags.append(f"ROIC显著下滑({e:.1f}%→{l:.1f}%)")
    elif isinstance(roic, dict) and roic.get("可信") is False:
        roic_trend = {"失真": True, "note": "ROIC单年波动极大、疑似一次性项目污染,趋势不可用"}

    # 3) 现金流:资本开支趋势 + FCF 覆盖分红
    cf, cf_err = _annual_cashflow(code)
    capex_trend = fcf_cover = None
    if cf:
        ce, cl = _trend([r["资本开支"] for r in cf])
        if ce is not None and cl is not None:
            capex_trend = {"早": ce, "近": cl, "上升": (ce > 0 and cl >= ce * CAPEX_SURGE)}
            if capex_trend["上升"]:
                flags.append(f"资本开支大幅上升({ce/1e8:.1f}亿→{cl/1e8:.1f}亿)")
        fcfs = [r["FCF"] for r in cf if r["FCF"] is not None]
        divs = [r["分配股利及利息现金"] for r in cf if r.get("分配股利及利息现金") is not None]
        avg_fcf = sum(fcfs) / len(fcfs) if fcfs else None
        avg_div = sum(divs) / len(divs) if divs else None
        cover = (round(avg_fcf / avg_div, 2) if (avg_fcf is not None and avg_div and avg_div > 0)
                 else None)
        fcf_cover = {"FCF_3y_avg_亿": round(avg_fcf / 1e8, 2) if avg_fcf is not None else None,
                     "分红利息现金_avg_亿": round(avg_div / 1e8, 2) if avg_div is not None else None,
                     "覆盖倍数": cover}
        if avg_fcf is not None and avg_fcf < 0:
            flags.append("自由现金流为负,分红无FCF支撑")
        elif cover is not None and cover < COVER_MIN:
            flags.append(f"分红+利息现金流出明显超过自由现金流(覆盖{cover}<{COVER_MIN})")

    # 4) 分红趋势(支付率下滑 / 近年不分配)
    pe, pl = _trend(payout_series)
    if recent_no_pay:
        flags.append(f"近年出现不分配年份({','.join(recent_no_pay)})")
    elif payout_series:
        peak = max([p for p in payout_series if p is not None], default=None)
        if peak and pl is not None and pl < peak * PAYOUT_CUT:
            flags.append(f"股利支付率明显下滑(峰值{peak:.0f}%→近{pl:.0f}%)")

    # 综合判定(不下买卖结论,只摊开"分红是否真有支撑")
    has_roic_down = any("ROIC显著下滑" in f for f in flags)
    has_capex_up = any("资本开支大幅上升" in f for f in flags)
    has_cover = any(("覆盖" in f) or ("FCF支撑" in f) for f in flags)
    enough = (roic_trend is not None) or (capex_trend is not None) or (fcf_cover is not None)
    if has_roic_down and has_capex_up:
        verdict = "现金奶牛故事需警惕:回报下滑且资本开支上升,分红可持续性存疑(大秦式信号)"
    elif has_cover:
        verdict = "分红缺乏自由现金流支撑,可持续性存疑"
    elif not enough:
        verdict = "数据不足,无法判定可持续性"
    elif not flags and consec:
        verdict = "分红有自由现金流支撑、回报与资本开支稳定,暂无可持续性红旗"
    else:
        verdict = "存在个别可持续性风险信号(见 durability_flags)"

    return _clean({
        "code": code,
        "连续分红": consec,
        "平均股利支付率": payout_avg,
        "近年股息率": yld,
        "ROIC趋势": roic_trend,
        "资本开支趋势": ({"早_亿": round(capex_trend["早"]/1e8, 2),
                        "近_亿": round(capex_trend["近"]/1e8, 2),
                        "上升": capex_trend["上升"]} if capex_trend else
                       (None if cf else {"error": cf_err})),
        "FCF覆盖分红": fcf_cover,
        "durability_flags": flags,
        "verdict": verdict,
        "口径说明": ("交叉核对回报(ROIC)、再投资(资本开支)、现金覆盖(FCF vs 分红+利息)与分红趋势;"
                   "‘分配股利及利息现金’含利息,作保守(偏严)的覆盖口径;只摊开客观信号,不下买卖结论"),
    })


# ---- 工具集:step8 的 13 个 + 1 = 14 个 ----
DISPATCH = dict(BASE_DISPATCH)
DISPATCH["get_dividend_durability"] = get_dividend_durability

TOOLS = list(BASE_TOOLS) + [
    {"type": "function", "function": {"name": "get_dividend_durability",
        "description": "分红可持续性交叉核对(大秦铁路教训):一次性把 ROIC趋势(回报是否下滑)、资本开支趋势(是否暴增挤占分红)、FCF对分红+利息的覆盖倍数、分红/支付率趋势 摊开。返回 durability_flags 与 verdict。高股息‘现金奶牛’若同时出现ROIC下滑+资本开支上升+FCF覆盖不足,则‘稳定高分红’叙事存疑。数字来自财报,失真/缺数据返回null,不可据空值编造结论。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
]

SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + """

【分红可持续性(本步新增第14工具)】
8. 涉及高股息/现金奶牛类标的时,必须调用 get_dividend_durability 做交叉核对,并在【分红】节呈现其 verdict 与 durability_flags:
   - 把"稳定高分红"当作需要验证的假设,而非现成结论。重点看三条线索是否同时恶化:ROIC下滑、资本开支大幅上升、FCF覆盖不足(覆盖<1 或 FCF为负)。
   - 三者同时出现 = 大秦式陷阱信号(回报变差、烧钱扩张、分红靠借钱/吃老本),须明确点出"分红可持续性存疑"。
   - 任何 null 字段代表算不出(失真/缺数据),如实说明、不可据空值编造支撑或证伪。
   - 仍不下买卖结论:只把"分红到底有没有真金白银和回报支撑"摊给用户。
"""


def _build_meta(code, results):
    """在 step8 结构化 meta 基础上,补分红可持续性:把存疑 verdict 并入重大红旗。"""
    meta = base_build_meta(code, results)
    dd = results.get("get_dividend_durability") or {}
    verdict = dd.get("verdict") or ""
    if any(k in verdict for k in ("存疑", "需警惕")):
        flags = list(meta.get("major_flags") or [])
        if "分红可持续性存疑" not in flags:
            flags.append("分红可持续性存疑")
        meta["major_flags"] = flags
    return meta


def run_agent(code, max_steps=24):
    """返回 (简报正文, meta)。meta 由工具确定性汇总,不依赖 LLM 文字(同 step8 约定)。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请为 {code} 生成完整价值投资分析简报。"}]
    tool_results = {}
    for _ in range(max_steps):
        resp = chat_with_retry(model=MODEL, messages=messages, tools=TOOLS,
                               tool_choice="auto", temperature=TEMPERATURE)
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content, _build_meta(code, tool_results)
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = DISPATCH[tc.function.name](**args)
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            if isinstance(result, dict) and "error" not in result:
                tool_results[tc.function.name] = result
            print(f"  [工具] {tc.function.name}({args}) → {json.dumps(result, ensure_ascii=False)[:200]}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    return "(达到最大步数)", _build_meta(code, tool_results)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--tool":
        code = sys.argv[2] if len(sys.argv) > 2 else "601006"   # 大秦铁路
        print(f"=== 分红可持续性工具单测:{code} ===")
        print(json.dumps(get_dividend_durability(code), ensure_ascii=False, indent=2))
    else:
        code = sys.argv[1] if len(sys.argv) > 1 else "601006"
        content, meta = run_agent(code)
        print("<<<META>>>" + json.dumps(meta, ensure_ascii=False) + "<<<END_META>>>")
        print(f"\n=== 完整简报(14工具):{code} ===\n")
        print(content)
