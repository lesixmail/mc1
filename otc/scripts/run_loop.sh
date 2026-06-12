#!/usr/bin/env bash
# OTC Monitor 抓取-发布循环
#   $1 = 轮数 (默认 11)   $2 = 间隔秒 (默认 300)
# 每轮: 抓取 -> 推送 otc-data 数据分支; 首轮额外发布站点到 gh-pages 并确保 Pages 已开通。
set -uo pipefail

CYCLES="${1:-11}"
INTERVAL="${2:-300}"
REPO="${GITHUB_REPOSITORY:-lesixmail/mc1}"
TOKEN="${GH_TOKEN:?GH_TOKEN required}"
REMOTE="https://x-access-token:${TOKEN}@github.com/${REPO}.git"
WORK=/tmp/otc-work
DATA_DIR="$WORK/data"
SITE_SRC="${GITHUB_WORKSPACE:-$(pwd)}/otc/site"
FETCHER="${GITHUB_WORKSPACE:-$(pwd)}/otc/scripts/fetch_otc.py"

mkdir -p "$DATA_DIR"

# 取回既有历史序列, 保证跨 run 连续
if git clone -q --depth 1 --branch otc-data "$REMOTE" "$WORK/datarepo" 2>/dev/null; then
  cp "$WORK/datarepo/history.json" "$DATA_DIR/" 2>/dev/null || true
  echo "[loop] 已恢复历史数据 ($(wc -c <"$DATA_DIR/history.json" 2>/dev/null || echo 0) bytes)"
else
  echo "[loop] 数据分支不存在, 全新开始"
fi

push_branch() { # $1=dir $2=branch $3=msg
  local dir="$1" branch="$2" msg="$3" i
  (
    cd "$dir" || return 1
    rm -rf .git
    git init -q -b "$branch"
    git config user.name "otc-monitor[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git add -A
    git commit -qm "$msg"
    for i in 2 4 8 16; do
      git push -q -f "$REMOTE" "$branch" && return 0
      echo "[loop] push $branch 失败, ${i}s 后重试"
      sleep "$i"
    done
    git push -q -f "$REMOTE" "$branch"
  )
}

# 站点本体由工作流的 pages job 发布 (workflow 构建类型), 此循环只负责数据分支。
for ((i = 1; i <= CYCLES; i++)); do
  echo "=== cycle $i/$CYCLES $(date -u +%FT%TZ) ==="
  if python3 "$FETCHER" --out "$DATA_DIR" ${PROBE:+--probe}; then
    push_branch "$DATA_DIR" otc-data "data $(date -u +%FT%TZ)" || echo "[loop] 数据推送失败"
  else
    echo "[loop] 第 $i 轮抓取失败, 跳过发布"
  fi
  if [ "$i" -lt "$CYCLES" ]; then
    sleep "$INTERVAL"
  fi
done
echo "[loop] 本次运行结束"
