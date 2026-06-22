# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 深度层 步骤8:大宗交易(资金面补充)
================================================================
信号价值:大宗交易大幅【折价】成交,往往是大股东/机构急于出货
(愿意低于市价甩卖);溢价则相对中性。只报客观事实,不解读意图。

工具:get_block_trades(code) —— 东财 stock_dzjy_mrtj(全市场按日期,按code筛)
  汇总近3月:大宗笔数、平均折溢率、大幅折价(<-5%)笔数、合计成交额。
  注意:该接口走东财,可能间歇失败;失败如实返回 error,不编造。

挂进 agent 当第 13 个工具(资金面补充)。
"""

import json
import datetime as dt
import akshare as ak
import pandas as pd

from agent_step1_toolloop import client, MODEL, TEMPERATURE, _clean
from agent_step7_roic import (
    DISPATCH as BASE_DISPATCH, TOOLS as BASE_TOOLS, chat_with_retry,
)

_DZJY_CACHE = {}        # 近3月全市场大宗,当次进程缓存(接口较重)


def _load_block_3m():
    key = "3m"
    if key not in _DZJY_CACHE:
        end = dt.date.today()
        start = end - dt.timedelta(days=92)
        try:
            df = ak.stock_dzjy_mrtj(start_date=start.strftime("%Y%m%d"),
                                    end_date=end.strftime("%Y%m%d"))
            df["证券代码"] = df["证券代码"].astype(str).str.zfill(6)
            _DZJY_CACHE[key] = df
        except Exception as e:
            _DZJY_CACHE[key] = e        # 缓存异常,避免反复重试
    return _DZJY_CACHE[key]


def get_block_trades(code):
    code = str(code).zfill(6)
    data = _load_block_3m()
    if isinstance(data, Exception):
        return {"error": f"大宗交易接口失败(东财间歇性):{type(data).__name__}",
                "note": "可稍后重试;失败不代表无大宗交易"}
    sub = data[data["证券代码"] == code]
    if sub.empty:
        return _clean({"code": code, "近3月大宗笔数": 0,
                       "note": "近3个月无大宗交易记录"})

    rates = pd.to_numeric(sub["折溢率"], errors="coerce").dropna()
    big_discount = sub[pd.to_numeric(sub["折溢率"], errors="coerce") < -0.05]
    recent = sub.sort_values("交易日期").tail(6)
    detail = [{"日期": str(r["交易日期"])[:10],
               "成交价": _safe(r.get("成交价")), "收盘价": _safe(r.get("收盘价")),
               "折溢率": _safe(r.get("折溢率")),
               "成交额万元": round(_safe(r.get("成交总额"), 0), 1)}
              for _, r in recent.iterrows()]

    flags = []
    if len(big_discount) > 0:
        flags.append(f"近3月{len(big_discount)}笔大幅折价(<-5%),疑似出货")
    return _clean({
        "code": code,
        "近3月大宗笔数": len(sub),
        "平均折溢率": round(float(rates.mean()), 4) if len(rates) else None,
        "大幅折价笔数": len(big_discount),
        "明细": detail,
        "资金面红旗": flags,
        "口径说明": "折溢率<0=折价(成交价低于收盘),大幅折价多为急于出货;仅客观陈述,不解读意图",
    })


def _safe(x, default=None):
    try:
        v = float(x)
        return v if v == v else default      # NaN→default
    except (TypeError, ValueError):
        return default


# ---- 工具集:step7 的 12 个 + 1 = 13 个 ----
DISPATCH = dict(BASE_DISPATCH)
DISPATCH["get_block_trades"] = get_block_trades

TOOLS = list(BASE_TOOLS) + [
    {"type": "function", "function": {"name": "get_block_trades",
        "description": "近3个月大宗交易:笔数、平均折溢率、大幅折价(<-5%)笔数。大宗大幅折价成交常为大股东/机构急于出货的客观信号。只陈述事实,不解读资金'意图'。接口走东财可能间歇失败,失败时如实说明、不可臆测。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
]

SYSTEM_PROMPT = """你是一位严谨的A股价值投资分析师,产出供人决策的结构化论证,不替人做决定。

