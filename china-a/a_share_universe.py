# -*- coding: utf-8 -*-
"""
A股价值投资 Agent —— 阶段一:股票池清洗  v3
================================================================
诊断结论:东方财富(eastmoney push2)接口在当前网络下【持续不通】
(6次重试全 RemoteDisconnected,非限流而是链路问题)。
因此 v3:
  1) 金融股隔离改为【纯股票名称识别】,零网络调用,绕开东财。
  2) 仍依赖东财的"停牌过滤"改为【快速失败】(只重试2次),失败即跳过。
  3) 新增 probe_hosts():探测本机能通哪些数据源,决定第二步走哪条路。

依赖:pip install akshare pandas --upgrade
"""

import re
import time
import random
from datetime import datetime, timedelta

import pandas as pd

try:
    import akshare as ak
except ImportError:
    raise SystemExit("请先安装:pip install akshare pandas --upgrade")


def robust(fn, *args, retries=5, base_delay=3.0, label="", **kwargs):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt == retries:
                break
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 2)
            print(f"    [{label}] 第{attempt}次失败({type(e).__name__}),{delay:.1f}s 后重试...")
            time.sleep(delay)
    raise last_err


MIN_LISTED_DAYS = 365

# ----------------------------------------------------------------------
# 金融股识别:纯名称规则 + 一小撮"名字不自证"的大非银(原型够用,不求全)
# ----------------------------------------------------------------------
# 银行:名称含"银行",或农商行类(渝农商行/沪农商行/张家港行 等以"行"结尾)
BANK_NAME_RE = re.compile(r"银行$|农商|^.*行$")
# 券商:名称含"证券"
SEC_NAME_RE = re.compile(r"证券")
# 保险:名称含"保险/人寿/财险"
INS_NAME_RE = re.compile(r"保险|人寿|财险")

# 名字不自证身份的大非银(代码→板块)。原型用,后续以专业分类替换。
NONBANK_SUPPLEMENT = {
    "601318": "非银金融",  # 中国平安
    "601628": "非银金融",  # 中国人寿
    "601601": "非银金融",  # 中国太保
    "601336": "非银金融",  # 新华保险
    "601319": "非银金融",  # 中国人保
    "601211": "非银金融",  # 国泰君安(证券)
    "000166": "非银金融",  # 申万宏源(证券)
    "601881": "非银金融",  # 中国银河(证券)
    "300059": "非银金融",  # 东方财富(金融科技/券商)
    "600291": "非银金融",  # 西水股份(保险系,示例)
}


def get_all_a_share() -> pd.DataFrame:
    frames = []
    for board in ["主板A股", "科创板"]:
        try:
            df = robust(ak.stock_info_sh_name_code, symbol=board, label=f"SH/{board}")
            df = df.rename(columns={"证券代码": "code", "证券简称": "name", "上市日期": "list_date"})
            frames.append(df[["code", "name", "list_date"]])
            print(f"  [SH/{board}] 取得 {len(df)} 只")
        except Exception as e:
            print(f"  [SH/{board}] 最终失败:{e}")
        time.sleep(1.0)
    try:
        df = robust(ak.stock_info_sz_name_code, symbol="A股列表", label="SZ/A股列表")
        df = df.rename(columns={"A股代码": "code", "A股简称": "name", "A股上市日期": "list_date"})
        frames.append(df[["code", "name", "list_date"]])
        print(f"  [SZ/A股列表] 取得 {len(df)} 只")
    except Exception as e:
        print(f"  [SZ/A股列表] 最终失败:{e}")
    if not frames:
        raise SystemExit("所有列表接口均失败,请检查网络。")
    all_df = pd.concat(frames, ignore_index=True)
    all_df["code"] = all_df["code"].astype(str).str.zfill(6)
    all_df["list_date"] = pd.to_datetime(all_df["list_date"], errors="coerce")
    return all_df.drop_duplicates(subset="code").reset_index(drop=True)


def drop_st(df):
    return df[~df["name"].str.contains("ST", case=False, na=False)].copy()


def drop_new(df):
    cutoff = datetime.now() - timedelta(days=MIN_LISTED_DAYS)
    return df[df["list_date"] <= cutoff].copy()


