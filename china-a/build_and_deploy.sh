#!/usr/bin/env bash
# ============================================================================
# build_and_deploy.sh — 一条命令:本地生成数据 → 部署到 Cloudflare Pages
# ----------------------------------------------------------------------------
# 做两件事:
#   1) 跑 push_to_sheets.py:写入 Google Sheets(数据源之一)+ 生成 data.js
#   2) 只把 index.html + data.js 部署到 Cloudflare Pages(绝不上传 .env /
#      service_account.json / csv 缓存 —— 那些会变成可公开下载的文件)
#
# 用法:
#   ./build_and_deploy.sh                      # =--all:刷新母清单+候选+金融股,生成 data.js 并部署
#   ./build_and_deploy.sh --all --top 3        # 各行业前3的轻量母清单
#   ./build_and_deploy.sh --report 600519      # 生成单只简报、入库、更新 data.js 并部署
#   ./build_and_deploy.sh --report 600519 601006 600938   # 批量简报
#   ./build_and_deploy.sh --process-requests   # 处理用户提交的看票申请(requests 表)、入库并部署
#   ./build_and_deploy.sh --daily              # 日常例程:处理看票申请 → 刷新 data.js → 部署(适合定时跑)
#   ./build_and_deploy.sh --banks              # 刷新金融股评分卡(净资产收益率/ROA等,季度级)→ 重建 data.js 并部署
#   ./build_and_deploy.sh --preann             # 刷新业绩预告(前瞻红旗:首亏/预减/扭亏)→ 重建 data.js 并部署
#   ./build_and_deploy.sh --all --no-deploy    # 只本地生成,不部署(本地预览用)
#   ./build_and_deploy.sh --datajs             # 仅重建 data.js(不重算因子)并部署
#
# 前置:
#   · 已配置 china-a/.env(ZAI_API_KEY=...)与 service_account.json
#   · 已安装 wrangler(npx 会按需拉取),并 `npx wrangler login` 过一次
# ============================================================================
set -euo pipefail

# ── 配置(按需修改)────────────────────────────────────────────────────────
PROJECT_NAME="${CF_PAGES_PROJECT:-a-market}"   # ← 改成你的 Cloudflare Pages 项目名
PYTHON="${PYTHON:-python3}"
# ───────────────────────────────────────────────────────────────────────────

cd "$(dirname "$0")"

# 解析参数:抽出 --no-deploy / --daily,其余原样透传给 push_to_sheets.py
DEPLOY=1
DAILY=0
BANKS=0
PREANN=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --no-deploy) DEPLOY=0 ;;
    --daily)     DAILY=1 ;;
    --banks)     BANKS=1 ;;
    --preann)    PREANN=1 ;;
    *)           ARGS+=("$a") ;;
  esac
done

if [[ "$BANKS" -eq 1 ]]; then
  # 金融股评分卡:季度级数据,单独刷新后重建 data.js(不重算其它因子)
  echo "==> [1/2] 刷新金融股评分卡:bank_scorecard.py(约几分钟)"
  "$PYTHON" bank_scorecard.py
  echo "==> [1/2] 重建 data.js(并入评分卡)"
  "$PYTHON" push_to_sheets.py --datajs
elif [[ "$PREANN" -eq 1 ]]; then
  # 业绩预告(前瞻红旗层):一次全市场调用,刷新后重建 data.js
  echo "==> [1/2] 刷新业绩预告:earnings_preann.py"
  "$PYTHON" earnings_preann.py
  echo "==> [1/2] 重建 data.js(并入前瞻红旗)"
  "$PYTHON" push_to_sheets.py --datajs
elif [[ "$DAILY" -eq 1 ]]; then
  # 日常例程:看票申请 → 刷新业绩预告(前瞻红旗)→ 从 Sheets 刷新 data.js
  echo "==> [1/3] 日常例程:处理看票申请"
  "$PYTHON" push_to_sheets.py --process-requests
  echo "==> [2/3] 刷新业绩预告(前瞻红旗层)"
  "$PYTHON" earnings_preann.py || echo "  (业绩预告刷新失败,跳过,沿用旧值)"
  echo "==> [3/3] 刷新 data.js(同步 Sheets 最新简报 + 前瞻红旗)"
  "$PYTHON" push_to_sheets.py --datajs
else
  # 不带业务参数时,默认刷新全部
  if [[ ${#ARGS[@]} -eq 0 ]]; then ARGS=(--all); fi
  echo "==> [1/2] 本地生成(写 Google Sheets + data.js):push_to_sheets.py ${ARGS[*]}"
  "$PYTHON" push_to_sheets.py "${ARGS[@]}"
fi

if [[ ! -f data.js ]]; then
  echo "✗ 未找到 data.js,生成失败,终止部署。" >&2
  exit 1
fi

if [[ "$DEPLOY" -eq 0 ]]; then
  echo "==> 跳过部署(--no-deploy)。data.js 已就绪,可本地打开 index.html 预览。"
  exit 0
fi

echo "==> [2/2] 部署到 Cloudflare Pages 项目『${PROJECT_NAME}』"
# 只暂存要公开的两个文件,杜绝把密钥/缓存一起传上去
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT
cp index.html data.js "$STAGE_DIR"/
npx wrangler pages deploy "$STAGE_DIR" \
  --project-name="$PROJECT_NAME" \
  --commit-dirty=true

echo "✓ 完成:Google Sheets 已更新,站点已部署。"
