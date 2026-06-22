# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 深度层 步骤4:文本检索(定性)
================================================================
与数字工具的本质区别:这里把【非结构化文本】喂给模型去读/摘要。
纪律:工具只取原文,模型摘要必须基于原文、给出依据,严禁脱离文本编造。
粒度按"真实能拿到的"设计——公告/问询函多为【标题+链接】而非全文,
故工具产出"信号+链接",由人去巨潮核全文。

三个工具:
  · get_business_profile(code) → 主营/经营范围/简介(护城河线索)
  · scan_disclosures(code)     → 近2年公告标题筛 问询/关注/监管/警示函(定性红旗)
  · get_irm_qa(code)           → 互动易问答,挑财务质疑类(应收/商誉/现金流/减值)
                                 【自带字段发现】

前置:pip install openai akshare pandas;export ZAI_API_KEY=...
需 step1/2/3 与 step4b 文件、sina_sector.csv 同目录。
"""

import json
import datetime as dt
import akshare as ak
import pandas as pd

from agent_step1_toolloop import client, MODEL, TEMPERATURE, _clean
from agent_step3_deep_redflags import (
    tool_get_stock_quality, tool_get_stock_basics,
    get_fcf_3y, reverse_dcf, get_balance_items, red_flags_deep,
)

# 公告标题里的"监管关注"信号词
REG_FLAGS = ["问询函", "关注函", "监管函", "警示函", "立案", "处罚", "更正公告", "会计差错"]
# 互动易里值得看的财务质疑关键词
IRM_KEYS = ["应收", "存货", "商誉", "现金流", "减值", "关联交易", "担保", "质押", "业绩"]


def get_business_profile(code):
    code = str(code).zfill(6)
    try:
        p = ak.stock_profile_cninfo(symbol=code)
        r = p.iloc[0]
        return _clean({"code": code,
                       "主营业务": str(r.get("主营业务", ""))[:500],
                       "经营范围": str(r.get("经营范围", ""))[:500],
                       "所属行业": r.get("所属行业", "")})
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def scan_disclosures(code, years=2):
    code = str(code).zfill(6)
    end = dt.date.today()
    start = end.replace(year=end.year - years)
    try:
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code, market="沪深京",
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if "公告标题" not in df.columns:
        return {"error": "无公告标题列", "available": list(df.columns)}

    hits = []
    for _, r in df.iterrows():
        title = str(r["公告标题"])
        matched = [k for k in REG_FLAGS if k in title]
        if matched:
            hits.append({"标题": title, "时间": str(r.get("公告时间", ""))[:10],
                         "信号": matched, "链接": r.get("公告链接", "")})
    return _clean({"code": code, "近N年": years,
                   "公告总数": len(df), "监管关注类命中": len(hits),
                   "命中明细": hits[:15]})


def get_irm_qa(code, limit=12):
    code = str(code).zfill(6)
    try:
        df = ak.stock_irm_cninfo(symbol=code)
    except Exception as e:
        return {"error": f"stock_irm_cninfo: {type(e).__name__}: {e}"}
    cols = list(df.columns)
    # 字段发现:问题列 / 时间列
    q_col = next((c for c in cols if any(k in str(c) for k in ["问题", "提问", "内容"])), None)
    t_col = next((c for c in cols if any(k in str(c) for k in ["时间", "日期"])), None)
    if q_col is None:
        return {"error": "未找到提问列", "available_columns": cols}

    picked = []
    for _, r in df.iterrows():
        q = str(r[q_col])
        if any(k in q for k in IRM_KEYS):
            picked.append({"提问": q[:200],
                           "时间": str(r.get(t_col, ""))[:10] if t_col else ""})
        if len(picked) >= limit:
            break
    return _clean({"code": code, "字段": {"问题列": q_col, "时间列": t_col},
                   "财务质疑类提问数": len(picked), "明细": picked})


# ---- 完整工具集(9 个)----
DISPATCH = {
    "get_stock_quality": tool_get_stock_quality,
    "get_stock_basics": tool_get_stock_basics,
    "get_fcf_3y": get_fcf_3y,
    "reverse_dcf": reverse_dcf,
    "get_balance_items": get_balance_items,
    "red_flags_deep": red_flags_deep,
    "get_business_profile": get_business_profile,
    "scan_disclosures": scan_disclosures,
    "get_irm_qa": get_irm_qa,
}

TOOLS = [
    {"type": "function", "function": {"name": "get_stock_quality",
        "description": "3年质量因子:ROE/CFQ/负债率/红旗。数字来自财报,严禁估算。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "get_stock_basics",
        "description": "行业/PB/总市值/流通市值/最新价。估值用总市值。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "get_fcf_3y",
        "description": "近3年平均自由现金流(经营现金流−资本开支),元,含明细。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "reverse_dcf",
        "description": "反向DCF反推市场隐含增速。参数小数(r默认0.09,g_perp默认0.03,勿传9/3)。算术在工具内,严禁自算。null/error代表算不出,不可据此判高估低估。",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}, "r": {"type": "number"},
            "g_perp": {"type": "number"}, "years": {"type": "integer"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "get_balance_items",
        "description": "资产负债表精确科目近3年:应收/存货/商誉/货币资金/有息负债/归母净资产+营收。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "red_flags_deep",
        "description": "三表深度排雷:应收/存货增速vs营收背离、商誉占净资产、净有息负债及EV修正。数字来自财报。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "get_business_profile",
        "description": "公司主营业务/经营范围/所属行业(用于判断生意性质与护城河线索)。这是原文,摘要须基于原文。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "scan_disclosures",
        "description": "扫描近2年公告标题,筛出问询函/关注函/监管函/警示函/立案/处罚/会计差错等监管关注类(强定性风险信号),返回命中标题+巨潮链接。仅凭标题,全文需点链接核实。",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}, "years": {"type": "integer"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "get_irm_qa",
        "description": "互动易投资者问答中,涉及应收/存货/商誉/现金流/减值/关联交易/质押等财务质疑的提问,反映市场关注点与管理层坦诚度。",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["code"]}}},
]

SYSTEM_PROMPT = """你是一位严谨的A股价值投资分析师,产出供人决策的结构化论证,不替人做决定。
铁律:
1. 数字必须来自工具,严禁自行估算/编造。隐含增速必调reverse_dcf(r、g_perp用小数如0.09)。
   reverse_dcf返回null/error/note代表"算不出",绝不可据此判高估/低估。
