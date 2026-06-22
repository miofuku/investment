# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 深度层 步骤7:ROIC(投入资本回报率)
================================================================
为何加:ROE 会被杠杆/异常净资产污染(正邦重整后虚高66%就是例子)。
ROIC 看"每一块投入资本(不分股东还是借的)赚回多少",剔除杠杆失真,
更接近生意真实质地,是格林布拉特原版质量因子。与3年ROE并排交叉验证。

口径(融资端,字段全部现成已验证):
  EBIT      = 利润总额 + 利息费用
  税率      = clip(所得税/利润总额, 0, 30%);越界(负税率/畸高)回退法定25%
              —— 处理退税股/微利股的税率失控(讯飞-46%~-121%就是坑)
  NOPAT     = EBIT × (1 - 税率)
  投入资本   = 有息负债(短期借款+长期借款+应付债券+一年内到期非流动负债)
              + 归母净资产 − 货币资金
  ROIC      = NOPAT / 投入资本,取近3年平均

挂进 agent 当第 12 个工具。
"""

import json
import akshare as ak
import pandas as pd

from agent_step1_toolloop import client, MODEL, TEMPERATURE, _clean
from agent_step2_reverse_dcf import _sina_code, _num
from agent_step6_dividend import (
    DISPATCH as BASE_DISPATCH, TOOLS as BASE_TOOLS, chat_with_retry,
)

STATUTORY_TAX = 0.25      # 法定企业所得税率(税率失控时回退)


def _annual3(df):
    df = df.copy()
    df["报告日"] = df["报告日"].astype(str)
    key = df["报告日"].str.replace(r"\D", "", regex=True)
    return df[key.str.endswith("1231")].sort_values("报告日").tail(3)


def _z(v):
    return 0.0 if (v is None or (isinstance(v, float) and pd.isna(v))) else float(v)


def get_roic_3y(code):
    code = str(code).zfill(6)
    try:
        il = ak.stock_financial_report_sina(stock=_sina_code(code), symbol="利润表")
        bs = ak.stock_financial_report_sina(stock=_sina_code(code), symbol="资产负债表")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    il3, bs3 = _annual3(il), _annual3(bs)
    if il3.empty or bs3.empty:
        return {"error": "无足够年报数据"}

    bs_by = {str(r["报告日"])[:10]: r for _, r in bs3.iterrows()}
    detail = []
    pairs = []   # 仅 invested>0 的年份的 (NOPAT, 投入资本),保证分子分母取同一组年份
    for _, r in il3.iterrows():
        period = str(r["报告日"])[:10]
        利润总额 = _num(r.get("利润总额"))
        利息 = _num(r.get("利息费用"))
        利息 = 利息 if not pd.isna(利息) else _num(r.get("利息支出"))
        利息 = _z(利息)
        所得税 = _z(_num(r.get("所得税费用")))
        ebit = 利润总额 + 利息

        # 税率钳制:有效税率落在[10%,30%]才采用;越界(负/畸低/畸高)回退法定25%。
        # 下界用10%:正常盈利企业税率极少低于此,过低多因亏损弥补/重整/大额优惠,不可信。
        raw_tax = 所得税 / 利润总额 if 利润总额 and 利润总额 > 0 else None
        tax = raw_tax if (raw_tax is not None and 0.10 <= raw_tax <= 0.30) else STATUTORY_TAX
        nopat = ebit * (1 - tax)

        b = bs_by.get(period)
        if b is None:
            continue
        int_debt = sum(_z(_num(b.get(k))) for k in
                       ["短期借款", "长期借款", "应付债券", "一年内到期的非流动负债"])
        equity = _z(_num(b.get("归属于母公司股东权益合计")))
        cash = _z(_num(b.get("货币资金")))
        invested = int_debt + equity - cash

        if invested and invested > 0:
            pairs.append((nopat, invested))
        # 单年ROIC仅供明细展示(可能因重整等失真),均值不取单年平均
        single = (nopat / invested * 100) if invested and invested > 0 else None
        detail.append({
            "报告期": period, "EBIT": ebit, "采用税率": round(tax, 3),
            "NOPAT": nopat, "投入资本": invested,
            "单年ROIC_pct": round(single, 2) if single is not None else None,
        })

    # 年均口径:平均NOPAT ÷ 平均投入资本(分子分母均为"一年",且取同一组 invested>0 的年份)
    # 注1:不可用 Σ3年NOPAT÷平均投入资本——分子三年之和、分母一年,量纲不匹配会虚高约3倍
    # 注2:分子分母必须用同一年份集合,否则混入 invested≤0 的年份(净现金等)会失真
    if pairs:
        avg_nopat = sum(p[0] for p in pairs) / len(pairs)
        avg_invested = sum(p[1] for p in pairs) / len(pairs)
        roic_3y = round(avg_nopat / avg_invested * 100, 2)
    else:
        roic_3y = None

    # 诚实护栏:单年ROIC波动极大→疑似重整/一次性项目污染EBIT,合计值也不可信
    singles = [d["单年ROIC_pct"] for d in detail if d["单年ROIC_pct"] is not None]
    可信 = True
    警示 = None
    if len(singles) >= 2 and (max(singles) - min(singles)) > 40:
        可信 = False
        警示 = ("单年ROIC波动极大(最高%.1f%% 最低%.1f%%),疑似重整/资产重估/债务豁免等"
                "一次性项目污染EBIT,ROIC失真,本指标不可靠,建议人工核实利润构成"
                % (max(singles), min(singles)))

    return _clean({
        "code": code,
        "ROIC_3y_pct": roic_3y if 可信 else None,
        "可信": 可信,
        "失真警示": 警示,
        "逐年明细": detail,
        "口径": "年均口径:平均NOPAT÷平均投入资本;EBIT=利润总额+利息;税率取[10%,30%]否则回退25%;投入资本=有息负债+归母净资产−货币资金",
        "说明": "与3年ROE对照:若ROE远高于ROIC,高ROE多由杠杆/异常净资产驱动(如重整),需警惕。单年波动极大时本指标失真(见失真警示)",
    })


# ---- 工具集:step6 的 11 个 + 1 = 12 个 ----
DISPATCH = dict(BASE_DISPATCH)
DISPATCH["get_roic_3y"] = get_roic_3y

TOOLS = list(BASE_TOOLS) + [
    {"type": "function", "function": {"name": "get_roic_3y",
        "description": "近3年平均ROIC(投入资本回报率,剔除杠杆失真)。与3年ROE对照:ROE远高于ROIC说明高回报靠杠杆/异常净资产撑起(如重整股),质量打折。注意返回的'可信'字段:若为false(单年波动极大、疑似重整/一次性项目污染),则ROIC_3y_pct为null,应转述'失真警示'而非使用数值。数字来自财报,严禁估算。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
]

SYSTEM_PROMPT = """你是一位严谨的A股价值投资分析师,产出供人决策的结构化论证,不替人做决定。
铁律:
1. 数字必须来自工具,严禁估算/编造。隐含增速必调reverse_dcf(小数);null/error=算不出,不可据此判高估低估。
2. 定性结论须基于工具返回原文/信号并点明依据;资金面只陈述客观事实,严禁解读资金"意图"/宏观叙事。
3. 质量节须同时呈现 3年ROE 与 3年ROIC 并对照:若 ROE 显著高于 ROIC,指出高回报可能由杠杆或异常净资产(如重整、微利)驱动,对"高ROE"打折看待。若 get_roic_3y 返回'可信'=false,不得使用其ROIC数值,须如实转述'失真警示'(疑似重整/一次性项目污染),说明ROIC在此票失真、ROE的高值更需警惕。
4. 分红:常年稳定分红+合理支付率是现金流真实佐证;但高分红≠便宜。
5. 简报分九节:【公司快照】【质量(ROE+ROIC对照)】【估值PB/行业】【反向DCF】【深度排雷】【定性:生意/护城河/监管/互动易】【资金面】【分红】【综合:摊开假设,不下买卖结论】。
6. 不下买卖结论,不给目标价。每个数字对应工具返回。
"""


def run_agent(code, max_steps=20):
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请为 {code} 生成完整价值投资分析简报。"}]
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
    code = sys.argv[1] if len(sys.argv) > 1 else "002157"   # 默认正邦,验ROIC能否拉平虚高ROE
    print(f"=== ROIC工具单测:{code} ===")
    print(json.dumps(get_roic_3y(code), ensure_ascii=False, indent=2))
    print(f"\n>>> 对照:正邦3年ROE=66%,看ROIC是否被拉到合理水平(说明那66%是重整虚高)")
