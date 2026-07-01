"""スタンプ利用者専用リッチメニューを作成し、画像をアップロードする（per-user 配信のセットアップ）。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md

スタンプを始めた人にだけ出すメニュー（v3デザイン）。**デフォルトには設定しない**＝
全友だちは既存メニューのまま。初回スタンプ時に per-user リンクされる。

画像: stamps/assets/richmenu.png（2500×1686・上=ロゴ/マスコット帯／下=2×2ボタン）。
タップ領域（v3レイアウト基準・上のブランド帯は除外）:
  左上=今週のメニュー / 右上=本日の出店状況 / 左下=販売場所 / 右下=スタンプ

使い方:
  manage.py setup_stamp_richmenu            # 作成＋画像アップ → richMenuId を表示
  manage.py setup_stamp_richmenu --list      # 既存メニュー一覧（API管理分）
  manage.py setup_stamp_richmenu --delete <richMenuId>
作成後、richMenuId を LINE_STAMP_RICHMENU_ID に設定（本番は heroku config:set）。
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from reservations import line_richmenu

_IMAGE = Path(__file__).resolve().parent.parent.parent / "assets" / "richmenu.png"

# 各ボタンの遷移先（既存メニューと同じURL＋スタンプはマイカードLIFF）。
_URL_STATUS = "https://status.lunchnetsalessystem.com/"
_URL_MAP = ("https://www.google.com/maps/d/viewer?mid=1lG5pD1pUX2twtrlIioMYU0GagQ8F2FIG"
            "&ll=35.69456744876126%2C139.77624459475328&z=12")
_URL_MENU = "https://www.lunchnet.jp/index.html?utm_source=LINE&utm_medium=social&utm_campaign=LINE#thisweek"


def _stamp_url():
    liff = getattr(settings, "STAMP_LIFF_ID", "") or getattr(settings, "LIFF_ID", "")
    return f"https://liff.line.me/{liff}/stamp/me/"


def _menu_definition() -> dict:
    """v3レイアウトの2×2ボタン（上のブランド帯 y<330 はタップ領域に含めない）。"""
    HEAD = 330            # ブランド帯の高さ（おおよそ）
    ROW = (1686 - HEAD) // 2   # 678
    return {
        "size": {"width": 2500, "height": 1686},
        "selected": True,
        "name": "ランチネット スタンプメニュー（利用者）",
        "chatBarText": "メニュー",
        "areas": [
            {"bounds": {"x": 0, "y": HEAD, "width": 1250, "height": ROW},
             "action": {"type": "uri", "label": "今週のメニュー", "uri": _URL_MENU}},
            {"bounds": {"x": 1250, "y": HEAD, "width": 1250, "height": ROW},
             "action": {"type": "uri", "label": "本日の出店状況", "uri": _URL_STATUS}},
            {"bounds": {"x": 0, "y": HEAD + ROW, "width": 1250, "height": ROW},
             "action": {"type": "uri", "label": "販売場所", "uri": _URL_MAP}},
            {"bounds": {"x": 1250, "y": HEAD + ROW, "width": 1250, "height": ROW},
             "action": {"type": "uri", "label": "スタンプ", "uri": _stamp_url()}},
        ],
    }


class Command(BaseCommand):
    help = "スタンプ利用者用リッチメニューを作成し画像をアップロード（per-user 配信用）"

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="既存メニュー一覧（API管理分）")
        parser.add_argument("--delete", help="指定 richMenuId を削除")

    def handle(self, *args, **options):
        if not line_richmenu._token():
            self.stderr.write("LINE_CHANNEL_ACCESS_TOKEN が未設定です。")
            return
        if options.get("list"):
            for m in line_richmenu.list_rich_menus():
                self.stdout.write(f"  {m['richMenuId']}  name={m.get('name')}")
            return
        if options.get("delete"):
            line_richmenu.delete_rich_menu(options["delete"])
            self.stdout.write(self.style.SUCCESS(f"削除しました: {options['delete']}"))
            return
        if not _IMAGE.exists():
            self.stderr.write(f"画像が見つかりません: {_IMAGE}")
            return

        rich_menu_id = line_richmenu.create_rich_menu(_menu_definition())
        self.stdout.write(f"作成: {rich_menu_id}")
        line_richmenu.upload_image(rich_menu_id, _IMAGE.read_bytes())
        self.stdout.write(self.style.SUCCESS(
            f"画像アップ完了。\n  → LINE_STAMP_RICHMENU_ID={rich_menu_id}  を設定してください。"
            f"\n  スタンプ遷移先: {_stamp_url()}"))
