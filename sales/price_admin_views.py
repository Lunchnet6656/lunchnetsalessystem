"""価格表・割引設定の画面（本部の担当者向け）。

お金の設定なので、サイドバーで隠すだけでなく、ビューで必ず権限を確認する（URL直打ち対策）。
仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md §8.1・§8.2・§13
画面: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-画面設計.md §1・§2
"""
import datetime
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from sales import menu_registry
from sales import menu_week as mw
from sales import price_admin as pa
from sales.models import CONTAINER_CHOICES, ClassifyRule, DiscountItem, MenuProfile, PriceTable

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
        messages.error(request, "「内容を確認しました」にチェックを入れてから登録してください。",
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


# ===== 割引設定 =====

@price_master_required
def discount_item_list(request):
    today = timezone.localdate()
    groups, ended = pa.discount_overview(today)
    return render(request, "prices/discount_item_list.html", {
        "groups": groups, "ended": ended, "history": pa.recent_changes("discount"),
    })


def _discount_page(request, template, context, plan=None, errors=None):
    """入力画面（エラー時も）か、入力が正しければ確認画面を出す。"""
    if plan is not None:
        return render(request, "prices/discount_confirm.html", {"plan": plan, "post": request.POST})
    return render(request, template, {**context, "errors": errors or [], "post": request.POST})


def _plan_from_post(request, today):
    """確認画面の［登録する］から来たPOSTを、もう一度チェックして予定に戻す。"""
    action = request.POST.get("action")
    if action == "add":
        return pa.plan_add(request.POST, today)
    item = get_object_or_404(DiscountItem, pk=request.POST.get("item"))
    if action == "change":
        return pa.plan_change(item, request.POST, today)
    if action == "end":
        return pa.plan_end(item, request.POST, today)
    return ["操作が分かりませんでした。もう一度やり直してください。"], None


@price_master_required
def discount_change(request, pk):
    item = get_object_or_404(DiscountItem, pk=pk)
    errors, plan = pa.plan_change(item, request.POST, timezone.localdate()) if request.method == "POST" else ([], None)
    return _discount_page(request, "prices/discount_change.html", {"item": item, "now": pa.yen(item)}, plan, errors)


@price_master_required
def discount_add(request):
    errors, plan = pa.plan_add(request.POST, timezone.localdate()) if request.method == "POST" else ([], None)
    return _discount_page(request, "prices/discount_add.html", {}, plan, errors)


@price_master_required
def discount_end(request, pk):
    item = get_object_or_404(DiscountItem, pk=pk)
    errors, plan = pa.plan_end(item, request.POST, timezone.localdate()) if request.method == "POST" else ([], None)
    return _discount_page(request, "prices/discount_end.html", {"item": item}, plan, errors)


@price_master_required
@require_POST
def discount_apply(request):
    errors, plan = _plan_from_post(request, timezone.localdate())
    if errors:
        messages.error(request, "　".join(errors), extra_tags="alert alert-danger")
    else:
        pa.apply_plan(request.user, plan)
        messages.success(request, f"{plan['summary']}を登録しました。", extra_tags="alert alert-success")
    return redirect("discount_item_list")


@price_master_required
def discount_cancel_plan(request, pk):
    item = get_object_or_404(DiscountItem, pk=pk)
    if request.method == "POST":
        summary = pa.cancel_plan(request.user, item, timezone.localdate())
        if summary:
            messages.success(request, f"{summary}しました。", extra_tags="alert alert-success")
        return redirect("discount_item_list")
    return render(request, "prices/discount_cancel.html", {"item": item})


@price_master_required
@require_POST
def discount_move(request, pk, step):
    pa.move_item(get_object_or_404(DiscountItem, pk=pk), -1 if step == "up" else 1, timezone.localdate())
    return redirect("discount_item_list")


# ===== 週のメニュー確認 =====

def _parse_week(text):
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise Http404("週の日付が読めません")


@price_master_required
def menu_week_list(request):
    return render(request, "prices/menu_week_list.html", {"weeks": mw.recent_weeks(timezone.localdate())})


@price_master_required
def menu_week_detail(request, week):
    week = _parse_week(week)
    today = timezone.localdate()
    view = mw.week_view(week, today)
    ranks = pa.ranks()
    if request.method == "POST":
        if view["state"] == "ended" or view["legacy"] or not view["rows"]:
            messages.error(request, "この週はここでは直せません。", extra_tags="alert alert-danger")
            return redirect("menu_week_detail", week=week.isoformat())
        changes, errors = mw.read_choices(request.POST, [r.product for r in view["rows"]], ranks)
        if errors:
            messages.error(request, "　".join(errors), extra_tags="alert alert-danger")
            return redirect("menu_week_detail", week=week.isoformat())
        changed = any(c.rank_changed or c.container_changed for c in changes)
        if view["state"] == "selling" and changed and not request.POST.get("selling_ok"):
            return render(request, "prices/menu_week_selling_confirm.html", {
                **view, "summary": mw.selling_week_summary(week, changes, today), "post": request.POST.items(),
            })
        mw.confirm_week(week, changes, request.user)
        messages.success(request, f"{view['label']}を確認済みにしました。", extra_tags="alert alert-success")
        return redirect("menu_week_detail", week=week.isoformat())
    return render(request, "prices/menu_week_detail.html", {
        **view, "ranks": ranks, "containers": CONTAINER_CHOICES,
        "prices_json": mw.prices_json(view["segments"], ranks),
    })


# ===== メニュー辞書・判定ルール =====

def _week_labels(weeks):
    return "・".join(f"{w.month}/{w.day}週" for w in weeks)


@price_master_required
def menu_dictionary(request):
    today = timezone.localdate()
    ranks = pa.ranks()
    if request.method == "POST":
        rank_by_id = {str(r.id): r for r in ranks}
        # 要確認の表は1つのフォームに全行の profile が入っているので、行のボタンは confirm_one で送る
        ids = request.POST.getlist("profile") if request.POST.get("action") == "confirm_all" \
            else [request.POST.get("confirm_one") or request.POST.get("profile")]
        for profile in MenuProfile.objects.filter(pk__in=[i for i in ids if i]):
            rank = rank_by_id.get(request.POST.get(f"rank_{profile.pk}"))
            container = request.POST.get(f"container_{profile.pk}")
            if rank is None or container not in CONTAINER_CHOICES:
                messages.error(request, f"『{profile.name}』の種類か容器を選び直してください。",
                               extra_tags="alert alert-danger")
                continue
            upcoming, selling = menu_registry.update_profile(profile, rank, container, request.user, today)
            text = f"『{profile.name}』を {rank.name}・{container} で覚えました。"
            if upcoming:
                text += f"{_week_labels(upcoming)}（開始前）のメニューにも反映しました。"
            if selling:
                text += f"{_week_labels(selling)}（販売中）のメニューは変わりません。変えるときは週のメニュー確認で直してください。"
            messages.success(request, text, extra_tags="alert alert-success")
        return redirect(request.get_full_path())

    prices = pa.current_rank_prices(today, ranks)
    for rank in ranks:
        rank.prices_text = prices[rank.id]
    query = (request.GET.get("q") or "").strip()
    profiles = MenuProfile.objects.select_related("rank")
    found = profiles.filter(name__contains=query) if query else profiles
    paginator = Paginator(found.order_by("name"), 50)
    page = paginator.get_page(request.GET.get("page"))
    edit_id = request.GET.get("edit")
    return render(request, "prices/menu_dictionary.html", {
        "needs_check": list(profiles.filter(confirmed=False).order_by("first_seen", "name")),
        "page": page, "total": profiles.count(), "query": query,
        "edit_id": int(edit_id) if edit_id and edit_id.isdigit() else None,
        "ranks": ranks, "containers": CONTAINER_CHOICES,
    })


@price_master_required
def menu_rules(request):
    ranks = pa.ranks()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            keyword = (request.POST.get("keyword") or "").strip()[:50]
            rank = next((r for r in ranks if str(r.id) == request.POST.get("rank")), None)
            container = request.POST.get("container") or ""
            if not keyword:
                messages.error(request, "メニュー名に含まれる言葉を入れてください。", extra_tags="alert alert-danger")
            elif rank is None and container not in CONTAINER_CHOICES:
                messages.error(request, "値段の種類か容器の、どちらかは決めてください。", extra_tags="alert alert-danger")
            else:
                last = ClassifyRule.objects.order_by("-sort_order").first()
                ClassifyRule.objects.create(keyword=keyword, rank=rank,
                                            container=container if container in CONTAINER_CHOICES else "",
                                            sort_order=(last.sort_order + 1) if last else 1)
                messages.success(request, f"ルール「{keyword}」を足しました。", extra_tags="alert alert-success")
        elif action == "delete":
            rule = get_object_or_404(ClassifyRule, pk=request.POST.get("rule"))
            rule.delete()
            messages.success(request, f"ルール「{rule.keyword}」を消しました。", extra_tags="alert alert-success")
        elif action in ("up", "down"):
            rules = list(ClassifyRule.objects.all())
            index = next(i for i, r in enumerate(rules) if str(r.pk) == request.POST.get("rule"))
            target = index + (-1 if action == "up" else 1)
            if 0 <= target < len(rules):
                rules[index], rules[target] = rules[target], rules[index]
                for order, rule in enumerate(rules, start=1):
                    if rule.sort_order != order:
                        rule.sort_order = order
                        rule.save(update_fields=["sort_order"])
        return redirect("menu_rules")

    trial = (request.GET.get("try") or "").strip()
    result = menu_registry.classify(menu_registry.normalize_name(trial)) if trial else None
    return render(request, "prices/menu_rules.html", {
        "rules": ClassifyRule.objects.select_related("rank"), "ranks": ranks, "containers": CONTAINER_CHOICES,
        "trial": trial, "result": result,
    })
