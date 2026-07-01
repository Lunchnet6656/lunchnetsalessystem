"""スタンプ用リッチメニューの per-user 割当て（自作Lステップのセグメント配信の土台）。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md

LINE公式の管理画面では「セグメント別リッチメニュー」はできない（デフォルト1枚のみ）。
Messaging API の per-user リンク（linkRichMenuIdToUser）を使い、スタンプを始めた人にだけ
スタンプ用リッチメニューを出す。API操作はメッセージ数を消費しない＝追加コスト0。

LINE API 呼び出しは既存の reservations.line_richmenu を流用（チャネルトークン共用）。
"""
import logging

from django.conf import settings
from django.utils import timezone

from reservations import line_richmenu
from stamps.models import RichMenu, RichMenuLink

logger = logging.getLogger(__name__)


# 各ボタンの既定リンク先（画面で上書き可）。
DEFAULT_URLS = {
    "menu": "https://www.lunchnet.jp/index.html?utm_source=LINE&utm_medium=social&utm_campaign=LINE#thisweek",
    "status": "https://status.lunchnetsalessystem.com/",
    "map": ("https://www.google.com/maps/d/viewer?mid=1lG5pD1pUX2twtrlIioMYU0GagQ8F2FIG"
            "&ll=35.69456744876126%2C139.77624459475328&z=12"),
    "stamp": "",  # stamp_url() で動的生成
}


def stamp_url():
    liff = getattr(settings, "STAMP_LIFF_ID", "") or getattr(settings, "LIFF_ID", "")
    return f"https://liff.line.me/{liff}/stamp/me/"


def get_or_create_stamp_menu():
    obj, _ = RichMenu.objects.get_or_create(purpose=RichMenu.PURPOSE_STAMP)
    return obj


def build_areas(urls, width=2500, height=1686, head=330):
    """v3レイアウト（上=ブランド帯／下=2×2）のタップ領域JSONを作る。Bでは自由編集に拡張。"""
    row = (height - head) // 2
    return [
        {"bounds": {"x": 0, "y": head, "width": width // 2, "height": row},
         "action": {"type": "uri", "label": "今週のメニュー", "uri": urls.get("menu", "")}},
        {"bounds": {"x": width // 2, "y": head, "width": width // 2, "height": row},
         "action": {"type": "uri", "label": "本日の出店状況", "uri": urls.get("status", "")}},
        {"bounds": {"x": 0, "y": head + row, "width": width // 2, "height": row},
         "action": {"type": "uri", "label": "販売場所", "uri": urls.get("map", "")}},
        {"bounds": {"x": width // 2, "y": head + row, "width": width // 2, "height": row},
         "action": {"type": "uri", "label": "スタンプ", "uri": urls.get("stamp") or stamp_url()}},
    ]


def register_stamp_menu(image_png_bytes=None, urls=None):
    """画面からのリッチメニュー登録/更新。画像をAPIにアップしIDをDB保存。(ok, error) を返す。"""
    menu = get_or_create_stamp_menu()
    urls = urls or {}
    if image_png_bytes:
        menu.image_data = image_png_bytes
    if not menu.image_data:
        return False, "画像が未設定です。PNG画像をアップロードしてください。"
    if not line_richmenu._token():
        return False, "LINE_CHANNEL_ACCESS_TOKEN が未設定です。"

    menu.areas = build_areas({**DEFAULT_URLS, **urls})
    # 旧メニューがあれば削除（オーファン防止・best-effort）
    old_id = menu.line_rich_menu_id
    try:
        new_id = line_richmenu.create_rich_menu({
            "size": {"width": menu.image_width, "height": menu.image_height},
            "selected": True,
            "name": menu.name[:300],
            "chatBarText": menu.chat_bar_text[:14],
            "areas": menu.areas,
        })
        line_richmenu.upload_image(new_id, bytes(menu.image_data))
    except Exception as e:
        logger.warning("richmenu register error: %s", e)
        return False, f"LINE登録に失敗しました：{e}"

    menu.line_rich_menu_id = new_id
    from django.utils import timezone as _tz
    menu.registered_at = _tz.now()
    menu.save()
    if old_id and old_id != new_id:
        try:
            line_richmenu.delete_rich_menu(old_id)
        except Exception:
            pass
    return True, ""


def stamp_richmenu_id():
    """有効なスタンプ用リッチメニューID。DB登録分を優先し、無ければ環境変数。"""
    menu = RichMenu.objects.filter(purpose=RichMenu.PURPOSE_STAMP).first()
    if menu and menu.line_rich_menu_id:
        return menu.line_rich_menu_id
    return getattr(settings, "LINE_STAMP_RICHMENU_ID", "") or ""


def is_configured():
    """割当てに必要な設定（チャネルトークン＋メニューID）が揃っているか。"""
    return bool(line_richmenu._token() and stamp_richmenu_id())


def assign(member, now=None):
    """会員にスタンプ用リッチメニューを割り当て（best-effort）。結果を RichMenuLink に記録。"""
    now = now or timezone.now()
    link, _ = RichMenuLink.objects.get_or_create(member=member)
    rmid = stamp_richmenu_id()
    if not line_richmenu._token():
        link.status = RichMenuLink.STATUS_FAILED
        link.detail = "LINE_CHANNEL_ACCESS_TOKEN 未設定"
        link.save()
        return False
    if not rmid:
        link.status = RichMenuLink.STATUS_FAILED
        link.detail = "LINE_STAMP_RICHMENU_ID 未設定"
        link.save()
        return False

    ok = line_richmenu.link_to_user(member.line_user_id, rmid)
    if ok:
        link.rich_menu_id = rmid
        link.status = RichMenuLink.STATUS_LINKED
        link.linked_at = now
        link.detail = ""
    else:
        link.status = RichMenuLink.STATUS_FAILED
        link.detail = "LINE APIリンク失敗（トークン/ID/通信を確認）"
    link.save()
    return ok


def unassign(member):
    """会員のリッチメニュー割当てを解除（デフォルトへ戻す）。"""
    ok = line_richmenu.unlink_from_user(member.line_user_id)
    link = RichMenuLink.objects.filter(member=member).first()
    if link:
        link.status = RichMenuLink.STATUS_UNLINKED
        link.detail = "" if ok else "解除API失敗"
        link.save()
    return ok


def assign_on_first_stamp(member, now=None):
    """初回スタンプ時のフック。自動割当てがONで設定が揃っていれば割り当てる（best-effort）。"""
    if not getattr(settings, "STAMP_RICHMENU_AUTO_ASSIGN", True):
        return False
    if not is_configured():
        return False
    try:
        return assign(member, now=now)
    except Exception as e:  # 紐付け失敗でスタンプ本体を止めない
        logger.warning("stamp richmenu assign error: %s", e)
        return False