【事实与解读的区分——本提示的核心】
你要做"基于数据的商业解读",但绝不"编造"。两者区别:
· 编造 = 给一个数据无法支撑的判断(如"机构在出逃""国家队护盘")—— 严禁。
· 解读 = 把多个客观数据串起来,还原背后的商业逻辑(如"资本开支三年翻倍+ROIC逐年下滑+
  机构持续折价减持,合起来指向:这是价格战中烧钱抢份额、规模扩张但效率下降的格局")—— 鼓励且必须做。
解读的终点是把"商业图景+核心假设"摊给用户,不是替用户得出"所以该买/该卖"。

铁律:
1. 数字必须来自工具,严禁估算/编造。隐含增速必调reverse_dcf(小数);null/error=算不出,不可据此判高估低估。
2. 定性结论须基于工具返回原文/信号并点明依据。
3. 资金面(质押/增减持/解禁/股东数/大宗交易)只陈述客观事实,严禁解读为资金"意图"/"护盘"/"出货意图"等叙事;大幅折价大宗可作客观风险提示,但不臆测原因。(注:此条约束的是"猜测资金背后的意图";把减持/折价作为"商业承压"的佐证之一去串联是允许的,只要不编造意图。)
4. 质量节同时呈现3年ROE与3年ROIC对照;若get_roic_3y返回'可信'=false,转述失真警示、不用其数值。
5. 分红:常年稳定分红是现金流真实佐证;高分红≠便宜。
6. 简报分十节:【公司快照】【质量(ROE+ROIC)】【估值PB/行业】【反向DCF】【深度排雷】【定性:生意/护城河/监管/互动易】【资金面:质押/增减持/解禁/股东数/大宗】【分红】【深层解读】【综合:摊开假设,不下买卖结论】。
   前八节保持简洁、客观报数据;深度洞察集中放在【深层解读】节。
7. 【深层解读】节(本简报最重要的增值部分),做三件事:
   (a) 串联而非罗列:挑出2-4个"单看平淡、合看有意义"的指标组合,讲清它们共同指向的商业事实
       (例:ROIC下滑+资本开支上升+净资产缩水→该生意正在变重且回报变差)。
   (b) 行业常识锚点:用一两句说清"这门生意靠什么赚钱、最怕什么"(快递看单票成本与价格战;
       油气看油价中枢;航空货运看运价周期;轻资产物流看周转与网络;周期股看所处周期位置),
       让数据有解读的坐标系。
   (c) 翻译反直觉数据:凡需财务知识才能读懂含义的,每个都展开成一句人话解释。例如:
       "ROIC>ROE→高回报不靠杠杆,且常因大量净现金拉低了ROE,真实经营效率更高";
       "EV是市值数倍→看似低PB其实不便宜,因为有大量净有息负债";
       "股利支付率>100%→分红超过当年利润,在吃老本,不可持续";
       "营收降但应收不增→没有靠放宽信用粉饰收入,回款健康"。
8. 不下买卖结论,不给目标价。每个数字对应工具返回。【深层解读】可以有倾向性地指出"风险更值得警惕"
   或"安全边际线索",但仍以摊开假设收尾,把最终判断留给用户。
"""


def run_agent(code, max_steps=22):
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
            print(f"  [工具] {tc.function.name}({args}) → {json.dumps(result, ensure_ascii=False)[:200]}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    return "(达到最大步数)"


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "002230"
    print(f"=== 大宗交易工具单测:{code} ===")
    print(json.dumps(get_block_trades(code), ensure_ascii=False, indent=2))
    print(f"\n=== 完整简报(13工具九节):{code} ===\n")
    print(run_agent(code))
