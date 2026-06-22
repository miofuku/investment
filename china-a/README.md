# A股价值投资系统 — 项目说明

一套面向 A股、**量化做广度筛选 + LLM(GLM)做价值深度尽调**的研究系统。
数据全部用免费 AKShare,数字全部在确定性 Python 里算,LLM 只做编排与定性表达、不做算术、不下买卖结论。

> **一句话定位:这是一个纪律严明的"研究助手",不是一个已验证的"交易策略"。**
> 它帮你高效缩小范围、系统排雷、逼你看清假设;每个买卖决定仍需你自己的判断兜底。
> (详见文末「能力边界」。)

---

## 0. 环境与前置

- 依赖:`pip install openai akshare pandas`
- 环境变量:`export ZAI_API_KEY=...`(GLM,OpenAI 兼容接口)
- 模型:`glm-5.1`,temperature 0.1
- **网络说明:东方财富(eastmoney)接口在本机不稳(ConnectionReset);可用源为新浪/巨潮/同花顺/交易所直连。** 大宗交易工具走东财,可能间歇失败(已做诚实降级,失败不编造)。

---

## 1. 三层架构

```
全市场洗池 → 行业内神奇公式打分 → 干净母清单         ← 量化广度
                                      ↓
              传统行业筛选(A偏便宜 / B偏稳健 / 交集)   ← 候选缩小
                                      ↓
              13工具 GLM agent 逐只深挖 → 九节简报      ← 价值深度
                                      ↓
                            你的判断 = 最终决策
```

---

## 2. 文件清单

### 2.1 核心程序(必留)

| 文件 | 作用 |
|---|---|
| `a_share_universe_v3.py` | 洗池子:全A股→剔ST/次新/银行,产 `universe_normal.csv` / `universe_financials.csv` |
| `step4b_market_factors.py` | **数据函数库**,被 step4c 和所有 agent import(删了全盘崩) |
| `step4c_magic_formula.py` | 全市场行业内神奇公式打分,产母清单;含金融股隔离 |
| `check_masterlist.py` | 母清单体检:抽出榜首/带红旗/超低PB等可疑票供人工核 |
| `screen_traditional.py` | 传统行业选股:A偏便宜 / B偏稳健 / 交集,生成深挖命令 |

### 2.2 Agent(链式 import,全留)

`agent_step1` → `step2` → `step3` → `step4` → `step5` → `step6` → `step7` → `step8`,
**后一个 import 前一个的全部工具。日常只跑 `agent_step8_block_trade.py`(它含全部13工具)**,但前面 7 个都不能删。

| 文件 | 新增工具 |
|---|---|
| `agent_step1_toolloop.py` | 质量、基础信息、tool-loop |
| `agent_step2_reverse_dcf.py` | FCF、反向DCF(含入参归一化与边界) |
| `agent_step3_deep_redflags.py` | 三表科目、深度排雷(应收/存货/商誉/净负债) |
| `agent_step4_text_qualitative.py` | 主营护城河、公告问询函扫描、互动易;GLM重试 |
| `agent_step5_capital_flow.py` | 资金面:质押/增减持/解禁/股东人数 |
| `agent_step6_dividend.py` | 分红历史(连续年数/支付率/股息率) |
| `agent_step7_roic.py` | ROIC(剔杠杆失真,含失真护栏) |
| `agent_step8_block_trade.py` | 大宗交易(折价=出货信号)— **最新,含全部13工具+run_agent** |

### 2.3 缓存(必留,删了要重拉数小时)

| 文件 | 内容 | 何时删 |
|---|---|---|
| `ths_quality_cache.csv` | 全市场财务质量(最金贵,数千只) | 仅刷新财报时 |
| `sina_sector.csv` | 行情+行业+PB+市值 | 刷新行情时 |

### 2.4 产出

| 文件 | 内容 |
|---|---|
| `factor_all_market_magic.csv` | **母清单**(选票入口,~3533只正盈利) |
| `factor_all_market_magic_excluded.csv` | 负盈利回避区(~1243只) |
| `factor_financials.csv` | 分流的金融股(口径不适用) |
| `factor_trad_value.csv` / `factor_trad_stable.csv` | 传统行业 A/B 候选 |

### 2.5 可删(一次性探针 + 旧版本)

- 探针:`probe*.py`、`probe2/3/4/5/5b/6/7/8`、`step2a_field_discovery.py`、`step4a_industry_map.py`、`diagnose_wuliangye.py`
- 旧版本:`a_share_universe.py` / `_v2.py`、`step3_factor_engine.py` / `step3b_*.py`
- 中间CSV:`factor_baijiu*` / `factor_房地产*` / `factor_smoke*`

> **删除前先确认** `python step4c_magic_formula.py` 与 `python agent_step8_block_trade.py 600519` 都能跑通。
> 建议把"可删"挪进 `archive/` 而非硬删,确认一周无碍再清。

---

## 3. 常用命令

### 刷新母清单
```bash
# 刷行情(PB/市值,每周):删行情缓存,财务缓存复用→几十秒
rm sina_sector.csv
# step4c 顶部:RUN_ALL=True, SMOKE=False
python step4c_magic_formula.py

# 刷财报(ROE/现金流,每季财报季后):删财务缓存→全市场重拉~1-2小时
rm ths_quality_cache.csv sina_sector.csv
python step4c_magic_formula.py
```

