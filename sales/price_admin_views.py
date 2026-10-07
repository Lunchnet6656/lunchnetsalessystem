"""価格表・割引設定の画面（本部の担当者向け）。

お金の設定なので、サイドバーで隠すだけでなく、ビューで必ず権限を確認する（URL直打ち対策）。
仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md §8.1・§8.2・§13
画面: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-画面設計.md §1・§2
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

NO_PERMISSION_MESSAGE = "このページを開く権限がありません。本部の担当者に相談してください。"


def has_price_master_permission(user):
    perm = getattr(user, "menu_permission", None)
    return bool(user.is_authenticated and perm and perm.can_view_price_master)


def price_master_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not has_price_master_permission(request.user):
            messages.error(request, NO_PERMISSION_MESSAGE, extra_tags="alert alert-danger")
            return redirect("dashboard")
        return view(request, *args, **kwargs)
    return wrapper


@price_master_required
def price_table_list(request):
    return render(request, "prices/price_table_list.html", {})


@price_master_required
def discount_item_list(request):
    return render(request, "prices/discount_item_list.html", {})
