# -*- coding: utf-8 -*-
"""
push_to_sheets.py — 本地产出写入 Google Sheets
================================================================
结构:本地脚本(akshare/GLM在这里) → Google Sheets → Cloudflare前端
安全:service_account.json 放本地、加入.gitignore,绝不上传

依赖:pip install gspread pandas
凭证:service_account.json 放在本脚本同目录(或 GSPREAD_SA_PATH 环境变量)

Spreadsheet 结构(一个文件四张Sheet):
  masterlist    母清单(全量或行业前N)
  traditional   传统候选(A/B/交集)
  reports       agent简报(含历史,按code+date去重)
  financials    金融股备查(可选)

用法:
  python push_to_sheets.py --all          # 推母清单+传统候选
  python push_to_sheets.py --report 600519  # 对一只票生成简报并入库
  python push_to_sheets.py --dryrun --all   # 只打印不写入
  python push_to_sheets.py --report 600519 --dryrun

.gitignore 里加上:
  service_account.json
  ths_quality_cache.csv
  sina_sector.csv
"""

import os
import re
import sys
import json
import math
import time
import datetime as dt
import pandas as pd
import gspread
from gspread.exceptions import WorksheetNotFound


from _env import load_dotenv
load_dotenv()


def _clean_val(v):
    """NaN/inf/None 统一转空字符串，避免 JSON 序列化报错。"""
    if v is None: return ""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return ""
    return v


def _cell(header, value):
    """写入 Sheets 的单元格值。code 列强制文本(前置单引号)并补足6位,
    否则 Google Sheets 会把 000651 当数字吃掉前导零(读回成 651)。"""
    v = _clean_val(value)
    if header == "code" and v not in ("", None):
        return "'" + str(v).zfill(6)
    return v

# ================================================================
# 配置
# ================================================================
SA_PATH = os.environ.get("GSPREAD_SA_PATH", "service_account.json")
SPREADSHEET_NAME = "A股价值投资系统"   # 你在 Google Drive 里新建的文件名

# Sheet 名称(可改,前端读的是这些名字)
SH = {
    "masterlist":  "masterlist",
    "traditional": "traditional",
    "reports":     "reports",
    "financials":  "financials",
    "requests":    "requests",
    "signals":     "signals",     # 前瞻信号档案的耐久备份(唯一不可复原的产物)
}

TODAY = dt.date.today().strftime("%Y-%m-%d")

# ================================================================
# gspread 连接(懒加载,dryrun时不连)
# ================================================================
_gc = None
_ss = None

def _connect():
    global _gc, _ss
    if _gc is None:
        _gc = gspread.service_account(filename=SA_PATH)
        _ss = _gc.open(SPREADSHEET_NAME)
    return _ss


def _get_or_create_sheet(name, headers):
    ss = _connect()
    try:
        ws = ss.worksheet(name)
    except WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=5000, cols=len(headers))
        ws.append_row(headers)
        print(f"  [新建Sheet] {name}")
    return ws


# ================================================================
# 写入:upsert(按 key_col 去重,存在则更新,不存在则追加)
# ================================================================
def upsert_sheet(sheet_name, records, key_cols=("code",), dryrun=False):
    """全量刷新：clear后整体写入，避免逐行update触发Google Sheets 429限速。
    reports表例外：按key_cols追加（不清空，保留历史）。
    """
    if not records:
        print(f"  [{sheet_name}] 无数据,跳过")
        return

    headers = list(records[0].keys())

    if dryrun:
        print(f"  [dryrun] {sheet_name}: {len(records)} 条,headers={headers}")
        print(f"    样本: {records[0]}")
        return

    ws = _get_or_create_sheet(sheet_name, headers)

    # 去重 key 归一化:code 列补零(Sheets 可能把旧行的 000651 存成了 651)
    def _key(d):
        return tuple(str(d.get(c, "")).zfill(6) if c == "code" else str(d.get(c, ""))
                     for c in key_cols)

    # reports表：追加模式（保留历史简报，按key去重）
    if sheet_name == SH.get("reports", "reports"):
        existing = ws.get_all_records()
        existing_keys = {_key(r) for r in existing}
        to_append = []
        for rec in records:
            if _key(rec) not in existing_keys:
                to_append.append([_cell(h, rec.get(h)) for h in headers])
        if to_append:
            ws.append_rows(to_append, value_input_option="USER_ENTERED")
            print(f"  [{sheet_name}] 追加 {len(to_append)} 条新记录")
        else:
            print(f"  [{sheet_name}] 无新记录需追加")
        return

    # 其他表：全量清空重写（1次API调用，不限速）
    rows = [[_cell(h, rec.get(h)) for h in headers] for rec in records]
    ws.clear()
    ws.append_row(headers)                                    # 写header
    ws.append_rows(rows, value_input_option="USER_ENTERED")  # 写数据
    print(f"  [{sheet_name}] 全量写入 {len(rows)} 条")


