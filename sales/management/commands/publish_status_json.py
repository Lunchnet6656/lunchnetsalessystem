"""本日の出店状況を生成し、GitHub Contents API で公開ページ用リポジトリへコミットする。

Heroku の実行 dyno には git バイナリが無い（Python buildpack はビルド時しか git を持たない）ため、
git clone/push ではなく GitHub の REST API（HTTPS）でファイルを直接更新する。
コミットされると Cloudflare（lunchnet-status-page）が自動デプロイ → status.lunchnetsalessystem.com に反映。

Heroku Scheduler 設定: 毎日 23:00 UTC（= 8:00 JST）に `python manage.py publish_status_json` を実行。
  ※ Scheduler は曜日指定できないが、土日祝の扱いは generate_status_json 側のロジックが判定するので毎日でよい。
必要な Heroku Config Var:
  GH_TOKEN   ＝ lunchnet-status-page への Contents:write 権限を持つ GitHub fine-grained PAT（対象リポジトリのみに絞ること）
  （任意）GH_STATUS_REPO ＝ "owner/repo"（デフォルト shohei-02/lunchnet-status-page）
"""
import base64
import json
import os
import urllib.error
import urllib.request

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from sales.management.commands.generate_status_json import build_status
from sales.models import SalesLocation

DEFAULT_REPO = "shohei-02/lunchnet-status-page"
FILE_PATH = "status.json"
API_BASE = "https://api.github.com"


def _request(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "lunchnet-status-publisher")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {"message": body}
        return e.code, parsed


class Command(BaseCommand):
    help = "本日の出店状況を生成し GitHub Contents API で公開ページ用リポジトリへコミットする"

    def add_arguments(self, parser):
        parser.add_argument(
            "--repo",
            default=os.environ.get("GH_STATUS_REPO", DEFAULT_REPO),
            help='対象リポジトリ "owner/repo"（デフォルト: GH_STATUS_REPO env か shohei-02/lunchnet-status-page）',
        )
        parser.add_argument(
            "--branch",
            default="main",
            help="コミット先ブランチ（デフォルト: main）",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="GitHub へ書き込まず、生成内容と差分有無だけ表示する",
        )

    def handle(self, *args, **options):
        repo = options["repo"]
        branch = options["branch"]
        dry_run = options["dry_run"]

        # 毎朝 8:00 JST の Scheduler 実行時に前日（以前）の today_override を全クリアする。
        # これで「昨日完売にしたまま」が翌日に引きずらない。今日付の override は維持。
        today = timezone.localdate()
        cleared = SalesLocation.objects.filter(
            ~Q(today_override="")
        ).exclude(today_override_date=today).update(
            today_override="", today_override_date=None,
        )
        if cleared:
            self.stdout.write(f"[publish_status_json] stale override クリア: {cleared}件")

        data = build_status()
        new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        new_bytes = new_text.encode("utf-8")

        open_count = sum(1 for loc in data["locations"] if loc["status"] == "open")
        summary = (
            f"{data['date']} ({data['weekday']}) business_day={data['business_day']} "
            f"open={open_count} closed={len(data['locations']) - open_count} "
            f"all_unregistered={data['all_unregistered']}"
        )

        token = os.environ.get("GH_TOKEN", "").strip()
        if not token and not dry_run:
            raise CommandError("環境変数 GH_TOKEN が未設定です（Heroku Config Var に設定してください）")

        contents_url = f"{API_BASE}/repos/{repo}/contents/{FILE_PATH}?ref={branch}"

        current_sha = None
        current_bytes = None
        if token:
            status, payload = _request("GET", contents_url, token)
            if status == 200:
                current_sha = payload.get("sha")
                if payload.get("encoding") == "base64" and payload.get("content"):
                    current_bytes = base64.b64decode(payload["content"])
            elif status == 404:
                pass  # 初回。新規作成する
            else:
                raise CommandError(
                    f"GitHub GET 失敗 ({status}): {payload.get('message')} [{repo}/{FILE_PATH}@{branch}]"
                )

        if current_bytes == new_bytes:
            self.stdout.write(f"[publish_status_json] 変更なし — {summary}")
            return

        if dry_run:
            self.stdout.write(f"[publish_status_json] (dry-run) 更新が必要 — {summary}")
            self.stdout.write(new_text)
            return

        commit_payload = {
            "message": f"status.json 更新 {data['generated_at']}",
            "content": base64.b64encode(new_bytes).decode("ascii"),
            "branch": branch,
            "committer": {"name": "lunchnet-bot", "email": "bot@lunchnetsalessystem.com"},
        }
        if current_sha:
            commit_payload["sha"] = current_sha

        put_url = f"{API_BASE}/repos/{repo}/contents/{FILE_PATH}"
        status, payload = _request("PUT", put_url, token, commit_payload)
        if status not in (200, 201):
            raise CommandError(
                f"GitHub PUT 失敗 ({status}): {payload.get('message')} {payload.get('errors', '')}"
            )

        commit_sha = (payload.get("commit") or {}).get("sha", "?")[:7]
        self.stdout.write(f"[publish_status_json] コミット {commit_sha} — {summary}")
