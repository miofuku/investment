# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 深度层 步骤1:工具注册 + 单只票管道验证
================================================================
目的:只验证 GLM-5.1 的 function-calling 管道 ——
  能否稳定【调对工具、复述对数字、不胡编】。
不做 DCF、不读年报、不下买卖结论。这是整个 agent 的地基。

工具(复用前面写好的确定性函数):
  · get_stock_quality(code) → quality_of:3年ROE / CFQ / 负债率 / 红旗
  · get_stock_basics(code)  → 行情快照:行业 / PB / 市值 / 价格

前置:
  pip install openai akshare pandas
  export ZAI_API_KEY=你的key
  需有 step4b_market_factors.py 与 sina_sector.csv(前面已生成)

依赖文件同目录。
"""

import os
import json
import math
import pandas as pd
from openai import OpenAI

# 复用我们自己的确定性函数 / 缓存
from step4b_market_factors import quality_of, QUALITY_CACHE, SECTOR_CACHE


def _load_dotenv():
    """读取本脚本同目录的 .env(KEY=VALUE 形式)。已存在的环境变量优先,不覆盖。
    零依赖,无需 pip install python-dotenv。支持 # 注释、export 前缀与引号。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)


_load_dotenv()

# ---- GLM-5.1(智谱 Coding Plan,OpenAI 兼容)----
_api_key = os.environ.get("ZAI_API_KEY")
if not _api_key:
    raise SystemExit(
        "未找到 ZAI_API_KEY:请在 china-a/.env 写入 ZAI_API_KEY=你的key,"
        "或在终端 export ZAI_API_KEY=你的key 后再运行。")
client = OpenAI(
    api_key=_api_key,
    base_url="https://api.z.ai/api/coding/paas/v4",
)
MODEL = "glm-5.1"          # 如端点要求别的名字,改这里即可
TEMPERATURE = 0.1          # 低温:要稳定,不要随机


# ----------------------------------------------------------------------
# 工具的真实实现(数字全在这里算,模型不碰)
# ----------------------------------------------------------------------
def _clean(d):
    """把 numpy / NaN 转成 JSON 安全的值。"""
    out = {}
    for k, v in d.items():
        if isinstance(v, float) and math.isnan(v):
            out[k] = None
        elif hasattr(v, "item"):
            out[k] = v.item()
        else:
            out[k] = v
    return out


def tool_get_stock_quality(code):
    code = str(code).zfill(6)
    # 优先读缓存,没有再现拉
    if os.path.exists(QUALITY_CACHE):
        c = pd.read_csv(QUALITY_CACHE, dtype={"code": str})
        c["code"] = c["code"].str.zfill(6)
        hit = c[c["code"] == code]
        if len(hit):
            return _clean(hit.iloc[0].to_dict())
    q = quality_of(code)        # 现拉一次同花顺
    q["code"] = code
    return _clean(q)


def tool_get_stock_basics(code):
    code = str(code).zfill(6)
    if not os.path.exists(SECTOR_CACHE):
        return {"error": f"缺少 {SECTOR_CACHE},请先跑 step4b 生成行业/PB缓存"}
    df = pd.read_csv(SECTOR_CACHE, dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)
    hit = df[df["code"] == code]
    if not len(hit):
        return {"error": f"{code} 不在行情快照中(可能停牌/北交所)"}
    r = hit.iloc[0]
    return _clean({"code": code, "name": r.get("name"), "行业": r.get("行业"),
                   "PB": r.get("pb"),
                   "总市值万元": r.get("mktcap"), "流通市值万元": r.get("nmc"),
                   "最新价": r.get("trade")})


DISPATCH = {
    "get_stock_quality": tool_get_stock_quality,
    "get_stock_basics": tool_get_stock_basics,
}

TOOLS = [
    {"type": "function", "function": {
        "name": "get_stock_quality",
        "description": "获取某A股的3年质量因子:3年平均ROE(%)、3年现金流质量CFQ(经营现金流/净利润)、最新资产负债率(%)、风险红旗。数字均来自财报,严禁自行估算或编造。",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "6位股票代码,如 600519"}},
            "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "get_stock_basics",
        "description": "获取某A股的所属行业、市净率PB、总市值、流通市值、最新价(来自行情快照)。注意:总市值与流通市值口径不同,做估值/EV/市值相关计算时用『总市值万元』。",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "6位股票代码"}},
            "required": ["code"]}}},
]

SYSTEM_PROMPT = """你是一位严谨的A股价值投资分析师。
规则(必须严格遵守):
1. 所有财务与估值数字,必须通过调用工具获得;严禁自行估算、推测或编造任何数字。
2. 如果工具返回 error 或某字段为 null,如实说明"数据缺失",不要填补。
3. 本轮任务只做"结构化基本面简报",分四节:【公司快照】【质量(ROE/现金流/负债)】【估值(PB/行业)】【风险红旗】。
4. 不做DCF估值,不下"买入/卖出/持有"任何结论,不给目标价。
5. 简报里引用的每个数字,都应能对应到某次工具返回值。
"""


# ----------------------------------------------------------------------
# 最简 tool-loop:模型要调工具 → 本地执行真函数 → 回灌 → 模型收尾
# ----------------------------------------------------------------------
def run_agent(code, max_steps=6):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"请为股票 {code} 生成结构化基本面简报。"},
    ]
    for step in range(max_steps):
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
            print(f"  [工具] {name}({args}) → {json.dumps(result, ensure_ascii=False)}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
    return "(达到最大步数仍未收尾)"


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"   # 默认茅台
    print(f"=== Agent管道验证:{code} ===\n")
    brief = run_agent(code)
    print("\n=== 模型简报 ===\n")
    print(brief)
    print("\n>>> 验收:简报里的 ROE/CFQ/负债率/PB/行业 是否与上面[工具]返回值一致?"
          "\n    有没有出现工具没返回过的数字(=胡编)?有没有偷偷下买卖结论?")
