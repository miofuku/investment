# 价值镜头 lenses/ —— 声明式筛选(加视角只写 YAML,不写代码)

每个 `*.yaml` 是一种"价值投资视角",对母清单 `factor_all_market_magic.csv` 做**确定性**筛选+排序,
产候选清单。引擎是 [`../lens_screen.py`](../lens_screen.py)。

> 纪律:**算术全在 `lens_screen.py` 里**,YAML 只声明阈值与排序,不含任何计算。
> 字段写错**当场报错**(不静默)。镜头只做广度缩小、不下买卖结论;真伪留给 agent 深挖。

`_` 开头的文件是配置而非镜头(如 `_groups.yaml` 定义命名行业组)。

## 命令
```bash
python lens_screen.py             # 跑全部镜头,打印 + 落盘(factor_lens_<name>.csv + factor_lenses.json)
python lens_screen.py deep_value  # 只跑一个
python lens_screen.py --list      # 列出可用镜头
```

## 写一个新镜头(最简)
```yaml
name: my_lens                 # 唯一标识(英文下划线)
display_name: 我的镜头         # 显示名
description: 一句话说清这个视角在找什么
rationale: |                  # 可选但强烈建议:在找什么 / 最怕什么 / 怎么读
  ...
industry_group: traditional   # 可选;引用 _groups.yaml 的组名;省略=全市场
filters:                      # 全部 AND;field 必须是下方"可用字段"之一
  - {field: pb,     op: le, value: 1.5}
  - {field: 负债率, op: le, value: 50}
exclude_flags: any            # none | any(零红旗) | severe(剔重红旗) | [关键词,...]
rank_by: 综合分               # 排序列(可用字段之一)
ascending: true               # 综合分/排名/PB 越小越好→true
top_n: 30                     # 截断;省略=不截
```

## 可用字段
**母清单真实列**(step4c 产):
`pb` · `ROE_3y`(原始3年ROE) · `ROE_adj`(去杠杆ROE≈ROA) · `综合分`(便宜+质量各半,越小越前) ·
`便宜排名` · `质量排名` · `CFQ_w`(3年现金流质量,clip[-1,3]) · `负债率` · `红旗` · `排名可信度`

**引擎派生列**(算术在代码里):
- `flag_count` —— 红旗个数
- `roe_leverage_ratio` —— `ROE_adj / ROE_3y` ≈ 权益占比。**越接近1,高回报越是真功夫;越低=ROE 多半靠杠杆撑**(正邦教训的镜头级护栏)。

操作符:`ge` `le` `gt` `lt` `eq` `ne`

## 内置镜头
| name | 视角 |
|---|---|
| `deep_value` | 传统行业偏便宜(≈旧 A 表):剔最差陷阱后按综合分 |
| `quality_stable` | 传统行业偏稳健(≈旧 B 表):先卡活得久硬指标再挑便宜 |
| `graham_defensive` | 全市场格雷厄姆防御:低PB+低负债+正现金流+零红旗 |
| `clean_compounder` | 全市场干净复利:高ROE 且回报非靠杠杆(`roe_leverage_ratio` 护栏) |

> `deep_value`∩`quality_stable` = 又便宜又稳,最甜的点。命中后用 `agent_step8_block_trade.py <code>` 逐只深挖
> (分红/ROIC/反向DCF/三表排雷都在 agent 里),再把简报喂给信号跟踪(见 ../README.md §9)。

## 按镜头的前瞻成绩单
发布简报时会**冻结当时命中的镜头归属**(写进 `signals.csv` 的 `lenses` 列);信号跟踪据此给每个镜头算
"已到期窗口数 / 跑赢沪深300占比 / 平均超额",显示在前端「价值镜头」页每个镜头描述下方。
用**冻结归属**而非当前归属,避免镜头会员事后漂移带来的前视偏差。详见 ../README.md §9。
