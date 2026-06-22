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

# 解析参数:抽出 --no-deploy,其余原样透传给 push_to_sheets.py
DEPLOY=1
ARGS=()
for a in "$@"; do
  if [[ "$a" == "--no-deploy" ]]; then DEPLOY=0; else ARGS+=("$a"); fi
done
# 不带业务参数时,默认刷新全部
if [[ ${#ARGS[@]} -eq 0 ]]; then ARGS=(--all); fi

echo "==> [1/2] 本地生成(写 Google Sheets + data.js):push_to_sheets.py ${ARGS[*]}"
"$PYTHON" push_to_sheets.py "${ARGS[@]}"

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