### 选票
```bash
python check_masterlist.py            # 母清单整体体检
python screen_traditional.py          # 传统行业 A/B/交集 候选
```

### 单只深挖(产九节简报)
```bash
python agent_step8_block_trade.py 601006    # 换成任意6位代码
```

---

## 4. 十三个工具(agent 深挖时调用)

| # | 工具 | 客观信号 |
|---|---|---|
| 1 | get_stock_quality | 3年ROE / CFQ / 负债率 / 红旗 |
| 2 | get_stock_basics | 行业 / PB / 市值 / 价 |
| 3 | get_fcf_3y | 近3年平均自由现金流 |
| 4 | reverse_dcf | 市场隐含增速(负FCF→不适用,不判高估低估) |
| 5 | get_balance_items | 应收/存货/商誉/货币资金/有息负债/净资产 |
| 6 | red_flags_deep | 应收存货背离、商誉占比、净有息负债、EV修正 |
| 7 | get_business_profile | 主营/经营范围(护城河线索) |
| 8 | scan_disclosures | 近2年公告筛问询/关注/监管/警示函(给标题+链接) |
| 9 | get_irm_qa | 互动易财务质疑类问答 |
| 10 | capital_flow_signals | 质押比例 / 增减持 / 解禁 / 股东人数 |
| 11 | get_dividend_history | 连续分红年数 / 平均支付率 / 股息率 |
| 12 | get_roic_3y | ROIC(剔杠杆失真,失真时诚实标注不可信) |
| 13 | get_block_trades | 近3月大宗笔数 / 折溢率 / 大幅折价 |

**九节简报:** 公司快照 · 质量(ROE+ROIC) · 估值PB · 反向DCF · 深度排雷 · 定性(护城河/监管/互动易) · 资金面(质押/增减持/解禁/股东数/大宗) · 分红 · 综合(摊开假设,不下买卖结论)。

---

## 5. 贯穿全程的设计纪律

1. **数字全在确定性 Python 算**,LLM 只编排+定性,绝不做算术或编造数字。
2. **先探针后写**:每个新接口先 probe 签名/字段,从不凭记忆猜。
3. **已知答案的干净票验证**:用茅台等"心里有数"的票验解析,再放大到全市场。
4. **比值型计算加边界**:反向DCF的r/g、ROIC的税率都做钳制/回退,异常分母不失控。
5. **工具诚实标注边界**:算不准就返回 null+警示(ROIC遇重整、reverseDCF遇负FCF、金融股口径不适用),不硬给可信外观的错数。
6. **资金面/宏观只报客观数据,绝不编码"意图"叙事**:工具说"质押63%/折价9%",不说"国家队护盘"。让叙事决定看哪里,让数据决定信什么。
7. **不下买卖结论、不给目标价**:摊开假设与风险,决策留给人。

### 压测记录(每个都被极端票验过)
- 五粮液:年报筛选 `endswith("1231")` 对带横线日期失效 → 改为去非数字再判,并全面改3年口径
- 茅台:NaN 真值性 bug(`NaN or 0` 返回 NaN)→ `_z()` 兜底
- 反向DCF:模型传 r=9(本意9%)→ 自动归一化+边界,且 null≠高估低估
- 正邦:虚高ROE 66%(重整压低净资产)→ ROIC 揭穿;ROIC遇重整一次性利得 → 失真护栏返回 null
- 中国建筑:PB 0.38 价值陷阱 → EV修正(真实EV是市值3.8倍)+应收占净资产85% 拆穿
- 江苏金租:金融股口径不适用 → 母清单隔离 + agent 自我识别
- 大秦铁路:经典"高股息现金奶牛"故事 → ROIC腰斩+资本开支暴增+分红砍71% 三信号证伪

---

## 6. 能力边界(务必牢记)

**能做:** 全市场高效缩小范围、系统化排雷、把"便宜/质量/现金流/分红/资金面"变成可查的客观数字、用定性尽调补足财报外信号、强制摊开估值假设。

**不能做(及原因):**
- **不能回测、不能算策略收益**。免费数据无 point-in-time(前视偏差)、无退市股(幸存者偏差),任何回测都不可信。这是全程**刻意不做回测**的原因。
- **不替你决策**。它给信号和假设,不给买卖建议。
- **不处理极端财务失真到"给可信数"**(如破产重整股),只会诚实标注"算不准"。
- **大宗交易等东财源可能间歇不可用**,有就看、没有不依赖。

**下一个台阶(质变,非补丁):** 换付费 point-in-time 数据(聚宽/Wind,含退市股)→ 才谈得上严肃回测、因子验证、真实超额收益。在那之前,把它当研究助手用。

---

## 7. 待办 / 可改进(攒够真实案例再动,勿过度工程)

- 强周期股单年 ROE/PB 失真:agent 深挖已能识别,暂不在母清单层加"周期标记"
- 个别票数据为空的容错(如茅台无互动易记录):实战遇到集中收集后统一加
- `agent_step7_roic.py` 返回里"口径"字段文字仍写旧口径(数值已是年均口径,仅文案待同步)
- 龙虎榜:已评估,因偏短线、与价值投资取向冲突,**决定不做**
