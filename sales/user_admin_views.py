"""ユーザー管理の画面（#188 H10 S1b）。画面設計＝harness/specs/lunchnetsale-画面の権限チェック-画面設計.md"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.models import User
from django.db.models import OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from sales import user_admin as ua
from sales.models import UserChangeLog, UserMenuPermission
from sales.permissions import can_manage_account, menu_permission_required

user_admin_required = menu_permission_required("can_view_user_list", staff=True)

FILTERS = [
    ("all", "すべて"),
    ("admin", "管理者"),
    ("direct", "直行直帰"),
    ("locked", "ロック中"),
    ("idle", "30日以上利用なし"),
]
IDLE_DAYS = 30
LONG_IDLE_DAYS = 90


def _ok(request, text):
    messages.success(request, text)


def _ng(request, text):
    messages.error(request, text)


def _search(qs, q):
    # 「山田 太郎」のように空白で区切っても、どの語も名前かユーザー名に入っていれば当たる
    for word in q.split():
        qs = qs.filter(Q(username__icontains=word) | Q(last_name__icontains=word) | Q(first_name__icontains=word))
    return qs


def _row(user, me, locked):
    perm = getattr(user, "menu_permission", None)
    idle = ua.idle_days(user)
    return {
        "user": user,
        "name": ua.full_name(user),
        "is_me": user.pk == me.pk,
        "direct_return": bool(perm and perm.direct_return),
        "locked": user.username in locked,
        "last_login": ua.last_login_label(user),
        "long_idle": idle >= LONG_IDLE_DAYS,
        "idle": idle,
    }


@user_admin_required
def user_list(request):
    q = request.GET.get("q", "").strip()
    current = request.GET.get("filter", "all")
    if current not in dict(FILTERS):
        current = "all"
    locked = ua.locked_usernames()
    base = _search(User.objects.select_related("menu_permission"), q)

    active = [_row(u, request.user, locked) for u in base.filter(is_active=True).order_by("date_joined")]
    matchers = {
        "all": lambda r: True,
        "admin": lambda r: r["user"].is_staff,
        "direct": lambda r: r["direct_return"],
        "locked": lambda r: r["locked"],
        "idle": lambda r: r["idle"] >= IDLE_DAYS,
    }
    chips = [{"key": k, "label": label, "count": sum(1 for r in active if matchers[k](r))} for k, label in FILTERS]
    rows = [r for r in active if matchers[current](r)]

    deactivated_at = (UserChangeLog.objects.filter(target=OuterRef("pk"), action="deactivate")
                      .order_by("-changed_at").values("changed_at")[:1])
    inactive_qs = base.filter(is_active=False).annotate(deactivated_at=Subquery(deactivated_at)) \
        .order_by("-deactivated_at", "-date_joined")
    inactive = [_row(u, request.user, locked) for u in inactive_qs]

    return render(request, "users/list.html", {
        "rows": rows,
        "inactive": inactive,
        "chips": chips,
        "q": q,
        "current": current,
        "active_count": User.objects.filter(is_active=True).count(),
        "inactive_count": User.objects.filter(is_active=False).count(),
    })


@menu_permission_required("can_view_register", staff=True)
def register(request):
    form = ua.RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = ua.create_user(form, request.user)
        _ok(request, f"{ua.full_name(user)} さんを作成しました。権限を確認して、本人にログイン方法を伝えてください。")
        return redirect(f"{reverse('user_edit', args=[user.pk])}?created=1")
    return render(request, "users/register.html", {"form": form, "password_rules": ua.PASSWORD_RULES})


def _target_or_refuse(request, user_id):
    """操作できない相手なら (None, レスポンス)。スーパーユーザーは本人以外触れない。"""
    UserMenuPermission.objects.get_or_create(user=get_object_or_404(User, pk=user_id))
    target = User.objects.select_related("menu_permission").get(pk=user_id)
    if not can_manage_account(request.user, target):
        _ng(request, "このアカウントは本人だけが変更できます。")
        return None, redirect("user_list")
    return target, None


@user_admin_required
def user_edit(request, user_id):
    target, refused = _target_or_refuse(request, user_id)
    if refused:
        return refused
    is_self = target.pk == request.user.pk
    form = ua.UserEditForm(request.POST or None, target=target, lock_permissions=is_self)
    if request.method == "POST" and form.is_valid():
        changed = ua.save_edit(form, request.user)
        _ok(request, f"{ua.full_name(target)} さんを保存しました。" if changed else "変更はありませんでした。")
        return redirect("user_edit", user_id=target.pk)
    return _render_edit(request, target, form)


def _render_edit(request, target, form, password_form=None, status=200):
    return render(request, "users/edit.html", {
        "target": target,
        "name": ua.full_name(target),
        "form": form,
        "password_form": password_form or ua.PasswordSetForm(user=target),
        "password_rules": ua.PASSWORD_RULES,
        "is_self": target.pk == request.user.pk,
        "created": request.GET.get("created") == "1",
        "login_url": request.build_absolute_uri(reverse("login")),
        "locked": ua.is_locked(target),
        "failure_limit": settings.AXES_FAILURE_LIMIT,
        "last_login": ua.last_login_label(target),
        "logs": target.change_logs.select_related("changed_by")[:20],
    }, status=status)


@user_admin_required
@require_POST
def user_password(request, user_id):
    target, refused = _target_or_refuse(request, user_id)
    if refused:
        return refused
    form = ua.PasswordSetForm(request.POST, user=target)
    if not form.is_valid():
        edit_form = ua.UserEditForm(target=target, lock_permissions=target.pk == request.user.pk)
        return _render_edit(request, target, edit_form, password_form=form, status=400)
    target.set_password(form.cleaned_data["new_password"])
    target.save()
    if target.pk == request.user.pk:
        update_session_auth_hash(request, target)  # 自分のパスワードを変えてもログアウトさせない
    ua.log(target, request.user, "password", "パスワードを設定")
    _ok(request, f"{ua.full_name(target)} さんのパスワードを設定しました。本人に伝えてください。")
    return redirect("user_edit", user_id=target.pk)


def _set_active(request, user_id, active):
    target, refused = _target_or_refuse(request, user_id)
    if refused:
        return refused
    if target.pk == request.user.pk:
        _ng(request, "自分のアカウントは無効にできません。")
        return redirect("user_edit", user_id=target.pk)
    if target.is_active != active:
        target.is_active = active
        target.save(update_fields=["is_active"])
        ua.log(target, request.user, "activate" if active else "deactivate", "有効に戻す" if active else "無効化")
    _ok(request, f"{ua.full_name(target)} さんを{'有効に戻しました' if active else '無効にしました'}。")
    if request.POST.get("next") == "list":
        return redirect("user_list")
    return redirect("user_edit", user_id=target.pk)


@user_admin_required
@require_POST
def user_deactivate(request, user_id):
    return _set_active(request, user_id, False)


@user_admin_required
@require_POST
def user_activate(request, user_id):
    return _set_active(request, user_id, True)


@user_admin_required
@require_POST
def user_unlock(request, user_id):
    target, refused = _target_or_refuse(request, user_id)
    if refused:
        return refused
    ua.unlock(target, request.user)
    _ok(request, f"{ua.full_name(target)} さんのロックを解除しました。すぐにログインできます。")
    return redirect("user_edit", user_id=target.pk)


@menu_permission_required("can_view_user_list", staff=True, json=True)
def toggle_direct_return(request, user_id):
    if request.method != "POST":
        return JsonResponse({"success": False}, status=400)
    target = get_object_or_404(User, pk=user_id)
    perm, _ = UserMenuPermission.objects.get_or_create(user=target)
    checked = request.POST.get("checked") == "true"
    if perm.direct_return != checked:
        perm.direct_return = checked
        perm.save(update_fields=["direct_return"])
        ua.log(target, request.user, "assignment", f"直行直帰を{ua.on_off(checked)}")
    status = "オン" if checked else "オフ"
    return JsonResponse({"message": f"{ua.full_name(target)} さんの直行直帰を{status}にしました。", "success": True})
