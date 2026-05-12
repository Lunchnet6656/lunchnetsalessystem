#!/usr/bin/env bash
# 本日の出店状況を生成し、Cloudflare Pages 用リポジトリ (lunchnet-status-page) の main へ push する。
# Cloudflare Pages が main の更新を検知して自動デプロイ → status.lunchnetsalessystem.com に反映される。
#
# 実行タイミング: Heroku Scheduler で毎日 23:00 UTC（= 8:00 JST）。持参数は当日朝に確定するため。
#   ※ Scheduler は曜日指定できないが、土日祝の扱いは generate_status_json 側が判定するので毎日実行でよい。
# 必要な Heroku Config Var: GH_TOKEN ＝ lunchnet-status-page への Contents:write 権限を持つ
#   GitHub fine-grained Personal Access Token（対象リポジトリのみに絞ること）。
#
# 動作確認: GH_TOKEN を設定後、`heroku run bash bin/publish_status_page.sh` で1回手動実行 → リポジトリに反映されるか確認。
set -euo pipefail

REPO="shohei-02/lunchnet-status-page"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

git clone --depth 1 "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" "$WORKDIR/repo"
python manage.py generate_status_json --output "$WORKDIR/repo/status.json"

cd "$WORKDIR/repo"
if git diff --quiet -- status.json; then
  echo "[publish_status_page] 変更なし（status.json は最新）"
  exit 0
fi

git -c user.name="lunchnet-bot" -c user.email="bot@lunchnetsalessystem.com" \
  commit -m "status.json 更新 $(date -u +%Y-%m-%dT%H:%MZ)" -- status.json
git push origin HEAD:main
echo "[publish_status_page] push 完了"