def keep_tradeable(df):
    """东财快照,已知本网络不通 → 快速失败(retries=2)后跳过。"""
    try:
        spot = robust(ak.stock_zh_a_spot_em, label="实时快照", retries=2, base_delay=2.0)
        spot = spot.rename(columns={"代码": "code", "最新价": "price"})
        spot["code"] = spot["code"].astype(str).str.zfill(6)
        tradeable = spot.loc[spot["price"].notna(), "code"]
        return df[df["code"].isin(set(tradeable))].copy()
    except Exception as e:
        print(f"  [停牌过滤] 东财不通,跳过(原型可接受):{type(e).__name__}")
        return df


def split_financials(df):
    """纯名称识别 + 大非银补充,零网络调用。"""
    def sector_of(row):
        name, code = row["name"], row["code"]
        if BANK_NAME_RE.search(name):
            return "银行"
        if SEC_NAME_RE.search(name) or INS_NAME_RE.search(name):
            return "非银金融"
        if code in NONBANK_SUPPLEMENT:
            return NONBANK_SUPPLEMENT[code]
        return None

    df = df.copy()
    df["sector"] = df.apply(sector_of, axis=1)
    fin = df[df["sector"].notna()].copy()
    normal = df[df["sector"].isna()].drop(columns=["sector"]).copy()
    return normal, fin


def build_universe():
    print("\n=== 阶段一:A股股票池清洗 v3 ===\n")
    print("[1/5] 拉取全A股列表...")
    df = get_all_a_share()
    print(f"  → 全A股(沪+深):{len(df)} 只\n")
    print("[2/5] 剔除 ST / *ST ...")
    df = drop_st(df); print(f"  → 剩余:{len(df)} 只\n")
    print(f"[3/5] 剔除次新股(上市不满 {MIN_LISTED_DAYS} 天)...")
    df = drop_new(df); print(f"  → 剩余:{len(df)} 只\n")
    print("[4/5] 剔除当前停牌 / 无报价 ...")
    df = keep_tradeable(df); print(f"  → 剩余:{len(df)} 只\n")
    print("[5/5] 隔离 银行 / 非银金融(纯名称识别)...")
    normal, fin = split_financials(df)
    print(f"  → 普通行业观察池:{len(normal)} 只")
    print(f"  → 金融桶:{len(fin)} 只 "
          f"(银行 {sum(fin['sector']=='银行')} / 非银 {sum(fin['sector']=='非银金融')})\n")
    normal.to_csv("universe_normal.csv", index=False, encoding="utf-8-sig")
    fin.to_csv("universe_financials.csv", index=False, encoding="utf-8-sig")
    print("已保存:universe_normal.csv  /  universe_financials.csv")
    return normal, fin


# ----------------------------------------------------------------------
# 数据源连通性探测:决定第二步(财务报表)走哪条路
# ----------------------------------------------------------------------
def probe_hosts():
    """逐个测试代表性接口,看本机能通哪些【数据源】。
    第二步选源就看这里谁通。接口名若漂移会算作'失败',
    但我们关心的是'源能否连上',多数情况一类源要么全通要么全断。"""
    print("\n=== 数据源连通性探测 ===")
    probes = [
        ("东财 eastmoney",  lambda: ak.stock_board_industry_name_em()),
        ("新浪 sina(日线)", lambda: ak.stock_zh_a_daily(symbol="sh600519", adjust="qfq")),
        ("新浪 sina(财报)", lambda: ak.stock_financial_report_sina(stock="sh600519", symbol="资产负债表")),
        ("巨潮 cninfo(简介)", lambda: ak.stock_profile_cninfo(symbol="600519")),
        ("同花顺 ths(财务)", lambda: ak.stock_financial_abstract_ths(symbol="600519")),
    ]
    results = {}
    for name, fn in probes:
        try:
            out = fn()
            ok = out is not None and len(out) > 0
            results[name] = ok
            print(f"  [{'OK ' if ok else '空 '}] {name}")
        except Exception as e:
            results[name] = False
            print(f"  [FAIL] {name}  ({type(e).__name__})")
        time.sleep(1.5)
    print("\n→ 把上面这张表发我,我据此定第二步的财务数据源。")
    return results


if __name__ == "__main__":
    build_universe()
    probe_hosts()
