# -*- coding: utf-8 -*-
"""
A�股价值投资 Agent —— 深度层 步骤5:资金面信号(客观,只标记不解读)
================================================================
原则(与昨日共识一致):只报【可查证的客观事实】,绝不解读资金"意图"。
工具说"质押63%/控股股东减持/3个月后解禁8%",不说"国家队在护盘"。

四个客观信号(全部非东财、已实测可用):
  · 股权质押比例  stock_gpzy_pledge_ratio_em(全市场按日期,按code查)—— 排雷
  · 股东增减持    stock_shareholder_change_ths(产业资本/法人股东,非高管噪音)
  · 未来限售解禁  stock_restricted_release_queue_sina —— 资金面压力
  · 股东人数变化  stock_hold_num_cninfo(全市场按日期)—— 筹码集中度

前置:pip install openai akshare pandas;export ZAI_API_KEY=...
需 step1-4 与 step4b 文件同目录。
"""

import re
import json
import datetime as dt
import akshare as ak
import pandas as pd

from agent_step1_toolloop import client, MODEL, TEMPERATURE, _clean
from agent_step4_text_qualitative import (
    DISPATCH as BASE_DISPATCH, TOOLS as BASE_TOOLS,
    chat_with_retry,
)

# 质押/股东人数是全市场表,首次拉取后缓存当次进程,避免重复请求
_PLEDGE_CACHE = {}
_HOLDERNUM_CACHE = {}


def _recent_trade_date():
    """用最近的季度末作为质押/股东人数的查询日期(这两个接口按日期返回全市场)。"""
    today = dt.date.today()
    for m, d in [(12, 31), (9, 30), (6, 30), (3, 31)]:
        cand = dt.date(today.year if (today.month, today.day) >= (m, d) else today.year - 1, m, d)
        if cand <= today:
            return cand.strftime("%Y%m%d")
    return (today.replace(year=today.year - 1)).strftime("%Y%m%d")


def _pledge_ratio(code):
    d = _recent_trade_date()
    if d not in _PLEDGE_CACHE:
        try:
            df = ak.stock_gpzy_pledge_ratio_em(date=d)
            df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
            _PLEDGE_CACHE[d] = df.set_index("股票代码")["质押比例"].to_dict()
        except Exception:
            _PLEDGE_CACHE[d] = {}
    return _PLEDGE_CACHE[d].get(str(code).zfill(6))   # 不在表里=无质押或未披露


def _holder_num_change(code):
    d = _recent_trade_date()
    if d not in _HOLDERNUM_CACHE:
        try:
            df = ak.stock_hold_num_cninfo(date=d)
            df["证券代码"] = df["证券代码"].astype(str).str.zfill(6)
            _HOLDERNUM_CACHE[d] = df.set_index("证券代码")["股东人数增幅"].to_dict()
        except Exception:
            _HOLDERNUM_CACHE[d] = {}
    return _HOLDERNUM_CACHE[d].get(str(code).zfill(6))


def _shareholder_change(code, months=12):
    """同花顺股东(法人/产业资本)增减持,近 months 个月,从文本解析方向。"""
    try:
        df = ak.stock_shareholder_change_ths(symbol=str(code).zfill(6))
    except Exception as e:
        return {"error": f"{type(e).__name__}", "增减持": None}
    if "变动数量" not in df.columns or "公告日期" not in df.columns:
        return {"available": list(df.columns)}
    cutoff = (dt.date.today() - dt.timedelta(days=30 * months)).strftime("%Y-%m-%d")
    df = df[df["公告日期"].astype(str) >= cutoff]
    inc = dec = 0
    recent = []
    for _, r in df.iterrows():
        txt = str(r["变动数量"])
        direction = "增持" if "增持" in txt else ("减持" if "减持" in txt else "")
        if direction == "增持":
            inc += 1
        elif direction == "减持":
            dec += 1
        recent.append({"日期": str(r["公告日期"]), "股东": str(r.get("变动股东", ""))[:20],
                       "变动": txt, "途径": str(r.get("变动途径", ""))})
    return {"近{}月增持笔数".format(months): inc, "减持笔数": dec,
            "净方向": "净增持" if inc > dec else ("净减持" if dec > inc else "持平/无"),
            "明细": recent[:8]}


