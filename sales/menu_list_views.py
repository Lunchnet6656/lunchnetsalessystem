"""販売員用のメニューリスト画面。"""
from django.shortcuts import render
from django.utils import timezone

from sales import menu_list
from sales.permissions import menu_permission_required


@menu_permission_required("can_view_menu_list")
def staff_menu_list(request):
    ctx = menu_list.screen(request.GET.get("week"), timezone.localdate())
    return render(request, "menu_list.html", ctx)
