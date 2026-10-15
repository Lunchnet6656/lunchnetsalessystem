"""画面の権限チェック（#188 H10）。

サイドバーの can_view_〇〇 はリンクを隠すだけなので、URL直打ちでも同じ条件で弾く。
「サイドバーにリンクが出る条件＝その画面を使える条件」にそろえる。
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect

NO_PERMISSION_MESSAGE = "このページを開く権限がありません。本部の担当者に相談してください。"


def is_admin(user):
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


def has_menu_permission(user, field):
    perm = getattr(user, "menu_permission", None)
    return bool(user.is_authenticated and perm and getattr(perm, field))


def menu_permission_required(field, staff=False, json=False):
    """field の権限（staff=True なら管理者であることも）がない人を弾く。

    json=True は fetch から呼ばれる画面用。ログイン画面へのリダイレクトでは
    呼び出し側が失敗に気づけないので、未ログインも含めて 403 の JSON を返す。
    """
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            allowed = has_menu_permission(user, field) and (not staff or is_admin(user))
            if not allowed:
                if json:
                    return JsonResponse(
                        {"status": "error", "success": False, "message": NO_PERMISSION_MESSAGE},
                        status=403,
                    )
                messages.error(request, NO_PERMISSION_MESSAGE, extra_tags="alert alert-danger")
                return redirect("dashboard")
            return view(request, *args, **kwargs)
        return wrapper if json else login_required(wrapper)
    return decorator


def can_manage_account(operator, target):
    """スーパーユーザーのアカウントは本人以外が触れない（しょうへいのアカウントを守る）。"""
    return not target.is_superuser or operator.pk == target.pk
