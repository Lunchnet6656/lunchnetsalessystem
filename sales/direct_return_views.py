"""直行直帰の出勤記録・勤務時間の画面。

給与に使う数字なので、サイドバーで隠すだけでなく、ビューで必ず権限を確認する（URL直打ち対策）。
- 出勤記録（集計）: can_view_direct_return_attendance。一般スタッフは自分の分だけ見る
- 日別明細・出勤追加・就業時間設定: 管理者（is_staff / is_superuser）だけ
仕様: .company/engineering/harness/specs/lunchnetsale-直行直帰-勤務時間計算-要件定義.md
画面: .company/engineering/harness/specs/lunchnetsale-直行直帰-勤務時間計算-画面設計.md
"""
import datetime
from collections import defaultdict
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from sales import direct_return as dr
from sales.models import (
    DirectReturnDayAdjustment,
    DirectReturnExtraAttendance,
    DirectReturnWorkTime,
    SalesLocation,
)

NO_PERMISSION_MESSAGE = "このページを開く権限がありません。本部の担当者に相談してください。"


def is_admin(user):
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


def can_view_attendance(user):
    perm = getattr(user, "menu_permission", None)
    return bool(user.is_authenticated and perm and perm.can_view_direct_return_attendance)


def _guard(check):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not check(request.user):
                messages.error(request, NO_PERMISSION_MESSAGE)
                return redirect("dashboard")
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


attendance_viewer_required = _guard(can_view_attendance)
admin_required = _guard(is_admin)


def _period_context(request):
    today = timezone.localdate()
    year, month = dr.parse_period(request.GET.get("period") or request.POST.get("period"), today)
    if request.GET.get("target_year") and request.GET.get("target_month"):  # 旧URLの互換
        year, month = dr.parse_period(f"{request.GET['target_year']}-{request.GET['target_month']}", today)
    start, end = dr.period_of(year, month)
    prev_y, prev_m = dr.shift_month(year, month, -1)
    next_y, next_m = dr.shift_month(year, month, 1)
    period = f"{year}-{month:02d}"
    choices = dr.period_choices(today)
    if period not in {c["value"] for c in choices}:  # 前の月を辿って選択肢の外に出たとき
        choices.append({"value": period, "label": dr.period_label(year, month)})
        choices.sort(key=lambda c: c["value"], reverse=True)
    return {
        "period": period,
        "period_label": dr.period_label(year, month),
        "period_choices": choices,
        "prev_period": f"{prev_y}-{prev_m:02d}",
        "next_period": f"{next_y}-{next_m:02d}",
        "start_date": start,
        "end_date": end,
    }


def _unset_warning(summaries):
    days = [d for s in summaries for d in s.unset_days]
    names = []
    for d in days:
        if d.location_name not in names:
            names.append(d.location_name or "（売り場名なし）")
    return {"count": len(days), "locations": names} if days else None


# ===== 画面A：出勤記録（集計） =====

@attendance_viewer_required
def attendance(request):
    ctx = _period_context(request)
    start, end = ctx["start_date"], ctx["end_date"]

    if not is_admin(request.user):
        me = request.user
        is_direct = bool(getattr(me, "menu_permission", None) and me.menu_permission.direct_return)
        summary = dr.build_summaries([me], start, end)[0] if is_direct else None
        ctx.update({
            "is_admin": False,
            "is_direct": is_direct,
            "summary": summary,
            "days_desc": list(reversed(summary.days)) if summary else [],
        })
        return render(request, "direct_return/attendance_self.html", ctx)

    summaries = dr.build_summaries(dr.direct_return_users(), start, end)

    # 日付×売り場（印刷用）。今までの表と同じく、セルは担当者名
    pivot = defaultdict(lambda: defaultdict(list))
    locations = set()
    for s in summaries:
        for d in s.days:
            locations.add(d.location_name)
            pivot[d.date][d.location_name].append(s.name)
    pivot_locations = sorted(locations)
    pivot_rows = [
        {"date_str": dr.format_date_ja(d), "cells": [", ".join(pivot[d].get(loc, [])) for loc in pivot_locations]}
        for d in sorted(pivot)
    ]

    ctx.update({
        "is_admin": True,
        "summaries": summaries,
        "has_days": any(s.days for s in summaries),
        "unset_warning": _unset_warning(summaries),
        "pivot_locations": pivot_locations,
        "pivot_rows": pivot_rows,
    })
    return render(request, "direct_return/attendance_admin.html", ctx)