def _upcoming_unlock(code):
    """未来限售解禁(资金面压力)。"""
    try:
        df = ak.stock_restricted_release_queue_sina(symbol=str(code).zfill(6))
    except Exception as e:
        return {"error": f"{type(e).__name__}"}
    if "解禁日期" not in df.columns:
        return {"available": list(df.columns)}
    today = dt.date.today().strftime("%Y-%m-%d")
    fut = df[df["解禁日期"].astype(str) >= today].sort_values("解禁日期")
    return [{"解禁日期": str(r["解禁日期"]), "解禁市值亿元": r.get("解禁股流通市值")}
            for _, r in fut.head(5).iterrows()]


def capital_flow_signals(code):
    """资金面客观信号汇总。只陈述事实,不解读意图。"""
    code = str(code).zfill(6)
    pledge = _pledge_ratio(code)
    flags = []
    if pledge is not None and pledge > 40:
        flags.append(f"高质押({pledge:.0f}%)")
    hn = _holder_num_change(code)
    if hn is not None and hn < -15:
        flags.append(f"股东人数减{abs(hn):.0f}%(筹码集中)")
    sc = _shareholder_change(code)
    if isinstance(sc, dict) and sc.get("净方向") == "净减持":
        flags.append("产业资本近一年净减持")
    return _clean({
        "code": code,
        "股权质押比例": pledge,            # None=不在质押名单/未披露
        "股东人数增幅pct": hn,
        "股东增减持": sc,
        "未来解禁": _upcoming_unlock(code),
        "资金面红旗": flags,
        "口径说明": "均为客观披露数据;不含任何资金意图解读",
    })


# ---- 工具集:在 step4 的 9 个基础上 +1 = 10 个 ----
DISPATCH = dict(BASE_DISPATCH)
DISPATCH["capital_flow_signals"] = capital_flow_signals

TOOLS = list(BASE_TOOLS) + [
    {"type": "function", "function": {"name": "capital_flow_signals",
        "description": "资金面客观信号:股权质押比例、产业资本(法人股东)近一年增减持方向、未来限售解禁、股东人数变化。全部为客观披露数据,只陈述事实,严禁解读为任何资金'意图'或宏观叙事。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
]

SYSTEM_PROMPT = """你是一位严谨的A股价值投资分析师,产出供人决策的结构化论证,不替人做决定。
铁律:
1. 数字必须来自工具,严禁自行估算/编造。隐含增速必调reverse_dcf(r、g_perp用小数如0.09);null/error代表算不出,不可据此判高估低估。
2. 定性结论必须基于工具返回的原文/信号,引用时点明依据;严禁脱离文本编造。
3. 【资金面】只陈述客观事实(质押比例、增减持方向、解禁、股东人数);严禁解读为资金"意图"、"护盘"、"洗盘"等宏观叙事。高质押/控股股东减持/临近大额解禁可作为客观风险提示。
4. 简报分八节:【公司快照】【质量】【估值PB/行业】【反向DCF】【深度排雷】【定性:生意/护城河/监管/互动易】【资金面:质押/增减持/解禁/股东数】【综合:摊开假设,不下买卖结论】。
5. 不下买入/卖出/持有结论,不给目标价。每个数字对应到某次工具返回。
"""


def run_agent(code, max_steps=16):
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请为 {code} 生成完整价值投资分析简报(含资金面)。"}]
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
            print(f"  [工具] {tc.function.name}({args}) → {json.dumps(result, ensure_ascii=False)[:240]}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    return "(达到最大步数)"


if __name__ == "__main__":
    import sys
    # 先单独验资金面工具的取数与解析,再看完整简报
    code = sys.argv[1] if len(sys.argv) > 1 else "002230"
    print(f"=== 资金面工具单测:{code} ===")
    print(json.dumps(capital_flow_signals(code), ensure_ascii=False, indent=2))
    print(f"\n=== 完整简报(含资金面):{code} ===\n")
    print(run_agent(code))