2. 定性结论(护城河、管理层、风险)必须基于工具返回的原文/信号,引用时点明依据
   (如"据主营业务描述…""scan_disclosures命中一封问询函,标题为…")。严禁脱离文本编造定性判断。
3. scan_disclosures仅给标题,不要声称读过全文;命中监管关注类应作为定性红旗并提示用户点链接核实。
4. 完整简报分七节:【公司快照】【质量】【估值PB/行业】【反向DCF:市场隐含预期】
   【深度排雷:应收/存货/商誉/净负债】【定性:生意/护城河/监管信号/互动易关注】【综合:摊开假设,不下买卖结论】。
5. 【综合】节摊开关键假设、安全边际线索、主要风险,但不给买入/卖出/持有结论,不给目标价。
"""


import time
import random


def chat_with_retry(retries=5, base=3.0, **kwargs):
    """GLM 调用退避重试:RemoteProtocolError/连接断开多为间歇性,重试即可。"""
    last = None
    for i in range(1, retries + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            last = e
            if i == retries:
                break
            delay = base * (2 ** (i - 1)) + random.uniform(0, 2)
            print(f"    [GLM重试] 第{i}次失败({type(e).__name__}),{delay:.1f}s 后重试...")
            time.sleep(delay)
    raise last


def run_agent(code, max_steps=14):
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请为 {code} 生成完整价值投资分析简报(含定性尽调)。"}]
    for _ in range(max_steps):
        resp = chat_with_retry(
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
            print(f"  [工具] {tc.function.name}({args}) → {json.dumps(result, ensure_ascii=False)[:240]}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    return "(达到最大步数)"


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"=== Agent完整尽调(含文本检索):{code} ===\n")
    print(run_agent(code))
    print("\n>>> 验收:1) get_irm_qa字段发现成没成(失败回传列名);"
          "\n    2) 定性结论是否都带原文依据、没脱文编造;"
          "\n    3) scan_disclosures命中的问询/关注函有没有被当定性红旗+给链接;"
          "\n    4) 茅台应较干净(无监管关注函);换一只被问询过的票看红旗能否亮。")