# ===== 画面B：担当者の日別明細 =====

def _staff_or_404(user_id):
    return get_object_or_404(get_user_model(), pk=user_id)


@admin_required
def staff_detail(request, user_id):
    staff = _staff_or_404(user_id)
    ctx = _period_context(request)
    summary = dr.build_summaries([staff], ctx["start_date"], ctx["end_date"])[0]

    errors = {}       # {日付キー: {欄名: エラー文}}
    posted = {}       # エラー時に入力値を残す
    if request.method == "POST":
        parsed = {}
        for day in summary.days:
            values, row_errors = {}, {}
            for name in dr.ADJUST_FIELDS:
                raw = request.POST.get(f"{name}_{day.key}", "")
                posted[f"{name}_{day.key}"] = raw
                value, err = dr.parse_adjust_hours(raw)
                if err:
                    row_errors[name] = err
                values[name] = value
            note = request.POST.get(f"note_{day.key}", "").strip()[:255]
            posted[f"note_{day.key}"] = note
            if not row_errors and day.base is not None:
                total = day.base + values["early_hours"] + values["overtime_hours"] \
                    - values["late_hours"] - values["leave_early_hours"]
                if total < 0:
                    row_errors["late_hours"] = "勤務時間がマイナスになります。遅刻・早退を見直してください。"
            if row_errors:
                errors[day.key] = row_errors
            parsed[day.date] = (values, note)

        if not errors:
            with transaction.atomic():
                for d, (values, note) in parsed.items():
                    empty = all(v == 0 for v in values.values()) and not note
                    if empty:
                        DirectReturnDayAdjustment.objects.filter(user=staff, date=d).delete()
                    else:
                        DirectReturnDayAdjustment.objects.update_or_create(
                            user=staff, date=d, defaults={**values, "note": note, "updated_by": request.user},
                        )
            messages.success(request, f"{dr.person_name(staff)} の{ctx['period_label'].split('（')[0]}を保存しました。")
            return redirect(f"{reverse('direct_return_staff', args=[staff.pk])}?period={ctx['period']}")

    rows = []
    for day in summary.days:
        cells = []
        for name in dr.ADJUST_FIELDS:
            key = f"{name}_{day.key}"
            value = posted[key] if key in posted else (dr.format_hours(day.adjust_value(name)) if day.adjust_value(name) else "")
            cells.append({"name": key, "value": value, "error": errors.get(day.key, {}).get(name)})
        note_key = f"note_{day.key}"
        rows.append({
            "day": day,
            "cells": cells,
            "note_name": note_key,
            "note_value": posted.get(note_key, day.note),
            "has_error": day.key in errors,
        })

    ctx.update({
        "staff": staff,
        "staff_name": dr.person_name(staff),
        "summary": summary,
        "rows": rows,
        "error_dates": [dr.format_date_ja(datetime.date.fromisoformat(k)) for k in sorted(errors)],
    })
    return render(request, "direct_return/staff_detail.html", ctx)


# ===== 画面C：出勤追加 =====

def _location_options(on_date):
    """売り場の選択肢。直行直帰=1の売り場を先、その他を後ろ。各売り場のその日の定時つき。"""
    book = dr.WorkTimeBook()
    main, others = [], []
    for loc in SalesLocation.objects.order_by("no", "id"):
        wt = book.on(loc.pk, on_date)
        option = {
            "location": loc,
            "hours": dr.format_hours(dr.hours_between(wt.start_time, wt.end_time)) if wt else "",
            "range": f"{wt.start_time:%H:%M}〜{wt.end_time:%H:%M}" if wt else "",
        }
        (main if loc.direct_return else others).append(option)
    return main, others


