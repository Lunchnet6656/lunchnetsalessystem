"""テスト用：ユーザーにサイドバーの権限（can_view_〇〇）を付ける。画面はサーバー側でも権限を確かめるため。"""
from sales.models import UserMenuPermission


def grant(user, *fields):
    perm, _ = UserMenuPermission.objects.get_or_create(user=user)
    for field in fields:
        setattr(perm, field, True)
    perm.save()
    # シグナルが作った権限がユーザーにキャッシュされているので外す
    user._state.fields_cache.pop("menu_permission", None)
    return user
