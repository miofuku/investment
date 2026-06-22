# A股价值投资系统 — 项目说明

一套面向 A股、**量化做广度筛选 + LLM(GLM)做价值深度尽调**的研究系统。
数据全部用免费 AKShare,数字全部在确定性 Python 里算,LLM 只做编排与定性表达、不做算术、不下买卖结论。

> **一句话定位:这是一个纪律严明的"研究助手",不是一个已验证的"交易策略"。**
> 它帮你高效缩小范围、系统排雷、逼你看清假设;每个买卖决定仍需你自己的判断兜底。
> (详见文末「能力边界」。)

---

## 0. 环境与前置

- 依赖:`pip install openai akshare pandas`;发布到 Sheets/网页另需 `pip install gspread`
- 密钥:在 `china-a/.env` 写 `ZAI_API_KEY=你的key`(脚本启动自动加载,无需手动 `export`;已 export 的同名变量优先)。`.env` 已被 `.gitignore` 忽略。
- 模型:`glm-5.1`,temperature 0.1
- 发布/分享相关前置见 §4。
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
| `a_share_universe.py` | 洗池子:全A股→剔ST/次新/银行,产 `universe_normal.csv` / `universe_financials.csv` |
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

### 2.6 发布层(Web 前端 + 部署,见 §4)

| 文件 | 作用 |
|---|---|
| `push_to_sheets.py` | 本地产出 → 写 Google Sheets + 生成 `data.js`;`--all` 刷清单、`--report` 单只入库、`--process-requests` 处理看票申请、`--datajs` 仅重建数据包 |
| `index.html` | 纯静态前端(母清单 / 估值散点 / 传统候选 / 深挖简报 + 看票申请框),只读 `data.js` |
| `data.js` | 自动生成的数据包(`window.SHEET_DATA`),与 `index.html` 一起部署;**勿手改** |
| `build_and_deploy.sh` | 一键:生成数据 → 只把 `index.html`+`data.js` 部署到 Cloudflare Pages |
| `apps_script_requests.gs` | Google Apps Script(贴进 Sheet 的 Apps Script 部署):接收看票申请,写入 `requests` 表 |
| `service_account.json` | gspread 服务账号凭证(本地,**已 gitignore,绝不上传**) |

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

## 4. 发布与分享(Web 前端 + Cloudflare)

研究产出通过一个**纯静态网页**分享给少数熟人:无后端、无数据库、无 API。Agent 永远只在本地跑。

### 4.1 数据怎么流到网页
```
本地 CSV / 简报 ──push_to_sheets.py──┬─→ Google Sheets(免费、可视的数据备份)
                                     └─→ data.js(window.SHEET_DATA = {…})
                                                │  index.html 直接读(无 fetch / 无 CORS)
                                                │
                              build_and_deploy.sh ─→ Cloudflare Pages(CDN)
                                                │
                              Cloudflare Access ─→ 仅白名单邮箱可访问
```
- `data.js` 把母清单 / 候选 / 金融股 / 简报全序列化进 `window.SHEET_DATA`,网页同步引入,彻底绕开 CORS。
- 部署**只上传 `index.html` + `data.js`**(经临时目录暂存),绝不上传 `.env` / `service_account.json` / CSV——否则密钥会变成可公开下载的文件。

### 4.2 一次性前置
1. **Google Sheets**:`pip install gspread`;在 Drive 新建表格,名字与 `push_to_sheets.py` 的 `SPREADSHEET_NAME`(默认「A股价值投资系统」)一致;把 `service_account.json` 里的服务账号邮箱加为该表格的「编辑者」。
2. **Cloudflare**:`npx wrangler login`(一次);把 `build_and_deploy.sh` 顶部 `PROJECT_NAME` 改成你的 Pages 项目名(或设 `CF_PAGES_PROJECT`)。项目不存在时先 `npx wrangler pages project create <名字>`。
3. **看票申请**:把 `apps_script_requests.gs` 贴进 Sheet 的 Apps Script 并部署为 Web App,把得到的 URL 写进 `china-a/.env` 的 `REQUEST_ENDPOINT=...`(`push_to_sheets.py` 会在生成 `data.js` 时注入;它不是密钥,但放 .env 便于集中管理、不写死在 `index.html`)。
4. **访问控制**:配 Cloudflare Access(见 §4.5)。