# ================================================================
# 数据准备:母清单
# ================================================================
def _load_preann(path="earnings_preann.csv"):
    """业绩预告 → {code: {...}}。由 earnings_preann.py 生成,补前瞻盲点。"""
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)
    out = {}
    for r in df.to_dict("records"):
        out[r["code"]] = {
            "type": r.get("ptype"),
            "dir": r.get("direction"),
            "pct": (round(float(r["pct"]), 1)
                    if pd.notna(r.get("pct")) else None),
            "date": r.get("pdate"),
        }
    return out


def prepare_masterlist(path="factor_all_market_magic.csv", top_n=None):
    """
    top_n=None → 全量(~3500行,Sheets完全支持)
    top_n=3    → 各行业前3,约240行(更轻量)
    """
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)
    df = df[df["综合分"].notna()]   # 只要可排名的

    if top_n:
        df = df.sort_values("综合分").groupby("行业").head(top_n)

    colmap = {
        "code": "code", "name": "name", "行业": "industry", "pb": "pb",
        "ROE_3y": "roe_3y", "ROE_adj": "roe_adj", "综合分": "score", "便宜排名": "cheap_rank",
        "质量排名": "quality_rank", "CFQ_w": "cfq", "负债率": "debt_ratio",
        "红旗": "flags", "排名可信度": "rank_confidence",
    }
    out = df[[c for c in colmap if c in df.columns]].rename(columns=colmap)
    # 数值列保留两位小数
    for col in ["pb", "roe_3y", "roe_adj", "score", "cfq", "debt_ratio"]:
        if col in out.columns:
            out[col] = out[col].round(2)
    recs = out.where(pd.notna(out), None).to_dict("records")

    # 合并业绩预告:neg 并入前瞻红旗 flags,全部附 preann_* 字段
    preann = _load_preann()
    for r in recs:
        pa = preann.get(r["code"])
        if not pa:
            continue
        r["preann_type"] = pa["type"]
        r["preann_dir"] = pa["dir"]
        r["preann_pct"] = pa["pct"]
        if pa["dir"] == "neg":                       # 前瞻红旗:并入 flags
            token = "业绩" + str(pa["type"])         # 业绩首亏/业绩预减/业绩续亏/业绩略减
            r["flags"] = (str(r["flags"]) + "," + token) if r.get("flags") else token
    return recs


# ================================================================
# 数据准备:传统候选
# ================================================================
def prepare_traditional(value_path="factor_trad_value.csv",
                        stable_path="factor_trad_stable.csv"):
    def load(p):
        if not os.path.exists(p):
            print(f"  [跳过] {p} 不存在")
            return pd.DataFrame()
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].str.zfill(6)
        return df

    a, b = load(value_path), load(stable_path)
    if a.empty and b.empty:
        return []

    a_codes = set(a["code"]) if not a.empty else set()
    b_codes = set(b["code"]) if not b.empty else set()
    merged = pd.concat([a, b]).drop_duplicates("code")

    def bucket(c):
        if c in a_codes and c in b_codes:
            return "交集(又便宜又稳)"
        return "A-偏便宜" if c in a_codes else "B-偏稳健"

    merged["bucket"] = merged["code"].apply(bucket)
    colmap = {
        "code": "code", "name": "name", "行业": "industry", "pb": "pb",
        "ROE_3y": "roe_3y", "综合分": "score", "CFQ_w": "cfq",
        "负债率": "debt_ratio", "红旗": "flags", "bucket": "bucket",
        "排名可信度": "rank_confidence",
    }
    out = merged[[c for c in colmap if c in merged.columns]].rename(columns=colmap)
    for col in ["pb", "roe_3y", "score", "cfq", "debt_ratio"]:
        if col in out.columns:
            out[col] = out[col].round(2)
    return out.where(pd.notna(out), None).to_dict("records")


