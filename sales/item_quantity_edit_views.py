"""持参数の修正画面（修正・消し込み）。保存は fetch の JSON（通信が切れても未保存を画面に残すため）。"""
import json

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods, require_POST

from sales import item_quantity_edit as iqe
from sales.permissions import menu_permission_required


def _date(value):
    try:
        d = parse_date(value)
    except ValueError:
        d = None
    if d is None:
        raise Http404
    return d


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None


@menu_permission_required("can_view_item_quantity")
def item_quantity_edit(request, date):
    ctx = iqe.screen(_date(date), timezone.localdate(), request.user)
    return render(request, "item_quantity_edit.html", ctx)


@require_POST
@menu_permission_required("can_view_item_quantity", json=True)
def item_quantity_edit_save(request, date):
    d = _date(date)
    body = _body(request)
    if not isinstance(body, dict):
        return JsonResponse({"ok": False, "error": "送信内容が不正"}, status=400)
    result = iqe.save_changes(d, request.user, body.get("upload_id"), body.get("changes"), timezone.localdate())
    if result.reupload:
        return JsonResponse({"ok": False, "reupload": True}, status=409)
    if result.conflicts:
        return JsonResponse({"ok": False, "conflicts": result.conflicts}, status=409)
    if not result.ok:
        return JsonResponse({"ok": False, "error": result.error}, status=400)

    text = f"保存 {result.saved}件"
    if result.publish == "failed":
        messages.warning(request, text + "・出店状況の更新失敗")
    else:
        messages.success(request, text + ("・出店状況 更新" if result.publish == "ok" else ""))
    return JsonResponse({"ok": True, "saved": result.saved})


@require_http_methods(["GET", "POST"])
@menu_permission_required("can_view_item_quantity", json=True)
def item_quantity_checks(request, date):
    d = _date(date)
    if request.method == "GET":
        return JsonResponse({"ok": True, "checked": iqe.checked_keys(d)})
    body = _body(request)
    saved = iqe.save_checks(d, request.user, (body or {}).get("checks") if isinstance(body, dict) else None,
                            timezone.localdate())
    if saved is None:
        return JsonResponse({"ok": False}, status=400)
    return JsonResponse({"ok": True, "saved": saved})