@admin_required
def extra_new(request, user_id):
    staff = _staff_or_404(user_id)
    today = timezone.localdate()
    form = {
        "date": request.POST.get("date", today.isoformat()),
        "location": request.POST.get("location", ""),
        "reason": request.POST.get("reason", DirectReturnExtraAttendance.REASON_TRAINING),
        "note": request.POST.get("note", ""),
    }
    errors = {}
    if request.method == "POST":
        try:
            d = datetime.date.fromisoformat(form["date"])
        except ValueError:
            d = None
            errors["date"] = "日付を入れてください。"
        location = SalesLocation.objects.filter(pk=form["location"]).first() if form["location"].isdigit() else None
        if location is None:
            errors["location"] = "売り場を選んでください。"
        if form["reason"] not in dict(DirectReturnExtraAttendance.REASON_CHOICES):
            errors["reason"] = "区分を選んでください。"
        if d and "date" not in errors:
            report_location = dr.report_location_on(staff, d)
            if report_location is not None:
                errors["date"] = f"{d.month}/{d.day} は日計表（{report_location}）があるので、出勤追加はできません。"
            else:
                existing = DirectReturnExtraAttendance.objects.filter(user=staff, date=d).select_related("location").first()
                if existing:
                    errors["date"] = (
                        f"{d.month}/{d.day} はもう出勤追加があります"
                        f"（{existing.location.name}・{existing.get_reason_display()}）。"
                    )
        if not errors:
            extra = DirectReturnExtraAttendance.objects.create(
                user=staff, date=d, location=location, reason=form["reason"],
                note=form["note"].strip()[:255], created_by=request.user,
            )
            messages.success(
                request,
                f"{d.month}/{d.day} {location.name}（{extra.get_reason_display()}）の出勤を追加しました。",
            )
            y, m = dr.period_containing(d)
            return redirect(f"{reverse('direct_return_staff', args=[staff.pk])}?period={y}-{m:02d}")

    try:
        on_date = datetime.date.fromisoformat(form["date"])
    except ValueError:
        on_date = today
    main, others = _location_options(on_date)
    return render(request, "direct_return/extra_form.html", {
        "staff": staff,
        "staff_name": dr.person_name(staff),
        "form": form,
        "errors": errors,
        "main_locations": main,
        "other_locations": others,
        "reasons": DirectReturnExtraAttendance.REASON_CHOICES,
        "back_period": request.GET.get("period") or request.POST.get("period", ""),
    })


@admin_required
@require_POST
def extra_delete(request, pk):
    extra = get_object_or_404(DirectReturnExtraAttendance.objects.select_related("location", "user"), pk=pk)
    staff, d = extra.user, extra.date
    label = f"{d.month}/{d.day} {extra.location.name}（{extra.get_reason_display()}）"
    extra.delete()
    messages.success(request, f"{label}の出勤追加を取り消しました。")
    y, m = dr.period_containing(d)
    return redirect(f"{reverse('direct_return_staff', args=[staff.pk])}?period={y}-{m:02d}")


# ===== 画面D：就業時間設定（一覧） =====

@admin_required
def work_time_list(request):
    today = timezone.localdate()
    book = dr.WorkTimeBook()
    configured = set(DirectReturnWorkTime.objects.values_list("location_id", flat=True))
    rows, others = [], []
    for loc in SalesLocation.objects.order_by("no", "id"):
        if not (loc.direct_return or loc.pk in configured):
            others.append(loc)
            continue
        current = book.on(loc.pk, today)
        pending = (
            DirectReturnWorkTime.objects.filter(location=loc, effective_from__gt=today)
            .order_by("effective_from").first()
        )
        rows.append({
            "location": loc,
            "unset": current is None,
            "current_text": _describe(current) if current else "",
            "pending_text": f"{_md(pending.effective_from)}から {_describe(pending)}" if pending else "",
        })
    rows.sort(key=lambda r: not r["unset"])  # 未設定を上に（同じ中では No. 順のまま）
    return render(request, "direct_return/work_time_list.html", {"rows": rows, "other_locations": others})


