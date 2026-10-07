"""価格表・割引設定の画面（本部の担当者向け）。

お金の設定なので、サイドバーで隠すだけでなく、ビューで必ず権限を確認する（URL直打ち対策）。
仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md §8.1・§8.2・§13
画面: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-画面設計.md §1・§2
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from sales import price_admin as pa
from sales.models import PriceTable

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


# ===== 価格表 =====

@price_master_required
def price_table_list(request):
    today = timezone.localdate()
    rank_list = pa.ranks()
    current = pa.table_in_effect(today)
    pending = pa.pending_tables(today)
    nearest = pending[0] if pending else None
    show_all_history = request.GET.get("history") == "all"
    return render(request, "prices/price_table_list.html", {
        "current": current,
        "current_rows": pa.matrix(rank_list, pa.cells_of(current)),
        "nearest": nearest,
        "nearest_rows": pa.matrix(rank_list, pa.cells_of(nearest), pa.cells_of(pa.table_before(nearest.valid_from)))
                        if nearest else [],
        "nearest_days": (nearest.valid_from - today).days if nearest else None,
        "other_pending": pending[1:],
        "past": pa.past_tables(today)[1:],
        "rank_list": rank_list,
        "locations": pa.locations_by_pattern(),
        "history": pa.recent_changes("price_table", limit=None if show_all_history else 20),
        "show_all_history": show_all_history,
    })


def _price_form_page(request, form_cells, base_cells, editing=None, valid_from="", note="", errors=None,
                     cell_errors=None):
    rank_list = pa.ranks()
    rows = pa.matrix(rank_list, form_cells, base_cells)
    for row in rows:
        for cell in row["cells"]:
            cell["invalid"] = cell["name"] in (cell_errors or set())
    return render(request, "prices/price_table_form.html", {
        "editing": editing,
        "rows": rows,
        "valid_from": valid_from,
        "note": note,
        "errors": errors or [],
        "locations": pa.locations_by_pattern(),
    })


@price_master_required
def price_table_new(request):
    today = timezone.localdate()
    pending = pa.pending_tables(today)
    base = pending[-1] if pending else pa.table_in_effect(today)
    return _price_form_page(request, pa.cells_of(base), pa.cells_of(base))


@price_master_required
def price_table_edit(request, pk):
    table = get_object_or_404(PriceTable, pk=pk)
    if table.valid_from <= timezone.localdate():
        messages.error(request, "始まった価格表は直せません。値段を変えるときは、新しい価格表を作ってください。",
                       extra_tags="alert alert-danger")
        return redirect("price_table_list")
    base = pa.table_before(table.valid_from, exclude=table)
    return _price_form_page(request, pa.cells_of(table), pa.cells_of(base), editing=table,
                            valid_from=table.valid_from.isoformat(), note=table.note)


def _editing_from(request):
    pk = request.POST.get("editing")
    if not pk:
        return None
    table = get_object_or_404(PriceTable, pk=pk)
    return table if table.valid_from > timezone.localdate() else None


@price_master_required
@require_POST
def price_table_confirm(request):
    """作る・直す画面から来る。ここではまだ保存しない（作りかけの価格表をDBに残さない）。"""
    today = timezone.localdate()
    editing = _editing_from(request)
    rank_list = pa.ranks()
    form = pa.read_price_form(request.POST, rank_list)
    base = pa.validate_price_form(form, today, editing)
    if form.errors:
        return _price_form_page(request, form.cells, pa.cells_of(base), editing=editing,
                                valid_from=request.POST.get("valid_from", ""), note=form.note,
                                errors=form.errors, cell_errors=form.cell_errors)
    return render(request, "prices/price_table_confirm.html", {
        "editing": editing,
        "c": pa.confirmation(form, base, today, rank_list),
        "form": form,
        "hidden": [(f"cell_{r}_{p}", v) for (r, p), v in form.cells.items()],
        "new_rows": pa.matrix(rank_list, form.cells, pa.cells_of(base)),
        "base_rows": pa.matrix(rank_list, pa.cells_of(base)),
        "locations": pa.locations_by_pattern(),
    })


@price_master_required
@require_POST
def price_table_register(request):
    today = timezone.localdate()
    editing = _editing_from(request)
    rank_list = pa.ranks()
    form = pa.read_price_form(request.POST, rank_list)
    base = pa.validate_price_form(form, today, editing)
    if form.errors:
        messages.error(request, "　".join(form.errors), extra_tags="alert alert-danger")
        return redirect("price_table_list")
    c = pa.confirmation(form, base, today, rank_list)
    if (c["warnings"] or c["is_today"]) and not request.POST.get("checked"):
        messages.error(request, "「内容を確かめました」にチェックを入れてから登録してください。",
                       extra_tags="alert alert-danger")
        return redirect("price_table_list")
    pa.register_table(request.user, form, editing)
    pa.log_change(request.user, "price_table", c["summary"] + ("を直しました" if editing else "を登録しました"))
    messages.success(request, f"{c['start']}からの価格表を登録しました。", extra_tags="alert alert-success")
    messages.info(request, "アプリの外の値段（お店のPOP・振分表・請求書の文面）も直しましたか？",
                  extra_tags="alert alert-info")
    return redirect("price_table_list")


@price_master_required
def price_table_cancel(request, pk):
    table = get_object_or_404(PriceTable, pk=pk)
    if table.valid_from <= timezone.localdate():
        messages.error(request, "始まった価格表は取り消せません。", extra_tags="alert alert-danger")
        return redirect("price_table_list")
    base = pa.table_before(table.valid_from, exclude=table)
    if request.method == "POST":
        summary = pa.cancel_table(request.user, table)
        messages.success(request, f"{summary}しました。", extra_tags="alert alert-success")
        return redirect("price_table_list")
    return render(request, "prices/price_table_cancel.html", {
        "table": table, "start": pa.md(table.valid_from),
        "base_label": f"{pa.md(base.valid_from)}からの価格表" if base else "今の価格表",
    })


@price_master_required
def discount_item_list(request):
    return render(request, "prices/discount_item_list.html", {})
