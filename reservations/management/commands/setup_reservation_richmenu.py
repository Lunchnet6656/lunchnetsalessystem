"""予約用「合体リッチメニュー」を作成し、画像をアップロードする（per-user 配信のセットアップ）。

登録済みテスト客にだけ出すメニュー。**既存メニューの4機能（出店状況/販売場所/今週のメニュー/友達紹介）
＋「予約する」**を1枚にまとめる（= per-user リンクで既存GUIメニューを置き換えても機能を失わない）。

画像は HTML/CSS で作って書き出した `reservations/assets/richmenu.png`（2500×1686）。
レイアウト：上=出店状況バナー（横長）／下=4ボタン（予約・販売場所・今週メニュー・友達紹介）。

使い方：
  manage.py setup_reservation_richmenu          # 作成＋画像アップ → richMenuId を表示
  manage.py setup_reservation_richmenu --list    # 既存メニュー一覧（API管理分のみ）
  manage.py setup_reservation_richmenu --delete <richMenuId>
作成後、richMenuId を `.env` の LINE_RESERVE_RICHMENU_ID に設定。**デフォルトには設定しない**
（全友だちに出さない）。登録時に per-user リンクされる。
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from reservations import line_richmenu

_IMAGE = Path(__file__).resolve().parent.parent.parent / "assets" / "richmenu.png"

# 既存メニューの遷移先（GUIメニュー由来・しょうへい提供 2026-06-10）。
_URL_STATUS = "https://status.lunchnetsalessystem.com/"
_URL_MAP = ("https://www.google.com/maps/d/viewer?mid=1lG5pD1pUX2twtrlIioMYU0GagQ8F2FIG"
            "&ll=35.69456744876126%2C139.77624459475328&z=12")
_URL_MENU = "https://www.lunchnet.jp/index.html?utm_source=LINE&utm_medium=social&utm_campaign=LINE#thisweek"
_URL_FRIEND = "https://line.me/R/nv/recommendOA/@243frciw"


def _menu_definition() -> dict:
    """5エリア（上1バナー＋下4ボタン）。座標は richmenu.png のレイアウトに対応。"""
    return {
        "size": {"width": 2500, "height": 1686},
        "selected": True,
        "name": "ランチネット予約メニュー（登録客）",
        "chatBarText": "メニュー",
        "areas": [
            # 上：出店状況バナー（横長・高さ650）
            {"bounds": {"x": 0, "y": 0, "width": 2500, "height": 650},
             "action": {"type": "uri", "label": "本日の出店状況", "uri": _URL_STATUS}},
            # 下：4ボタン（y=650〜1686 を4等分・隙間も取りこぼさないよう列で覆う）
            {"bounds": {"x": 0, "y": 650, "width": 625, "height": 1036},
             "action": {"type": "uri", "label": "予約する", "uri": settings.LIFF_RESERVE_URL}},
            {"bounds": {"x": 625, "y": 650, "width": 625, "height": 1036},
             "action": {"type": "uri", "label": "販売場所", "uri": _URL_MAP}},
            {"bounds": {"x": 1250, "y": 650, "width": 625, "height": 1036},
             "action": {"type": "uri", "label": "今週のメニュー", "uri": _URL_MENU}},
            {"bounds": {"x": 1875, "y": 650, "width": 625, "height": 1036},
             "action": {"type": "uri", "label": "友達に紹介", "uri": _URL_FRIEND}},
        ],
    }


class Command(BaseCommand):
    help = "予約用の合体リッチメニューを作成し画像をアップロード（per-user 配信用）"

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
            f"画像アップ完了。\n  → .env に  LINE_RESERVE_RICHMENU_ID={rich_menu_id}  を設定してください。"))