# ===== 画面E：就業時間の登録・確認 =====

def _describe(wt):
    hours = dr.format_hours(dr.hours_between(wt.start_time, wt.end_time))
    return f"{wt.start_time:%H:%M}〜{wt.end_time:%H:%M}（{hours}h）"


def _md(d):
    return f"{d.month}/{d.day}" if d.year == timezone.localdate().year else f"{d.year}/{d.month}/{d.day}"


@admin_required
def work_time_form(request, location_id):
    location = get_object_or_404(SalesLocation, pk=location_id)
    today = timezone.localdate()
    next_y, next_m = dr.shift_month(*dr.period_containing(today), 1)
    default_start = dr.period_of(next_y, next_m)[0]
    form = {
        "effective_from": request.POST.get("effective_from", default_start.isoformat()),
        "start_time": request.POST.get("start_time", ""),
        "end_time": request.POST.get("end_time", ""),
        "note": request.POST.get("note", ""),
    }
    errors = {}
    action = request.POST.get("action") if request.method == "POST" else None

    if action in ("confirm", "register"):
        try:
            effective_from = datetime.date.fromisoformat(form["effective_from"])
        except ValueError:
            effective_from = None
            errors["effective_from"] = "開始日を入れてください。"
        times = {}
        for name in ("start_time", "end_time"):
            try:
                times[name] = datetime.time.fromisoformat(form[name])
            except ValueError:
                errors[name] = "時刻を入れてください。"
                continue
            if times[name].minute % 15 or times[name].second:
                errors[name] = "15分単位で入れてください。"
        if not errors and times["end_time"] <= times["start_time"]:
            errors["end_time"] = "終業は始業より後にしてください。"

        if not errors:
            new = DirectReturnWorkTime(
                location=location, effective_from=effective_from,
                start_time=times["start_time"], end_time=times["end_time"], note=form["note"].strip()[:255],
            )
            same_day = DirectReturnWorkTime.objects.filter(location=location, effective_from=effective_from).first()
            if action == "register":
                DirectReturnWorkTime.objects.update_or_create(
                    location=location, effective_from=effective_from,
                    defaults={"start_time": new.start_time, "end_time": new.end_time, "note": new.note},
                )
                messages.success(
                    request,
                    f"{location.name}の就業時間を登録しました（{_md(effective_from)}から {_describe(new)}）。",
                )
                return redirect("direct_return_work_times")

            before = dr.work_time_in_effect(location, effective_from - datetime.timedelta(days=1))
            return render(request, "direct_return/work_time_confirm.html", {
                "location": location,
                "form": form,
                "start_label": _md(effective_from),
                "until_label": _md(effective_from - datetime.timedelta(days=1)),
                "new_text": _describe(new),
                "same_day_text": _describe(same_day) if same_day else None,
                "before_text": _describe(before) if before else None,
                "is_past": effective_from <= today,
                "past_from": _md(effective_from),
            })

    history = []
    for wt in DirectReturnWorkTime.objects.filter(location=location).order_by("-effective_from"):
        history.append({"wt": wt, "label": _md(wt.effective_from), "text": _describe(wt)})
    current = dr.work_time_in_effect(location, today)
    for row in history:
        row["pending"] = row["wt"].effective_from > today
        row["current"] = current is not None and row["wt"].pk == current.pk
    return render(request, "direct_return/work_time_form.html", {
        "location": location,
        "form": form,
        "errors": errors,
        "history": history,
    })


@admin_required
@require_POST
def work_time_delete(request, pk):
    wt = get_object_or_404(DirectReturnWorkTime.objects.select_related("location"), pk=pk)
    if wt.effective_from <= timezone.localdate():
        messages.error(request, "開始日を過ぎた就業時間は取り消せません。直すときは同じ開始日で登録し直してください。")
    else:
        messages.success(request, f"{wt.location.name}の{_md(wt.effective_from)}からの就業時間を取り消しました。")
        wt.delete()
    return redirect("direct_return_work_time", location_id=wt.location_id)