### 4.3 部署命令
```bash
./build_and_deploy.sh --all              # 刷母清单+候选+金融股 → 写 Sheets + data.js → 部署
./build_and_deploy.sh --report 600519    # 单只简报入库 → 更新 data.js → 部署(可跟多个代码)
./build_and_deploy.sh --process-requests # 处理用户提交的看票申请(§4.4)→ 部署
./build_and_deploy.sh --all --no-deploy  # 只本地生成,不部署(本地打开 index.html 预览)
```
> ⚠️ `--all` 只读现成 CSV,**不重算因子**。要刷新底层数字,先按 §3「刷新母清单」跑 `step4c` 等,再 `--all`。

### 4.4 让用户申请看某只票
- 「深挖简报」页有「想看哪只票?」输入框 → 提交到 Apps Script(`apps_script_requests.gs`)→ 追加到 Sheet 的 `requests` 表(服务端做 6 位校验 + pending 去重)。
- 你定期跑 `./build_and_deploy.sh --process-requests`:取 `pending` 行 → 校验在母清单内 → 跑 agent 生成简报 → 写 `reports` 表 + 重建 `data.js` → 把该行标记 `done`/`invalid`/`not_in_universe` → 部署。
- 已有简报的代码,前端直接打开,不重复申请。

### 4.5 限制访问(只给认识的几个人)
Pages 站点默认公开。用 **Cloudflare Access**(Zero Trust,免费 ≤50 人)按邮箱白名单放行:
1. Cloudflare → **Zero Trust → Access → Applications → Add → Self-hosted**。
2. 域名填 `<项目>.pages.dev`;策略 **Allow**,Include → **Emails** 列出受邀邮箱(或 Emails ending in 某域名)。
3. 登录方式用默认 **One-time PIN**(邮箱收 6 位码),无需配置 IdP。
未在名单上的人打不开站点,也看不到提交框。

### 4.6 日常节奏
- **偶尔(重)**:刷底层数据 → `step4c` 等重算 CSV → `./build_and_deploy.sh --all`。
- **日常(轻)**:`./build_and_deploy.sh --process-requests` 处理申请并发布。
- **用户**:打开 URL → 邮箱验证 → 看清单/散点/候选/简报,或提交想看的代码。

---

## 5. 十三个工具(agent 深挖时调用)

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

## 6. 贯穿全程的设计纪律

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

## 7. 能力边界(务必牢记)

**能做:** 全市场高效缩小范围、系统化排雷、把"便宜/质量/现金流/分红/资金面"变成可查的客观数字、用定性尽调补足财报外信号、强制摊开估值假设。

**不能做(及原因):**
- **不能回测、不能算策略收益**。免费数据无 point-in-time(前视偏差)、无退市股(幸存者偏差),任何回测都不可信。这是全程**刻意不做回测**的原因。
- **不替你决策**。它给信号和假设,不给买卖建议。
- **不处理极端财务失真到"给可信数"**(如破产重整股),只会诚实标注"算不准"。
- **大宗交易等东财源可能间歇不可用**,有就看、没有不依赖。

**下一个台阶(质变,非补丁):** 换付费 point-in-time 数据(聚宽/Wind,含退市股)→ 才谈得上严肃回测、因子验证、真实超额收益。在那之前,把它当研究助手用。

---

## 8. 待办 / 可改进(攒够真实案例再动,勿过度工程)

- 强周期股单年 ROE/PB 失真:agent 深挖已能识别,暂不在母清单层加"周期标记"
- 个别票数据为空的容错(如茅台无互动易记录):实战遇到集中收集后统一加
- `agent_step7_roic.py` 返回里"口径"字段文字仍写旧口径(数值已是年均口径,仅文案待同步)
- 龙虎榜:已评估,因偏短线、与价值投资取向冲突,**决定不做**
