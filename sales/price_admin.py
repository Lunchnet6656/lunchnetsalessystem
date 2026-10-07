"""価格表・割引設定の画面の裏側（確認画面の文章づくり・登録・変更履歴）。ビューからはここを呼ぶ。"""
from sales.models import PriceChangeLog


def user_label(user):
    if user is None:
        return "（不明）"
    name = f"{user.last_name} {user.first_name}".strip()
    return name or user.username


def log_change(user, kind, summary):
    return PriceChangeLog.objects.create(kind=kind, summary=summary,
                                         user=user if getattr(user, "is_authenticated", False) else None)


def recent_changes(kind, limit=20):
    qs = PriceChangeLog.objects.filter(kind=kind).select_related("user")
    return list(qs[:limit]) if limit else list(qs)
