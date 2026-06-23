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

# 取回既有历史序列 + 数据库, 保证跨 run 连续累积
if git clone -q --depth 1 --branch otc-data "$REMOTE" "$WORK/datarepo" 2>/dev/null; then
  cp "$WORK/datarepo/history.json" "$DATA_DIR/" 2>/dev/null || true
  cp "$WORK/datarepo/otc.db"       "$DATA_DIR/" 2>/dev/null || true
  echo "[loop] 已恢复: history=$(wc -c <"$DATA_DIR/history.json" 2>/dev/null || echo 0)B db=$(wc -c <"$DATA_DIR/otc.db" 2>/dev/null || echo 0)B"
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

# gh-pages 维护一份"站点+数据"快照: 供 raw.githack.com 即时公网访问,
# 也兼容用户在设置里选择 "Deploy from a branch" 的 legacy Pages 模式。
publish_site() {
  rm -rf "$WORK/site"
  mkdir -p "$WORK/site/data"
  cp -r "$SITE_SRC"/. "$WORK/site/"
  # 站点消费的全部 JSON 投影 (SQLite 本体 otc.db 不进站点, 仅留在数据分支)
  for f in latest.json history.json merchants.json suspicious.json channels.json series.json binance_api.json; do
    cp "$DATA_DIR/$f" "$WORK/site/data/" 2>/dev/null || true
  done
  touch "$WORK/site/.nojekyll"
  push_branch "$WORK/site" gh-pages "publish site $(date -u +%FT%TZ)" \
    && echo "[loop] 站点快照已推送 gh-pages"
}

for ((i = 1; i <= CYCLES; i++)); do
  echo "=== cycle $i/$CYCLES $(date -u +%FT%TZ) ==="
  if python3 "$FETCHER" --out "$DATA_DIR" ${PROBE:+--probe}; then
    push_branch "$DATA_DIR" otc-data "data $(date -u +%FT%TZ)" || echo "[loop] 数据推送失败"
    if [ "$i" -eq 1 ]; then
      publish_site
    fi
  else
    echo "[loop] 第 $i 轮抓取失败, 跳过发布"
  fi
  if [ "$i" -lt "$CYCLES" ]; then
    sleep "$INTERVAL"
  fi
done
echo "[loop] 本次运行结束"