# ================================================================
# 数据准备:简报(从 agent Markdown 抽摘要)
# ================================================================
def _extract(pattern, text, default=None):
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else default


def _lookup_name_industry(code):
    """按代码从本地 CSV 缓存回查规范的 name / 行业。
    比从简报 Markdown 正则抽取可靠得多(后者会把『行业』等字样误当公司名)。"""
    code = str(code).zfill(6)
    for path in ("factor_all_market_magic.csv", "sina_sector.csv"):
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path, dtype={"code": str})
            df["code"] = df["code"].str.zfill(6)
            hit = df[df["code"] == code]
            if not len(hit):
                continue
            r = hit.iloc[0]
            name = r.get("name")
            industry = r.get("行业") if "行业" in df.columns else None
            return (str(name).strip() if pd.notna(name) else None,
                    str(industry).strip() if (industry is not None and pd.notna(industry)) else None)
        except Exception:
            continue
    return None, None


def prepare_report(code, name, industry, markdown, meta=None):
    """组装一条 reports 表记录。数字与红旗优先用结构化 meta(来自 agent 工具的确定性返回),
    meta 缺失时才退回从 Markdown 正则抽取(脆弱,仅兜底)。"""
    meta = meta or {}

    def _n(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None

    # 数字:meta 优先,缺失再正则
    roe = _n(meta.get("roe_3y"))
    if roe is None:
        roe = _n(_extract(r"3年[平均]*ROE[^%\d]*([\d.]+)%?", markdown))
    roic = _n(meta.get("roic_3y"))
    if roic is None:
        roic = _n(_extract(r"3年[平均]*ROIC[^%\d]*([\d.]+)%?", markdown))
    pb = _n(meta.get("pb"))
    if pb is None:
        pb = _n(_extract(r"\bPB\b[^\d]*([\d.]+)", markdown))

    # 重大红旗:meta 优先(由工具返回确定性判定),缺失再正则
    if meta.get("major_flags"):
        major_flags = list(dict.fromkeys(meta["major_flags"]))
    else:
        major_flags = []
        if re.search(r"会计差错", markdown): major_flags.append("会计差错")
        if re.search(r"立案|处罚", markdown): major_flags.append("监管处罚")
        if re.search(r"问询函|关注函", markdown): major_flags.append("监管问询")
        if re.search(r"ROIC.*可信.*false|失真警示", markdown): major_flags.append("ROIC失真")
        if re.search(r"净减持", markdown): major_flags.append("产业资本净减持")
        if re.search(r"大幅折价.*笔数.*[1-9]", markdown): major_flags.append("大宗折价")

    # 深层解读摘要(前400字)
    summary = ""
    m = re.search(r"深层解读([\s\S]{0,600})", markdown)
    if m:
        summary = re.sub(r"[#*\|>_\-]{2,}", "", m.group(1))[:400].strip()

    return {
        "code": str(code).zfill(6),
        "name": name,
        "industry": industry,
        "date": TODAY,
        "roe_3y": roe,
        "roic_3y": roic,
        "pb": pb,
        "major_flags": ", ".join(major_flags) if major_flags else "",
        "summary": summary,
        "markdown": markdown,
    }


# ================================================================
# 金融股备查
# ================================================================
def prepare_financials(path="factor_financials.csv",
                       universe_path="universe_financials.csv",
                       sector_path="sina_sector.csv",
                       scorecard_path="bank_scorecard.csv"):
    """金融股备查清单。合并三路来源:
      1) factor_financials.csv —— step4c 按行业分流的金融股(可能含市净率/净资产收益率);
      2) universe_financials.csv —— step1 按名称隔离的银行/券商/保险(否则银行根本不在数据里),
         市净率从行情快照缓存 sina_sector.csv 补;这些票未算因子;
      3) bank_scorecard.csv —— bank_scorecard.py 为金融股补的适用指标
         (净资产收益率3年均值/ROA/资产负债率/利润增速),按 code 覆盖填入。"""
    frames = []

    if os.path.exists(path):
        df = pd.read_csv(path, dtype={"code": str})
        df["code"] = df["code"].str.zfill(6)
        colmap = {"code": "code", "name": "name", "行业": "industry",
                  "pb": "pb", "ROE_3y": "roe_3y"}
        frames.append(df[[c for c in colmap if c in df.columns]].rename(columns=colmap))

    if os.path.exists(universe_path):
        u = pd.read_csv(universe_path, dtype={"code": str})
        u["code"] = u["code"].str.zfill(6)
        u = u.rename(columns={"sector": "industry"})
        if os.path.exists(sector_path):                 # 补市净率
            sec = pd.read_csv(sector_path, dtype={"code": str})
            sec["code"] = sec["code"].str.zfill(6)
            u = u.merge(sec[["code", "pb"]], on="code", how="left")
        keep = [c for c in ["code", "name", "industry", "pb"] if c in u.columns]
        ub = u[keep].copy()
        ub["roe_3y"] = None                              # 占位,下面用评分卡覆盖
        frames.append(ub)

    if not frames:
        return []
    # factor_financials 在前 → 同代码优先保留其(可能带净资产收益率)
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset="code", keep="first")

    # 用评分卡覆盖/补全金融股指标(净资产收益率/ROA/负债率/增速)
    for col in ["roa", "debt_ratio", "profit_growth"]:
        out[col] = None
    if os.path.exists(scorecard_path):
        sc = pd.read_csv(scorecard_path, dtype={"code": str})
        sc["code"] = sc["code"].str.zfill(6)
        sc = sc.set_index("code")
        for col in ["roe_3y", "roa", "debt_ratio", "profit_growth"]:
            if col in sc.columns:
                mapped = out["code"].map(sc[col])
                # roe_3y:评分卡优先,缺失时保留原 factor 值;其余列直接取评分卡
                out[col] = mapped.where(mapped.notna(), out[col]) if col == "roe_3y" else mapped

    for col in ["pb", "roe_3y", "roa", "debt_ratio", "profit_growth"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(2)
    return out.where(pd.notna(out), None).to_dict("records")


# ================================================================
# 主流程
# ================================================================
def generate_data_js(ml, tr, fi, reports=None, out_path="data.js"):
    """
    把所有数据序列化成 data.js,前端直接引入,彻底绕过 CORS。
    reports 如果传 None 则从 Google Sheets 读(需联网);
    通常本地直接从已有的简报记录列表传入。
    """
    import json, math

    def clean(obj):
        if isinstance(obj, list):
            return [clean(i) for i in obj]
        if isinstance(obj, dict):
            return {k: clean(v) for k,v in obj.items()}
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        return obj

    rp = reports or []
    sig = _load_signal_pack()                                         # 前瞻信号跟踪(诚实成绩单)
    lenses = _load_lenses_pack()                                      # 价值镜头(声明式筛选候选)
    mkt = _load_market_context()                                      # 全市场估值温度计
    payload = {
        "generated": TODAY,
        "request_endpoint": os.environ.get("REQUEST_ENDPOINT", ""),   # 来自 .env,前端看票申请用
        "market_context": clean(mkt),
        "masterlist":  clean(ml),
        "traditional": clean(tr),
        "financials":  clean(fi),
        "reports":     clean(rp),
        "signals":     clean(sig.get("signals", [])),
        "outcomes":    clean(sig.get("outcomes", [])),
        "signal_summary": clean(sig.get("summary", {})),
        "lens_scorecard": clean(sig.get("lens_scorecard", {})),
        "realization": clean(sig.get("realization", [])),
        "lenses":      clean(lenses),
    }
    js = f"// 自动生成,勿手动编辑。由 push_to_sheets.py 生成于 {TODAY}\n"
    js += f"window.SHEET_DATA = {json.dumps(payload, ensure_ascii=False)};\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(js)
    print(f"  → data.js 已生成({out_path}): "
          f"母清单{len(ml)}条 / 候选{len(tr)}条 / 简报{len(rp)}条 / "
          f"信号{len(payload['signals'])}条 / 镜头{len(payload['lenses'])}个")
    return out_path


def _load_signal_pack():
    """读前瞻信号档案 + 对照结果供 data.js。signal_tracker 缺失/异常时返回空,不阻断。"""
    try:
        from signal_tracker import load_for_datajs
        return load_for_datajs()
    except Exception as e:
        print(f"  [信号跟踪] 读取失败(data.js 不含信号):{type(e).__name__}: {e}")
        return {"signals": [], "outcomes": [], "summary": {}}


def sync_signals(dryrun=False):
    """把前瞻信号档案在 本地 signals.csv ↔ Google Sheets『signals』表 之间双向合并。
    动机:signals.csv 是唯一**不可复原**的产物(锚点价之外的 PB/隐含增速/镜头归属是发布当时的快照)。
    合并规则:按 (code, signal_date) 取并集;**已存在的键以 Sheet 为准**(档案不可变,防本地误改/清空覆盖云端),
    本地仅新增 Sheet 没有的行。合并结果同时写回 Sheet 与本地 → 本地若丢失会自动从云端恢复。需联网。"""
    from signal_tracker import _read_signals, write_signals, SIGNAL_COLS

    local = _read_signals()
    local_recs = local.where(pd.notna(local), "").to_dict("records") if not local.empty else []

    sheet_recs = []
    try:
        ss = _connect()
        try:
            ws = ss.worksheet(SH["signals"])
            sheet_recs = ws.get_all_records()
            for r in sheet_recs:
                if "code" in r:
                    r["code"] = str(r["code"]).zfill(6)
        except WorksheetNotFound:
            ws = None
    except Exception as e:
        print(f"  [信号备份] 连不上 Sheets,跳过(本地档案不受影响):{type(e).__name__}: {e}")
        return

    def _key(r):
        return (str(r.get("code", "")).zfill(6), str(r.get("signal_date", "")))

    merged, seen = [], set()
    for r in sheet_recs:                          # Sheet 优先(不可变档案权威源)
        k = _key(r)
        if k not in seen and k[0]:
            seen.add(k); merged.append(r)
    new_local = 0
    for r in local_recs:                          # 本地仅补 Sheet 没有的新快照
        k = _key(r)
        if k not in seen and k[0]:
            seen.add(k); merged.append(r); new_local += 1

    # 规整列 + 排序(发布日)
    norm = [{c: r.get(c, "") for c in SIGNAL_COLS} for r in merged]
    norm.sort(key=lambda r: str(r.get("signal_date", "")))

    print(f"  [信号备份] Sheet {len(sheet_recs)} + 本地新增 {new_local} → 合并 {len(norm)} 条")
    if dryrun:
        return
    upsert_sheet(SH["signals"], norm, key_cols=("code", "signal_date"), dryrun=False)
    restored = write_signals(norm)                # 合并结果写回本地(本地丢失→自动恢复)
    print(f"  [信号备份] 已同步:Sheet ←→ 本地 signals.csv({restored} 条)")


def _load_lenses_pack(path="factor_lenses.json"):
    """读价值镜头合并包(lens_screen.py 产)供 data.js。缺失/异常返回空 dict,不阻断。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [价值镜头] 读取失败(data.js 不含镜头):{type(e).__name__}: {e}")
        return {}


def _load_market_context():
    """读全市场估值温度计(market_context.py 产)供 data.js。缺失/异常返回空 dict。"""
    try:
        from market_context import load
        return load()
    except Exception as e:
        print(f"  [估值温度计] 读取失败(data.js 不含温度计):{type(e).__name__}: {e}")
        return {}


# ================================================================
# 主流程
# ================================================================
def push_all(dryrun=False, top_n=None):
    """推母清单 + 传统候选 + 金融股备查,并生成 data.js 供前端使用。"""
    print(f"=== push_all {'[DRYRUN]' if dryrun else ''} ===")

    print("\n[1/3] 母清单")
    ml = prepare_masterlist(top_n=top_n)
    print(f"  准备 {len(ml)} 条{'(全量)' if not top_n else f'(各行业前{top_n})'}")
    upsert_sheet(SH["masterlist"], ml, key_cols=("code",), dryrun=dryrun)

    print("\n[2/3] 传统候选")
    tr = prepare_traditional()
    print(f"  准备 {len(tr)} 条")
    upsert_sheet(SH["traditional"], tr, key_cols=("code",), dryrun=dryrun)

    print("\n[3/3] 金融股备查")
    fi = prepare_financials()
    print(f"  准备 {len(fi)} 条")
    upsert_sheet(SH["financials"], fi, key_cols=("code",), dryrun=dryrun)

    print("\n[生成 data.js]")
    if not dryrun:
        rp = _read_reports_from_sheets()   # 保留已有简报,避免 --all 把 reports 清空
        generate_data_js(ml, tr, fi, rp)

    print("\n完成。把 data.js 和 index.html 一起部署到 Cloudflare Pages。")


def push_report(code, dryrun=False, rebuild=True):
    """对单只票生成 agent 简报并写入 reports Sheet。成功返回 True,失败返回 False。
    rebuild=False:不在此处重建 data.js(批量处理时由调用方最后统一重建一次)。"""
    import subprocess
    print(f"=== push_report {code} {'[DRYRUN]' if dryrun else ''} ===")

    # 调 agent 生成简报(复用现有脚本)
    print("  生成简报中(调 agent_step8)...")
    result = subprocess.run(
        [sys.executable, "agent_step8_block_trade.py", str(code)],
        capture_output=True, text=True, encoding="utf-8",
        cwd=os.path.dirname(os.path.abspath(__file__))  # 确保工作目录正确
    )
    output = result.stdout

    if result.returncode != 0:
        print(f"  [ERROR] agent_step8 退出码 {result.returncode}")
        print(f"  完整 stderr:\n{result.stderr}")
        return False

    # 结构化 META(agent_step8 末尾打印 <<<META>>>{json}<<<END_META>>>),优先于正则
    meta = {}
    mm = re.search(r"<<<META>>>(.*?)<<<END_META>>>", output, re.S)
    if mm:
        try:
            meta = json.loads(mm.group(1).strip())
        except Exception as e:
            print(f"  [WARN] META 解析失败,退回正则: {e}")

    # 过滤掉工具调用日志行，只保留简报正文
    clean_lines = [
        l for l in output.splitlines()
        if not l.strip().startswith('[工具]')
        and not l.strip().startswith('[GLM重试]')
        and not l.strip().startswith('=== ')
        and not l.strip().startswith('<<<META>>>')
        and l.strip() != '数据已全部返回，下面生成完整简报。'
    ]
    markdown = '\n'.join(clean_lines).strip()

    # 从输出里提取 "===" 分隔后的简报 Markdown
    md_match = re.search(r"===\s*完整简报.*?===\n+([\s\S]+)", output)
    if md_match:
        markdown = md_match.group(1).strip()
    if mm:                                  # 防御:剔除可能混入正文的 META 块
        markdown = markdown.replace(mm.group(0), "").strip()

    if not markdown:
        print("  [ERROR] 未能从 agent 输出中提取简报,请检查 agent_step8 输出")
        print("  stderr:", result.stderr[:300])
        return False

    # name/industry:META → 本地缓存回查 → 正则 → code
    name = (meta.get("name") or "").strip() or None
    industry = meta.get("industry")
    industry = str(industry).strip() if industry is not None else None
    if not name or industry is None:
        ln, li = _lookup_name_industry(code)
        name = name or ln
        if industry is None:
            industry = li
    if not name:
        name = _extract(r"公司[名称全称简称]*[^\w]+([\w\s（）]+)", markdown) or code
    if industry is None:
        industry = _extract(r"所属行业[^\w]+([\w、和及]+)", markdown) or ""

    rec = prepare_report(code, str(name).strip(), str(industry).strip(), markdown, meta=meta)
    print(f"  抽取: ROE={rec['roe_3y']} ROIC={rec['roic_3y']} PB={rec['pb']}"
          f" 重大红旗={rec['major_flags'] or '无'}  (来源:{'META' if meta else '正则'})")

    upsert_sheet(SH["reports"], [rec],
                 key_cols=("code", "date"),
                 dryrun=dryrun)
    if not dryrun:
        print(f"  ✓ {code} 简报已入库 reports Sheet(date={TODAY})")
        _snapshot_signal_safe(rec, meta)     # 前瞻信号跟踪:冻结当时判断(失败不阻断发布)
        if rebuild:                          # 批量处理时跳过,末尾统一重建
            _sync_signals_safe()             # 单只发布即备份到 Sheets(批量时末尾统一备份)
            _rebuild_data_js()
    return True


def _snapshot_signal_safe(rec, meta):
    """把当前简报快照进信号档案(signal_tracker)。任何异常都不应影响简报发布主流程。"""
    try:
        from signal_tracker import snapshot_signal
        snapshot_signal(rec, meta=meta)
    except Exception as e:
        print(f"  [信号跟踪] 快照失败(不影响发布):{type(e).__name__}: {e}")


def _sync_signals_safe():
    """把信号档案双向同步到 Sheets 备份。任何异常都不应影响主流程(本地档案仍在)。"""
    try:
        sync_signals(dryrun=False)
    except Exception as e:
        print(f"  [信号备份] 同步失败(不影响主流程,本地档案仍在):{type(e).__name__}: {e}")


def _read_reports_from_sheets():
    """从 reports Sheet 读已有简报(需联网)。失败则返回空列表,不阻断 data.js 生成。"""
    try:
        ss = _connect()
        ws = ss.worksheet(SH["reports"])
        rp = ws.get_all_records()
        for r in rp:                        # Sheets 可能把 000651 读回成数字 651,补零纠正
            if "code" in r:
                r["code"] = str(r["code"]).zfill(6)
        print(f"  读取已有简报 {len(rp)} 条")
        return rp
    except Exception as e:
        print(f"  [跳过简报] 读取 Sheets 失败: {e}")
        return []


def _rebuild_data_js():
    """从本地 CSV 重建 data.js(含已有简报从 Sheets 读)。"""
    ml = prepare_masterlist()
    tr = prepare_traditional()
    fi = prepare_financials()
    rp = _read_reports_from_sheets()
    generate_data_js(ml, tr, fi, rp)


# ================================================================
# 看票申请:读取 requests 表的 pending 行 → 逐只生成简报 → 标记 done
# ================================================================
def _masterlist_codes():
    """母清单里的合法代码集合,用于校验申请(避免乱填/退市票白跑一次 GLM)。"""
    path = "factor_all_market_magic.csv"
    if not os.path.exists(path):
        return set()
    df = pd.read_csv(path, dtype={"code": str})
    return set(df["code"].astype(str).str.zfill(6))


def process_requests(dryrun=False):
    """处理用户经前端(Apps Script)提交、落在 requests 表里的看票申请。
    流程:取 status=pending 的行 → 6位+母清单校验 → push_report 生成入库 →
    把该行 status 改为 done/invalid/not_in_universe。需联网。"""
    print(f"=== process_requests {'[DRYRUN]' if dryrun else ''} ===")
    ss = _connect()
    try:
        ws = ss.worksheet(SH["requests"])
    except WorksheetNotFound:
        print("  无 requests 工作表,跳过(还没有人提交申请)")
        return []

    records = ws.get_all_records()
    if not records:
        print("  requests 表为空")
        return []
    header = ws.row_values(1)
    try:
        status_col = header.index("status") + 1     # update_cell 用的列号(1基)
    except ValueError:
        print("  requests 表缺 status 列,跳过")
        return []

    # 行号:表头占第1行,数据从第2行起
    pending = [(i + 2, r) for i, r in enumerate(records)
               if str(r.get("status", "")).strip().lower() == "pending"]
    if not pending:
        print("  没有待处理(pending)的申请")
        return []

    valid = _masterlist_codes()
    print(f"  待处理 {len(pending)} 条")
    done = []
    for rownum, r in pending:
        code = str(r.get("code", "")).strip().zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            print(f"  [行{rownum}] 非法 code『{r.get('code')}』→ invalid")
            if not dryrun:
                ws.update_cell(rownum, status_col, "invalid")
            continue
        if valid and code not in valid:
            print(f"  [{code}] 不在母清单 → not_in_universe")
            if not dryrun:
                ws.update_cell(rownum, status_col, "not_in_universe")
            continue
        print(f"  → 生成简报 {code} ...")
        if not dryrun:
            ok = push_report(code, dryrun=False, rebuild=False)   # 批量:暂不重建 data.js
            ws.update_cell(rownum, status_col, "done" if ok else "error")
            if ok:
                done.append(code)
            else:
                print(f"  [{code}] 生成失败 → 标记 error(把该行改回 pending 可重试)")
        else:
            done.append(code)
    if not dryrun and done:
        _sync_signals_safe()                # 批量结束后统一备份新增信号到 Sheets
        _rebuild_data_js()                  # 批量结束后统一重建一次 data.js
    print(f"  完成 {len(done)} 条:{done or '无'}")
    return done


# ================================================================
# CLI
# ================================================================
if __name__ == "__main__":
    args = sys.argv[1:]
    dryrun = "--dryrun" in args
    args = [a for a in args if a != "--dryrun"]

    if not args:
        print("用法:")
        print("  python push_to_sheets.py --all              推母清单+候选+金融股+生成data.js")
        print("  python push_to_sheets.py --all --top 3      各行业前3(轻量版)")
        print("  python push_to_sheets.py --report 600519    生成+入库简报+更新data.js")
        print("  python push_to_sheets.py --report 600519 601006 600938  批量简报")
        print("  python push_to_sheets.py --datajs           仅重新生成data.js")
        print("  python push_to_sheets.py --process-requests 处理用户提交的看票申请(requests表)")
        print("  python push_to_sheets.py --eval-signals     前瞻信号对照(沪深300超额)→ 重建data.js")
        print("  python push_to_sheets.py --backup-signals   信号档案 ↔ Sheets 双向同步(备份/恢复)")
        print("  任何命令加 --dryrun 只打印不写入")
        sys.exit(0)

    if "--all" in args:
        top_idx = args.index("--top") if "--top" in args else None
        top_n = int(args[top_idx + 1]) if top_idx is not None else None
        push_all(dryrun=dryrun, top_n=top_n)

    elif "--report" in args:
        codes = args[args.index("--report") + 1:]
        if not codes:
            print("请提供至少一个股票代码")
            sys.exit(1)
        for c in codes:
            push_report(c, dryrun=dryrun)
            if len(codes) > 1:
                time.sleep(2)

    elif "--process-requests" in args:
        process_requests(dryrun=dryrun)

    elif "--eval-signals" in args:
        print("=== 前瞻信号对照 ===")
        from signal_tracker import evaluate_signals
        evaluate_signals(dryrun=dryrun)
        if not dryrun:
            _sync_signals_safe()            # 顺带把档案备份到 Sheets
            _rebuild_data_js()              # 把最新对照结果并入 data.js

    elif "--backup-signals" in args:
        print("=== 信号档案 ↔ Sheets 双向同步(备份/恢复)===")
        sync_signals(dryrun=dryrun)

    elif "--datajs" in args:
        print("=== 重新生成 data.js ===")
        _rebuild_data_js()
