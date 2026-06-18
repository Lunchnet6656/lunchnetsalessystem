"""勤怠アプリのビュー。

リクエストの受け取りとレスポンス返却のみを担い、打刻の判定・集計・整合性
チェックは services / aggregation 層へ委ねる。
"""
import json
import time
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import aggregation, payroll, payslip_pdf, services
from .forms import BulkMinWageUpdateForm, PayrollSettingForm, StaffPayrollProfileForm
from .models import (
    BUSINESS_UNIT_CHOICES,
    COMPANY_CHOICES,
    COMPANY_SHORT_LABELS,
    HourlyWage,
    ManualWorkHours,
    Payslip,
    PayslipAdjustment,
    PayrollPeriod,
    PayrollSetting,
    Staff,
    StaffPayrollProfile,
    TimeRecord,
    TimeRecordEdit,
)

_WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]


def _client_ip(request):
    """クライアントIPを取得する。Heroku は単一プロキシなので X-Forwarded-For の
    先頭を信頼する（SECURE_PROXY_SSL_HEADER と同じ前提）。"""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def _rate_limited(request, bucket, limit, window=60):
    """固定ウィンドウの簡易レート制限（IP単位）。上限超過なら True を返す。

    ログイン不要の打刻系エンドポイントの濫用（スクリプトによる大量打刻・総当たり）
    を抑える。新たな依存を足さず Django キャッシュで実装（1 dyno/1 worker 運用で
    十分。将来スケール時は共有キャッシュ前提のライブラリに置換）。
    """
    slot = int(time.time() // window)
    key = f"ratelimit:{bucket}:{_client_ip(request)}:{slot}"
    try:
        if cache.add(key, 1, window + 1):
            return False
        return cache.incr(key) > limit
    except ValueError:
        # incr 直前にキーが失効した稀なレース。安全側で作り直す。
        cache.set(key, 1, window + 1)
        return False


def _same_origin(request):
    """CSRF 免除エンドポイント向けの同一オリジン確認。

    Origin/Referer があれば自サイトのホストと一致を要求し、無ければ許可（ネイティブ
    アプリや一部端末で送られないため、後方互換として通す）。トークン認証と併用して
    クロスオリジンからの打刻書き込みを抑止する。
    """
    host = request.get_host()
    for header in ("HTTP_ORIGIN", "HTTP_REFERER"):
        value = request.META.get(header)
        if value:
            return urlparse(value).netloc == host
    return True


def _today_label():
    """打刻画面に出す日本語の日付ラベル（例: 2026年5月20日（火））。"""
    today = timezone.localdate()
    return f"{today.year}年{today.month}月{today.day}日（{_WEEKDAYS_JA[today.weekday()]}）"


@login_required
def punch_page(request):
    """打刻画面。本人の当日の打刻状況と、状態に応じた打刻ボタンを表示する。"""
    staff = services.get_active_staff(request.user)
    if staff is None:
        return render(request, "attendance/not_registered.html", status=403)
    context = {
        "staff": staff,
        "punch": services.get_punch_state(staff),
        "today_label": _today_label(),
        "now_label": timezone.localtime().strftime("%H:%M"),
    }
    return render(request, "attendance/punch.html", context)


@login_required
def punch(request):
    """出勤／退勤の打刻を受け付ける（POSTのみ）。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    staff = services.get_active_staff(request.user)
    if staff is None:
        return render(request, "attendance/not_registered.html", status=403)

    kind = request.POST.get("kind")
    try:
        if kind == TimeRecord.KIND_CLOCK_IN:
            services.clock_in(staff)
            messages.success(request, "出勤を記録しました。")
        elif kind == TimeRecord.KIND_CLOCK_OUT:
            services.clock_out(staff)
            messages.success(request, "退勤を記録しました。お疲れさまでした。")
        else:
            messages.error(request, "打刻の種類が正しくありません。")
    except services.PunchError as exc:
        messages.error(request, str(exc))
    return redirect("attendance:punch_page")


@login_required
def undo_punch(request):
    """本日の直近の打刻を取り消す（POSTのみ）。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    staff = services.get_active_staff(request.user)
    if staff is None:
        return render(request, "attendance/not_registered.html", status=403)
    try:
        services.undo_last_punch(staff)
        messages.success(request, "直近の打刻を取り消しました。")
    except services.PunchError as exc:
        messages.error(request, str(exc))
    return redirect("attendance:punch_page")


# --- QR打刻：トークンURL個人打刻ページ -------------------------------------

def _suggested_punch_kind(state):
    """当日の打刻状況から、次にすべき打刻種別を推測する。両方済みなら None。"""
    if state == services.STATE_NOT_CLOCKED_IN:
        return TimeRecord.KIND_CLOCK_IN
    if state == services.STATE_WORKING:
        return TimeRecord.KIND_CLOCK_OUT
    return None


def punch_by_token(request, token):
    """個人トークンURLでの打刻画面（ログイン不要・トークンが認証情報）。

    QRに埋め込んで配布する。当日の打刻状況を自動判定し、推測ボタンを大きく表示。
    POSTで打刻を確定する。
    """
    staff = get_object_or_404(Staff, punch_token=token, is_active=True)

    if request.method == "POST":
        if _rate_limited(request, "punch_token", limit=30, window=60):
            messages.error(request, "操作が多すぎます。少し待って再度お試しください。")
            return redirect("attendance:punch_by_token", token=staff.punch_token)
        kind = request.POST.get("kind")
        try:
            if kind == TimeRecord.KIND_CLOCK_IN:
                services.clock_in(staff)
                messages.success(
                    request, f"{staff.display_name} さん、出勤を記録しました。"
                )
            elif kind == TimeRecord.KIND_CLOCK_OUT:
                services.clock_out(staff)
                messages.success(
                    request,
                    f"{staff.display_name} さん、退勤を記録しました。お疲れさまでした。",
                )
            else:
                messages.error(request, "打刻の種類が正しくありません。")
        except services.PunchError as exc:
            messages.error(request, str(exc))
        return redirect("attendance:punch_by_token", token=staff.punch_token)

    state = services.get_punch_state(staff)
    # このページのURLそのものを QR に埋め込む。共有端末スキャナーは URL 末尾の
    # UUID を抽出して打刻 API に送るため、QR の中身とページの URL が同じで成立する。
    qr_url = request.build_absolute_uri(
        reverse("attendance:punch_by_token", args=[staff.punch_token])
    )
    qr_svg = _build_qr_svg(qr_url)
    # 過去の確定済み給与明細（直近6件まで）。本人が自分のPDFをダウンロードできる。
    past_payslips = list(
        Payslip.objects.filter(staff=staff)
        .select_related("payroll_period")
        .order_by("-payroll_period__period_end")[:6]
    )
    context = {
        "staff": staff,
        "punch": state,
        "suggested_kind": _suggested_punch_kind(state["state"]),
        "today_label": _today_label(),
        "now_label": timezone.localtime().strftime("%H:%M"),
        "qr_svg": qr_svg,
        "qr_url": qr_url,
        "past_payslips": past_payslips,
    }
    return render(request, "attendance/punch_by_token.html", context)


# --- QR打刻：共有端末（キオスク）スキャナー ---------------------------------

# 共有端末で受け付けるモード（出勤専用／退勤専用）。
_KIOSK_MODES = {TimeRecord.KIND_CLOCK_IN, TimeRecord.KIND_CLOCK_OUT}


def punch_scanner(request):
    """共有端末用のQRスキャナー画面（ログイン不要）。

    モード未指定（=ホーム）では [出勤] [退勤] の2大ボタンを表示する。
    モード指定があるとカメラを起動し、QR読み取り→API呼び出しで打刻する。
    """
    mode = (request.GET.get("mode") or "").strip()
    if mode not in _KIOSK_MODES:
        mode = ""

    mode_label = ""
    if mode == TimeRecord.KIND_CLOCK_IN:
        mode_label = "出勤"
    elif mode == TimeRecord.KIND_CLOCK_OUT:
        mode_label = "退勤"

    context = {
        "mode": mode,
        "mode_label": mode_label,
        "today_label": _today_label(),
        # 初期描画用の時刻。ロード後はクライアントJSが毎秒更新する。
        "now_label": timezone.localtime().strftime("%H:%M:%S"),
        "kiosk_api_url": reverse("attendance:punch_kiosk_api"),
        "scanner_home_url": reverse("attendance:punch_scanner"),
        "clock_in_mode_url": (
            reverse("attendance:punch_scanner") + "?mode=" + TimeRecord.KIND_CLOCK_IN
        ),
        "clock_out_mode_url": (
            reverse("attendance:punch_scanner") + "?mode=" + TimeRecord.KIND_CLOCK_OUT
        ),
    }
    return render(request, "attendance/punch_scanner.html", context)


@csrf_exempt
def punch_kiosk_api(request):
    """キオスクがQR読み取り後に呼ぶJSONエンドポイント（ログイン不要）。

    POST {"token": "<uuid>", "kind": "clock_in"|"clock_out"} を受け、打刻して
    JSONで結果を返す。トークンUUIDが認証情報。CSRF免除（同一オリジン前提・
    認証はトークン値）。
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)

    # CSRF 免除エンドポイントのため、クロスオリジンからの打刻書き込みを弾く。
    if not _same_origin(request):
        return JsonResponse({"ok": False, "error": "forbidden_origin"}, status=403)

    # 共有端末は1台で多人数を打刻するため上限は緩め。濫用スクリプト対策が目的。
    if _rate_limited(request, "kiosk_api", limit=60, window=60):
        return JsonResponse(
            {"ok": False, "error": "rate_limited", "message": "しばらく待って再度お試しください。"},
            status=429,
        )

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    token_raw = str(body.get("token") or "").strip()
    kind = body.get("kind") or ""

    # QRに埋め込まれているのが完全URL（/punch/<token>/）の場合に備え、末尾の
    # UUIDを抽出する。素のUUID文字列もそのまま通る。
    try:
        token = uuid.UUID(token_raw.rstrip("/").split("/")[-1])
    except (TypeError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "invalid_token", "message": "QRが読み取れませんでした。"},
            status=400,
        )

    try:
        staff = Staff.objects.get(punch_token=token, is_active=True)
    except Staff.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "unknown_token", "message": "登録されていないQRです。"},
            status=404,
        )

    try:
        if kind == TimeRecord.KIND_CLOCK_IN:
            record = services.clock_in(staff)
            label = "出勤"
        elif kind == TimeRecord.KIND_CLOCK_OUT:
            record = services.clock_out(staff)
            label = "退勤"
        else:
            return JsonResponse(
                {"ok": False, "error": "invalid_kind", "message": "モード指定が不正です。"},
                status=400,
            )
    except services.PunchError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": "punch_error",
                "message": str(exc),
                "staff_name": staff.display_name,
            },
            status=409,
        )

    return JsonResponse(
        {
            "ok": True,
            "staff_name": staff.display_name,
            "kind": kind,
            "kind_label": label,
            "recorded_at": timezone.localtime(record.recorded_at).strftime("%H:%M"),
            "message": f"{staff.display_name} さん／{label} {timezone.localtime(record.recorded_at).strftime('%H:%M')}",
        }
    )


# --- スタッフ新規追加（admin 脱却）------------------------------------

@staff_member_required
def manage_staff_new(request):
    """新規スタッフを追加する画面。auth.User と Staff と初期時給を1画面で作成。"""
    from .forms import StaffCreateForm
    if request.method == "POST":
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            try:
                staff = services.create_staff_with_user(
                    username=form.cleaned_data["username"],
                    password=form.cleaned_data["password"],
                    display_name=form.cleaned_data["display_name"],
                    business_unit=form.cleaned_data["business_unit"],
                    company=form.cleaned_data["company"],
                    store=form.cleaned_data["store"],
                    hired_on=form.cleaned_data.get("hired_on"),
                    initial_hourly_wage=form.cleaned_data.get("initial_hourly_wage"),
                    birthday=form.cleaned_data.get("birthday"),
                    gender=form.cleaned_data.get("gender", ""),
                    address=form.cleaned_data.get("address", ""),
                    phone=form.cleaned_data.get("phone", ""),
                    job_description=form.cleaned_data.get("job_description", ""),
                )
                messages.success(
                    request,
                    f"✓ {staff.display_name} を新規追加しました。"
                    f"続いて「スタッフ設定」で手当・控除を設定できます。",
                )
                return redirect("attendance:manage_staff_roster")
            except services.PunchError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "入力内容を確認してください。")
    else:
        form = StaffCreateForm()

    sections = [
        ("認証アカウント", ["username", "password"]),
        ("基本情報", ["display_name", "business_unit", "company", "store"]),
        ("勤務情報", ["hired_on", "job_description", "initial_hourly_wage"]),
        ("個人情報（労働者名簿）", ["birthday", "gender", "address", "phone"]),
    ]
    field_sections = [
        {"title": title, "fields": [form[name] for name in names]}
        for title, names in sections
    ]
    return render(
        request,
        "attendance/manage_staff_new.html",
        {"form": form, "field_sections": field_sections},
    )


# --- 労働者名簿（法定帳票・労基法107条）-------------------------------

@staff_member_required
def manage_staff_roster(request):
    """全スタッフの労働者名簿（在籍・退職含む）。フィルタ・名前検索付き。"""
    company = (request.GET.get("company") or "").strip()
    if company not in {c for c, _ in COMPANY_CHOICES}:
        company = ""
    unit = (request.GET.get("unit") or "").strip()
    if unit not in {bu for bu, _ in BUSINESS_UNIT_CHOICES}:
        unit = ""
    status = (request.GET.get("status") or "active").strip()  # active/retired/all

    qs = Staff.objects.select_related("store").order_by(
        "is_active", "business_unit", "display_name"
    )
    if company:
        qs = qs.filter(company=company)
    if unit:
        qs = qs.filter(business_unit=unit)
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "retired":
        qs = qs.filter(is_active=False)

    rows = list(qs)
    context = {
        "rows": rows,
        "company": company,
        "unit": unit,
        "status": status,
        "company_choices": COMPANY_CHOICES,
        "unit_choices": BUSINESS_UNIT_CHOICES,
    }
    return render(request, "attendance/manage_staff_roster.html", context)


@staff_member_required
def manage_staff_roster_csv(request):
    """全スタッフの労働者名簿をCSV出力。社労士・労基監督署提出用（UTF-8 BOM付き）。"""
    from django.http import HttpResponse
    from urllib.parse import quote
    import csv
    import io

    # フィルタは画面と同じパラメータを受け付ける
    company = (request.GET.get("company") or "").strip()
    unit = (request.GET.get("unit") or "").strip()
    status = (request.GET.get("status") or "active").strip()

    qs = Staff.objects.select_related("store").order_by(
        "is_active", "business_unit", "display_name"
    )
    if company in {c for c, _ in COMPANY_CHOICES}:
        qs = qs.filter(company=company)
    if unit in {bu for bu, _ in BUSINESS_UNIT_CHOICES}:
        qs = qs.filter(business_unit=unit)
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "retired":
        qs = qs.filter(is_active=False)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "氏名", "性別", "生年月日", "住所", "連絡先",
        "事業区分", "会社", "所属店舗", "業務の種類",
        "入社年月日", "在籍", "退職年月日", "退職事由",
    ])
    for s in qs:
        writer.writerow([
            s.display_name,
            s.get_gender_display() if s.gender else "",
            s.birthday.isoformat() if s.birthday else "",
            s.address,
            s.phone,
            s.get_business_unit_display(),
            s.get_company_display(),
            s.store.name if s.store else "",
            s.job_description,
            s.hired_on.isoformat() if s.hired_on else "",
            "在籍" if s.is_active else "退職",
            s.retired_on.isoformat() if s.retired_on else "",
            s.retire_reason,
        ])

    body = "﻿" + buf.getvalue()
    resp = HttpResponse(body, content_type="text/csv; charset=utf-8")
    filename = f"労働者名簿_{timezone.localdate():%Y%m%d}.csv"
    resp["Content-Disposition"] = (
        f"attachment; filename=\"staff_roster.csv\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return resp


@staff_member_required
def manage_staff_roster_edit(request, staff_id):
    """1スタッフの労働者名簿を編集（個人情報・入退社・業務の種類）。"""
    from .forms import StaffRosterForm
    staff = get_object_or_404(Staff.objects.select_related("store"), pk=staff_id)
    if request.method == "POST":
        form = StaffRosterForm(request.POST, instance=staff)
        if form.is_valid():
            form.save()
            messages.success(
                request, f"✓ {staff.display_name}の労働者名簿を保存しました。"
            )
            return redirect("attendance:manage_staff_roster")
        messages.error(request, "入力内容を確認してください。")
    else:
        form = StaffRosterForm(instance=staff)
    # フォームを4セクションに分けて並べる
    sections = [
        ("基本情報", ["display_name", "business_unit", "company", "store"]),
        ("個人情報（労働者名簿）", ["birthday", "gender", "address", "phone"]),
        ("勤務情報", ["hired_on", "job_description", "paid_leave_granted_days", "is_active"]),
        ("退職情報（退職した場合のみ）", ["retired_on", "retire_reason"]),
    ]
    field_sections = [
        {"title": title, "fields": [form[name] for name in names]}
        for title, names in sections
    ]
    context = {
        "staff": staff,
        "form": form,
        "field_sections": field_sections,
    }
    return render(request, "attendance/manage_staff_roster_edit.html", context)


# --- 賃金台帳（法定帳票）-----------------------------------------------

@staff_member_required
def manage_wage_book(request, staff_id):
    """1スタッフ・1年分の賃金台帳。労基法108条で5年間の作成保存義務。

    指定年（既定：今年）の月次 Payslip を横に並べ、各項目を縦軸にしたテーブル。
    確定済み Payslip のみ集計対象（未確定の試算は含めない）。
    """
    staff = get_object_or_404(Staff.objects.select_related("store"), pk=staff_id)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        year = today.year

    # 指定年の確定済み Payslip を期間順に取得
    payslips = list(
        Payslip.objects
        .filter(
            staff=staff,
            payroll_period__period_end__year=year,
        )
        .select_related("payroll_period")
        .order_by("payroll_period__period_end")
    )

    # 行＝項目、列＝月（締め日）の構造に変換
    # 各 Payslip を「月ラベル」付きで保持し、行データを集計する
    columns = []
    for ps in payslips:
        period_end = ps.payroll_period.period_end
        columns.append({
            "period_end": period_end,
            "label": f"{period_end.month}月",
            "payslip": ps,
        })

    # 表示する行と Payslip フィールド名のマッピング
    rows_spec = [
        ("勤務日数", "work_days", "日"),
        ("実働分", "work_minutes", "分"),
        ("時間外分", "overtime_minutes", "分"),
        ("時給", "hourly_wage", "円"),
        ("基本賃金", "base_wage_yen", "円"),
        ("時間外割増", "overtime_premium_yen", "円"),
        ("深夜割増", "night_premium_yen", "円"),
        ("休日割増", "holiday_premium_yen", "円"),
        ("通勤手当", "commute_allowance_yen", "円"),
        ("その他手当", "other_allowance_yen", "円"),
        ("行商手当", "peddling_allowance_yen", "円"),
        ("箱洗い手当", "box_wash_allowance_yen", "円"),
        ("有給手当", "paid_leave_yen", "円"),
        ("ドライバー手当", "driver_allowance_yen", "円"),
        ("通勤ガソリン代", "gasoline_yen", "円"),
        ("総支給額", "gross_yen", "円"),
        ("健康保険料", "health_insurance_yen", "円"),
        ("介護保険料", "nursing_insurance_yen", "円"),
        ("厚生年金保険料", "pension_yen", "円"),
        ("雇用保険料", "employment_insurance_yen", "円"),
        ("所得税", "income_tax_yen", "円"),
        ("住民税", "resident_tax_yen", "円"),
        ("その他控除", "other_deduction_yen", "円"),
        ("控除合計", "total_deduction_yen", "円"),
        ("差引支給額", "net_pay_yen", "円"),
    ]
    rows = []
    for label, field, unit in rows_spec:
        values = []
        total = 0
        for col in columns:
            val = getattr(col["payslip"], field, 0) or 0
            values.append(val)
            # 「分」「日」は合計してもよいが、「時給」は意味が無いので除外
            if field != "hourly_wage":
                total += val
        rows.append({
            "label": label,
            "values": values,
            "total": total if field != "hourly_wage" else "",
            "unit": unit,
        })

    # 年度切替用の選択肢（過去5年分）
    year_options = list(range(today.year - 4, today.year + 1))[::-1]

    context = {
        "staff": staff,
        "year": year,
        "year_options": year_options,
        "columns": columns,
        "rows": rows,
        "is_empty": not payslips,
    }
    return render(request, "attendance/manage_wage_book.html", context)


@staff_member_required
def manage_wage_book_csv(request, staff_id):
    """賃金台帳をCSV出力（UTF-8 BOM付き）。社労士・労基監督署への提出用。"""
    from django.http import HttpResponse
    from urllib.parse import quote
    import csv
    import io

    staff = get_object_or_404(Staff, pk=staff_id)
    try:
        year = int(request.GET.get("year") or timezone.localdate().year)
    except (TypeError, ValueError):
        year = timezone.localdate().year

    payslips = list(
        Payslip.objects
        .filter(staff=staff, payroll_period__period_end__year=year)
        .select_related("payroll_period")
        .order_by("payroll_period__period_end")
    )

    # CSV組み立て（メモリバッファ）
    buf = io.StringIO()
    writer = csv.writer(buf)
    # 1行目：氏名・会社・事業区分
    writer.writerow([
        f"賃金台帳", f"{year}年",
        staff.display_name, staff.get_company_display(),
        staff.get_business_unit_display(),
    ])
    # 2行目：月ラベル
    header = [""] + [f"{ps.payroll_period.period_end.month}月" for ps in payslips] + ["年間合計"]
    writer.writerow(header)

    # 各項目
    fields = [
        ("勤務日数", "work_days"),
        ("実働分", "work_minutes"),
        ("時間外分", "overtime_minutes"),
        ("時給", "hourly_wage"),
        ("基本賃金", "base_wage_yen"),
        ("時間外割増", "overtime_premium_yen"),
        ("深夜割増", "night_premium_yen"),
        ("休日割増", "holiday_premium_yen"),
        ("通勤手当", "commute_allowance_yen"),
        ("その他手当", "other_allowance_yen"),
        ("行商手当", "peddling_allowance_yen"),
        ("箱洗い手当", "box_wash_allowance_yen"),
        ("有給手当", "paid_leave_yen"),
        ("ドライバー手当", "driver_allowance_yen"),
        ("通勤ガソリン代", "gasoline_yen"),
        ("総支給額", "gross_yen"),
        ("健康保険料", "health_insurance_yen"),
        ("介護保険料", "nursing_insurance_yen"),
        ("厚生年金保険料", "pension_yen"),
        ("雇用保険料", "employment_insurance_yen"),
        ("所得税", "income_tax_yen"),
        ("住民税", "resident_tax_yen"),
        ("その他控除", "other_deduction_yen"),
        ("控除合計", "total_deduction_yen"),
        ("差引支給額", "net_pay_yen"),
    ]
    for label, field in fields:
        values = [getattr(ps, field, 0) or 0 for ps in payslips]
        total = sum(values) if field != "hourly_wage" else ""
        writer.writerow([label] + values + [total])

    # UTF-8 BOM 付きで返す（Excel で文字化けしないように）
    body = "﻿" + buf.getvalue()
    resp = HttpResponse(body, content_type="text/csv; charset=utf-8")
    filename = f"賃金台帳_{year}_{staff.display_name}.csv"
    resp["Content-Disposition"] = (
        f"attachment; filename=\"wage_book.csv\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return resp


# --- 給与明細PDF出力 ---------------------------------------------------

def _pdf_response(data, filename):
    """PDFバイト列を attachment レスポンスとして返す。"""
    from django.http import HttpResponse
    from urllib.parse import quote
    resp = HttpResponse(data, content_type="application/pdf")
    # 日本語ファイル名は RFC 5987 形式でエンコードして添付
    resp["Content-Disposition"] = (
        f"attachment; filename=\"payslip.pdf\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return resp


@staff_member_required
def manage_payslip_pdf(request, staff_id):
    """1スタッフ分の給与明細PDFをダウンロード（確定済みのみ）。"""
    staff = get_object_or_404(Staff, pk=staff_id)
    setting = PayrollSetting.current()
    period_end = _selected_period_end(request, setting.closing_day)
    payslip = Payslip.objects.select_related("staff", "payroll_period").filter(
        staff=staff, payroll_period__period_end=period_end
    ).first()
    if payslip is None:
        messages.error(request, "確定済みの給与明細がありません。先に給与計算を確定してください。")
        return redirect(
            f"{reverse('attendance:manage_payslip_detail', args=[staff.id])}"
            f"?period={period_end:%Y-%m-%d}"
        )
    # 臨時項目を引いて PDF に渡す
    adjustments = list(
        PayslipAdjustment.objects.filter(
            staff=staff, payroll_period=payslip.payroll_period
        )
    )
    pay_adjs = [a for a in adjustments if a.kind == PayslipAdjustment.KIND_PAYMENT]
    ded_adjs = [a for a in adjustments if a.kind == PayslipAdjustment.KIND_DEDUCTION]
    data = payslip_pdf.render_payslip_pdf(
        payslip,
        payment_adjustments=pay_adjs,
        deduction_adjustments=ded_adjs,
    )
    filename = f"{period_end:%Y-%m}_{staff.display_name}_給与明細.pdf"
    return _pdf_response(data, filename)


@staff_member_required
def manage_payslip_bulk_pdf(request):
    """期間内全スタッフの給与明細を1つのPDFに結合してダウンロード（確定済みのみ）。

    会社（company）・事業区分（unit）で絞り込み可能。給与計算一覧と同じフィルタ。
    """
    setting = PayrollSetting.current()
    period_end = _selected_period_end(request, setting.closing_day)
    company = (request.GET.get("company") or "").strip()
    unit = (request.GET.get("unit") or "").strip()

    qs = Payslip.objects.filter(
        payroll_period__period_end=period_end
    ).select_related("staff", "staff__store", "payroll_period")
    if company in {c for c, _ in COMPANY_CHOICES}:
        qs = qs.filter(staff__company=company)
    if unit in {bu for bu, _ in BUSINESS_UNIT_CHOICES}:
        qs = qs.filter(staff__business_unit=unit)
    payslips = list(qs.order_by("staff__company", "staff__business_unit", "staff__display_name"))
    if not payslips:
        messages.error(request, "確定済みの給与明細がありません。先に給与計算を確定してください。")
        return redirect(
            f"{reverse('attendance:manage_payroll')}?period={period_end:%Y-%m-%d}"
        )
    data = payslip_pdf.render_payslips_bulk_pdf(payslips)
    suffix = ""
    if company:
        suffix = f"_{company}"
    elif unit:
        suffix = f"_{dict(BUSINESS_UNIT_CHOICES).get(unit, '')}"
    filename = f"{period_end:%Y-%m}_給与明細{suffix}_{len(payslips)}名.pdf"
    return _pdf_response(data, filename)


def punch_payslip_pdf(request, token):
    """本人が個人トークンURL経由で自分の給与明細PDFをダウンロード（確定済みのみ）。

    認証はトークンUUID（管理者ログイン不要）。本人は ?period= で月度指定する。
    """
    # 給与明細PDF（個人情報＋金銭）の大量取得を抑える。
    if _rate_limited(request, "payslip_pdf", limit=20, window=60):
        return JsonResponse(
            {"ok": False, "error": "rate_limited"}, status=429
        )
    staff = get_object_or_404(Staff, punch_token=token, is_active=True)
    period_end = _parse_date(request.GET.get("period"))
    if period_end is None:
        return HttpResponseNotAllowed(["GET"])
    payslip = Payslip.objects.select_related("staff", "payroll_period").filter(
        staff=staff, payroll_period__period_end=period_end
    ).first()
    if payslip is None:
        # 本人画面にメッセージ付きで戻す
        messages.error(request, "この月度の給与明細はまだ発行されていません。")
        return redirect("attendance:punch_by_token", token=staff.punch_token)
    adjustments = list(
        PayslipAdjustment.objects.filter(
            staff=staff, payroll_period=payslip.payroll_period
        )
    )
    pay_adjs = [a for a in adjustments if a.kind == PayslipAdjustment.KIND_PAYMENT]
    ded_adjs = [a for a in adjustments if a.kind == PayslipAdjustment.KIND_DEDUCTION]
    data = payslip_pdf.render_payslip_pdf(
        payslip,
        payment_adjustments=pay_adjs,
        deduction_adjustments=ded_adjs,
    )
    filename = f"{period_end:%Y-%m}_{staff.display_name}_給与明細.pdf"
    return _pdf_response(data, filename)


# --- QR打刻：QR PDF一括出力（管理者） -----------------------------------

def _build_qr_svg(payload):
    """文字列 payload を埋め込んだQRコードのSVG文字列を返す。

    印刷時のジャギーを防ぐためベクター（SVG）で生成し、テンプレートに直埋め
    込みする（image タグや base64 を介さない）。
    """
    import qrcode
    import qrcode.image.svg

    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(payload, image_factory=factory, box_size=10, border=2)
    # 出力は ElementTree。文字列化して返す。
    return img.to_string(encoding="unicode")


@staff_member_required
def manage_punch_qrs(request):
    """全スタッフの打刻QRを印刷可能な一覧として表示する。

    ブラウザの印刷機能でPDF出力（または紙印刷）して配布する。
    各QRはトークンURLを埋め込む（例：https://host/attendance/punch/<uuid>/）。
    """
    business_unit = (request.GET.get("business_unit") or "").strip()
    qs = Staff.objects.filter(is_active=True).select_related("store")
    if business_unit in {bu for bu, _ in BUSINESS_UNIT_CHOICES}:
        qs = qs.filter(business_unit=business_unit)
    qs = qs.order_by("business_unit", "display_name")

    # トークンURLは絶対URLで埋め込む。kiosk側はURLからUUIDを抽出する設計で、
    # 個人スマホからQRを開くと /punch/<token>/ ページが直接開く。
    cards = []
    for staff in qs:
        url = request.build_absolute_uri(
            reverse("attendance:punch_by_token", args=[staff.punch_token])
        )
        cards.append(
            {
                "staff": staff,
                "url": url,
                "qr_svg": _build_qr_svg(url),
            }
        )

    context = {
        "cards": cards,
        "business_unit": business_unit,
        "business_units": BUSINESS_UNIT_CHOICES,
        "total": len(cards),
    }
    return render(request, "attendance/manage_punch_qrs.html", context)


@staff_member_required
def manage_rotate_punch_token(request, staff_id):
    """スタッフの打刻トークンを再発行（失効）する。

    QRやトークンURLが漏れた場合の対応導線。新しい UUID に差し替えることで、
    古いURL（旧QR・流出リンク）からの打刻・給与明細アクセスを即無効化する。
    再発行後は新しいQRを配布し直す必要がある。
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    staff = get_object_or_404(Staff, pk=staff_id, is_active=True)
    staff.punch_token = uuid.uuid4()
    staff.save(update_fields=["punch_token"])
    messages.success(
        request,
        f"{staff.display_name} さんの打刻QRを再発行しました。"
        "古いQR・URLは無効になったので、新しいQRを配布してください。",
    )
    return redirect("attendance:manage_punch_qrs")


# --- 管理者：勤怠集計と管理（スプリント2） -----------------------------------

def _parse_date(value):
    """YYYY-MM-DD 文字列を date に。失敗時は None。"""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _parse_time(value):
    """HH:MM 文字列を time に。空なら None。書式不正は ValueError。"""
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()


def _selected_period_end(request, closing_day):
    """クエリ ?period= から締め日を決める。無効なら今日が属する期間。"""
    requested = _parse_date(request.GET.get("period"))
    if requested:
        return requested
    return aggregation.resolve_period_end(timezone.localdate(), closing_day)


def _period_options(closing_day, today):
    """期間セレクタ用に、直近6期間の選択肢を作る。"""
    options = []
    for end in aggregation.recent_period_ends(closing_day, 6, today):
        start = aggregation.period_start_for(end, closing_day)
        options.append(
            {
                "value": end.isoformat(),
                "label": aggregation.period_label(end),
                "range": f"{start.month}/{start.day}〜{end.month}/{end.day}",
            }
        )
    return options


@staff_member_required
def manage_dashboard(request):
    """月次勤怠一覧。期間・事業区分で絞り、全スタッフの月次合計を表示する。"""
    setting = PayrollSetting.current()
    today = timezone.localdate()
    period_end = _selected_period_end(request, setting.closing_day)
    period_start = aggregation.period_start_for(period_end, setting.closing_day)

    unit = request.GET.get("unit", "")
    company = request.GET.get("company", "")
    staff_qs = Staff.objects.filter(is_active=True).select_related("store")
    if unit in dict(BUSINESS_UNIT_CHOICES):
        staff_qs = staff_qs.filter(business_unit=unit)
    if company in dict(COMPANY_CHOICES):
        staff_qs = staff_qs.filter(company=company)
    staff_list = list(staff_qs)

    summaries = aggregation.build_period_summaries(
        staff_list, period_start, period_end, setting
    )
    rows = []
    totals = {"work_days": 0, "work": 0, "night": 0, "overtime": 0, "missing": 0}
    for staff in staff_list:
        s = summaries[staff.id]
        totals["work_days"] += s.work_days
        totals["work"] += s.work_minutes
        totals["night"] += s.night_minutes
        totals["overtime"] += s.overtime_minutes
        totals["missing"] += s.missing_days
        rows.append(
            {
                "staff": staff,
                "work_days": s.work_days,
                "work_display": aggregation.fmt_minutes(s.work_minutes),
                "night_display": aggregation.fmt_minutes(s.night_minutes),
                "overtime_display": aggregation.fmt_minutes(s.overtime_minutes),
                "missing_days": s.missing_days,
            }
        )

    period_obj = PayrollPeriod.objects.filter(period_end=period_end).first()
    context = {
        "period_end": period_end,
        "period_start": period_start,
        "period_label": aggregation.period_label(period_end),
        "period_options": _period_options(setting.closing_day, today),
        "unit": unit,
        "unit_choices": BUSINESS_UNIT_CHOICES,
        "company": company,
        "company_choices": COMPANY_CHOICES,
        "rows": rows,
        "totals": {
            "work_days": totals["work_days"],
            "work_display": aggregation.fmt_minutes(totals["work"]),
            "night_display": aggregation.fmt_minutes(totals["night"]),
            "overtime_display": aggregation.fmt_minutes(totals["overtime"]),
            "missing": totals["missing"],
        },
        "is_closed": bool(period_obj and period_obj.is_closed),
        "closed_at": period_obj.closed_at if period_obj else None,
    }
    return render(request, "attendance/manage_dashboard.html", context)


@staff_member_required
def manage_period(request):
    """月締め・締め解除を実行する（POSTのみ）。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    period_end = _parse_date(request.POST.get("period"))
    action = request.POST.get("action")
    if period_end is None:
        messages.error(request, "対象の期間が正しくありません。")
    elif action == "close":
        services.close_period(period_end, request.user)
        messages.success(request, f"{aggregation.period_label(period_end)}を締めました。")
    elif action == "reopen":
        services.reopen_period(period_end)
        messages.success(
            request, f"{aggregation.period_label(period_end)}の締めを解除しました。"
        )
    else:
        messages.error(request, "操作が正しくありません。")

    url = reverse("attendance:manage_dashboard")
    query = f"?period={request.POST.get('period', '')}"
    if request.POST.get("unit"):
        query += f"&unit={request.POST.get('unit')}"
    return redirect(url + query)


@staff_member_required
def manage_staff_detail(request, staff_id):
    """スタッフ別の日次明細。期間内の全日付を1行ずつ表示し、修正・入力を受ける。"""
    staff = get_object_or_404(Staff.objects.select_related("store"), pk=staff_id)
    setting = PayrollSetting.current()
    period_end = _selected_period_end(request, setting.closing_day)
    period_start = aggregation.period_start_for(period_end, setting.closing_day)
    period_options = _period_options(setting.closing_day, timezone.localdate())

    rows = aggregation.build_day_rows(staff, period_start, period_end, setting)
    summary = aggregation.summarize_rows(rows)
    edits = list(
        TimeRecordEdit.objects.filter(
            time_record__staff=staff,
            time_record__work_date__range=(period_start, period_end),
        )
        .select_related("editor", "time_record")
        .order_by("-created_at")[:50]
    )
    period_obj = PayrollPeriod.objects.filter(period_end=period_end).first()
    context = {
        "staff": staff,
        "period_end": period_end,
        "period_start": period_start,
        "period_label": aggregation.period_label(period_end),
        "period_options": period_options,
        "rows": rows,
        "summary": {
            "work_days": summary.work_days,
            "work_display": aggregation.fmt_minutes(summary.work_minutes),
            "night_display": aggregation.fmt_minutes(summary.night_minutes),
            "overtime_display": aggregation.fmt_minutes(summary.overtime_minutes),
            "missing_days": summary.missing_days,
        },
        "edits": edits,
        "is_locked": bool(period_obj and period_obj.is_closed),
    }
    return render(request, "attendance/manage_staff_detail.html", context)


@staff_member_required
def manage_save_day(request, staff_id):
    """日次明細の1日分の出退勤を保存する（修正・まとめ入力共通・POSTのみ）。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    staff = get_object_or_404(Staff, pk=staff_id)
    work_date = _parse_date(request.POST.get("work_date"))
    period = request.POST.get("period", "")

    if work_date is None:
        messages.error(request, "対象の日付が正しくありません。")
    else:
        try:
            clock_in = _parse_time(request.POST.get("clock_in"))
            clock_out = _parse_time(request.POST.get("clock_out"))
        except ValueError:
            messages.error(request, "時刻は HH:MM の形式で入力してください。")
        else:
            try:
                services.set_day_punches(
                    staff,
                    work_date,
                    clock_in,
                    clock_out,
                    request.user,
                    reason=request.POST.get("reason", "").strip(),
                )
                messages.success(
                    request, f"{work_date.month}月{work_date.day}日の勤怠を保存しました。"
                )
            except services.PunchError as exc:
                messages.error(request, str(exc))

    url = reverse("attendance:manage_staff_detail", args=[staff.id])
    anchor = f"#d{work_date.isoformat()}" if work_date else ""
    return redirect(f"{url}?period={period}{anchor}")


# --- 管理者：給与計算（スプリント3） -----------------------------------------

def _payroll_row(obj, setting):
    """給与計算一覧の表示行を作る。obj は Payslip でも PayslipCalc でもよい
    （フィールド名が共通のため、確定済み／試算の両方を同じ形で扱える）。"""
    wage = obj.hourly_wage
    return {
        "staff": obj.staff,
        "work_days": obj.work_days,
        "hourly_wage": wage,
        "gross_yen": obj.gross_yen,
        "total_deduction_yen": obj.total_deduction_yen,
        "net_pay_yen": obj.net_pay_yen,
        "below_min_wage": 0 < wage < setting.min_wage,
        "no_wage": wage == 0,
    }


@staff_member_required
def manage_payroll(request):
    """給与計算。期間を選び、全スタッフの計算結果を一覧する。

    未確定なら試算（打刻からその場で計算）、確定済みなら凍結された Payslip を表示。
    会社（company）・事業区分（unit）で絞り込み可能。
    """
    setting = PayrollSetting.current()
    period_end = _selected_period_end(request, setting.closing_day)
    period_start = aggregation.period_start_for(period_end, setting.closing_day)
    period_obj = PayrollPeriod.objects.filter(period_end=period_end).first()
    is_closed = bool(period_obj and period_obj.is_closed)

    # フィルタ値（会社・事業区分）。空文字＝全件。
    company = (request.GET.get("company") or "").strip()
    if company not in {c for c, _ in COMPANY_CHOICES}:
        company = ""
    unit = (request.GET.get("unit") or "").strip()
    if unit not in {bu for bu, _ in BUSINESS_UNIT_CHOICES}:
        unit = ""

    payslips_qs = Payslip.objects.filter(
        payroll_period__period_end=period_end
    ).select_related("staff", "staff__store")
    if company:
        payslips_qs = payslips_qs.filter(staff__company=company)
    if unit:
        payslips_qs = payslips_qs.filter(staff__business_unit=unit)
    payslips = list(payslips_qs)
    is_confirmed = bool(payslips)

    if is_confirmed:
        sources = sorted(
            payslips,
            key=lambda p: (p.staff.company, p.staff.business_unit, p.staff.display_name),
        )
    else:
        staff_qs = Staff.objects.filter(is_active=True).select_related("store")
        if company:
            staff_qs = staff_qs.filter(company=company)
        if unit:
            staff_qs = staff_qs.filter(business_unit=unit)
        staff_list = list(staff_qs)
        calcs = payroll.compute_payslips(
            staff_list, period_start, period_end, setting
        )
        sources = [calcs[s.id] for s in staff_list]

    rows = []
    totals = {"work_days": 0, "gross": 0, "deduction": 0, "net": 0}
    for source in sources:
        row = _payroll_row(source, setting)
        rows.append(row)
        totals["work_days"] += row["work_days"]
        totals["gross"] += row["gross_yen"]
        totals["deduction"] += row["total_deduction_yen"]
        totals["net"] += row["net_pay_yen"]

    # 当月のガソリン単価（円/L）を表示用に取得。PayrollPeriod が無ければ 0。
    gasoline_yen_per_liter = period_obj.gasoline_yen_per_liter if period_obj else 0

    context = {
        "period_end": period_end,
        "period_start": period_start,
        "period_label": aggregation.period_label(period_end),
        "period_options": _period_options(setting.closing_day, timezone.localdate()),
        "rows": rows,
        "totals": totals,
        "min_wage": setting.min_wage,
        "is_closed": is_closed,
        "is_confirmed": is_confirmed,
        "confirmed_at": payslips[0].confirmed_at if is_confirmed else None,
        # フィルタの現在値と選択肢
        "company": company,
        "unit": unit,
        "company_choices": COMPANY_CHOICES,
        "unit_choices": BUSINESS_UNIT_CHOICES,
        # 当月のガソリン単価（PayrollPeriod 単位）
        "gasoline_yen_per_liter": gasoline_yen_per_liter,
    }
    return render(request, "attendance/manage_payroll.html", context)


@staff_member_required
def manage_save_gasoline(request):
    """当月のガソリン単価（円/L）を PayrollPeriod に保存する。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    period_end = _parse_date(request.POST.get("period"))
    if period_end is None:
        messages.error(request, "対象の期間が正しくありません。")
        return redirect("attendance:manage_payroll")
    try:
        value = int(request.POST.get("gasoline_yen_per_liter") or 0)
    except (TypeError, ValueError):
        value = 0
    if value < 0:
        value = 0
    period = services.get_or_create_period(period_end)
    period.gasoline_yen_per_liter = value
    period.save(update_fields=["gasoline_yen_per_liter", "updated_at"])
    messages.success(
        request,
        f"✓ {aggregation.period_label(period_end)}のガソリン単価を {value}円/L に更新しました。",
    )
    url = reverse("attendance:manage_payroll")
    return redirect(f"{url}?period={period_end:%Y-%m-%d}")


@staff_member_required
def manage_payroll_confirm(request):
    """給与計算の確定・取消を実行する（POSTのみ）。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    period_end = _parse_date(request.POST.get("period"))
    action = request.POST.get("action")
    if period_end is None:
        messages.error(request, "対象の期間が正しくありません。")
    elif action == "confirm":
        try:
            payslips = services.confirm_payroll(period_end, request.user)
            messages.success(
                request,
                f"{aggregation.period_label(period_end)}の給与計算を確定しました"
                f"（{len(payslips)}名）。",
            )
        except services.PunchError as exc:
            messages.error(request, str(exc))
    elif action == "cancel":
        services.cancel_payroll(period_end)
        messages.success(
            request,
            f"{aggregation.period_label(period_end)}の給与計算の確定を取り消しました。",
        )
    else:
        messages.error(request, "操作が正しくありません。")

    url = reverse("attendance:manage_payroll")
    return redirect(f"{url}?period={request.POST.get('period', '')}")


@staff_member_required
def manage_payslip_detail(request, staff_id):
    """スタッフ別の給与明細（勤怠・支給・控除の3区分）。

    確定済み期間は凍結された Payslip、未確定は試算（PayslipCalc）を表示する。
    """
    staff = get_object_or_404(Staff.objects.select_related("store"), pk=staff_id)
    setting = PayrollSetting.current()
    period_end = _selected_period_end(request, setting.closing_day)
    period_start = aggregation.period_start_for(period_end, setting.closing_day)

    payslip = Payslip.objects.filter(
        payroll_period__period_end=period_end, staff=staff
    ).first()
    if payslip is not None:
        data = payslip
        is_confirmed = True
    else:
        data = payroll.compute_payslip(staff, period_start, period_end, setting)
        is_confirmed = False

    profile = StaffPayrollProfile.objects.filter(staff=staff).first()
    manual = ManualWorkHours.objects.filter(
        staff=staff, payroll_period__period_end=period_end
    ).first()
    adjustments = list(
        PayslipAdjustment.objects.filter(
            staff=staff, payroll_period__period_end=period_end
        )
    )
    payment_adjustments = [
        a for a in adjustments if a.kind == PayslipAdjustment.KIND_PAYMENT
    ]
    deduction_adjustments = [
        a for a in adjustments if a.kind == PayslipAdjustment.KIND_DEDUCTION
    ]
    # 支給欄に「実数字入りの計算式」を出すための補助値。
    # 単価・割増率は時間入力で変わらないので静的に渡し、分数だけ JS が data-field で更新する。
    _normal = getattr(data, "normal_minutes", 0)
    _break = getattr(data, "break_minutes", 0)
    ot_full_rate = Decimal(1) + setting.overtime_rate
    night_full_rate = Decimal(1) + setting.night_rate
    holiday_full_rate = Decimal(1) + setting.holiday_rate
    context = {
        "staff": staff,
        "period_end": period_end,
        "period_start": period_start,
        "period_label": aggregation.period_label(period_end),
        "period_options": _period_options(setting.closing_day, timezone.localdate()),
        "d": data,  # Payslip / PayslipCalc どちらもフィールド名は共通
        "is_confirmed": is_confirmed,
        "manual_used": manual is not None,
        "profile": profile,
        "payment_adjustments": payment_adjustments,
        "deduction_adjustments": deduction_adjustments,
        "adjustment_kinds": PayslipAdjustment.KIND_CHOICES,
        "work_display": aggregation.fmt_minutes(data.work_minutes),
        "overtime_display": aggregation.fmt_minutes(data.overtime_minutes),
        # 支給欄の「基本賃金（拘束分）」＝実働分の基本賃金＋休憩控除。控除行と合わせて表示する。
        "base_wage_gross_yen": data.base_wage_yen + getattr(data, "break_deduction_yen", 0),
        # 計算式表示用（分は data-field で動的更新、単価・倍率は静的）。
        "gross_work_minutes": _normal + _break,        # 拘束分（基本賃金の対象）
        "normal_minutes": _normal,                     # 実働＝拘束−休憩
        "ot_unit_yen": payroll.premium_unit_yen(data.hourly_wage, ot_full_rate),
        "night_unit_yen": payroll.premium_unit_yen(data.hourly_wage, night_full_rate),
        "holiday_unit_yen": payroll.premium_unit_yen(data.hourly_wage, holiday_full_rate),
        "ot_full_rate": ot_full_rate,
        "night_full_rate": night_full_rate,
        "holiday_full_rate": holiday_full_rate,
        "over60h_display": aggregation.fmt_minutes(data.over60h_minutes),
        "night_display": aggregation.fmt_minutes(data.night_minutes),
        "below_min_wage": 0 < data.hourly_wage < setting.min_wage,
        # 勤怠インライン編集の入力初期値（現状値をそのまま入れて、変更があれば保存）。
        # 勤務時間入力は「通常勤務の拘束時間（休憩込み）」。時間外・深夜・休日は別欄で上乗せ（v2方式）。
        "input_work_days": data.work_days or "",
        "input_work_hours": _hours_display(
            getattr(data, "normal_minutes", 0) + getattr(data, "break_minutes", 0)
        ),
        "input_break_hours": _hours_display(getattr(data, "break_minutes", 0)),
        "input_overtime_hours": _hours_display(data.overtime_minutes),
        "input_night_hours": _hours_display(data.night_minutes),
        "input_holiday_hours": _hours_display(data.holiday_minutes),
        # 販売事業向けの手当（食堂事業は使わないのでテンプレートで非表示にする）
        "is_sales": staff.business_unit == "sales",
        "peddling_rate_yen": setting.peddling_allowance_yen,
        "box_wash_rate_yen": setting.box_wash_allowance_yen,
        "scheduled_minutes_per_day": (
            profile.scheduled_minutes_per_day if profile else 480
        ),
        # 内訳（件数・日数）。Payslip 凍結時は ManualWorkHours が無くても表示できるよう
        # 確定済みの場合は manual から、未確定は PayslipCalc から取る。
        "peddling_count": (
            manual.peddling_count if manual is not None else 0
        ),
        "box_wash_count": (
            manual.box_wash_count if manual is not None else 0
        ),
        "paid_leave_days": (
            manual.paid_leave_days if manual is not None else 0
        ),
        "driver_count": (
            manual.driver_count if manual is not None else 0
        ),
        # 計算結果（円）
        "peddling_allowance_yen": getattr(data, "peddling_allowance_yen", 0),
        "box_wash_allowance_yen": getattr(data, "box_wash_allowance_yen", 0),
        "paid_leave_yen": getattr(data, "paid_leave_yen", 0),
        "driver_allowance_yen": getattr(data, "driver_allowance_yen", 0),
        "gasoline_yen": getattr(data, "gasoline_yen", 0),
        # ドライバー手当はプロフィールで is_driver=True のスタッフだけ入力欄を出す
        "is_driver": bool(profile and profile.is_driver),
        "driver_rate_yen": setting.driver_allowance_yen,
        # 通勤手段（ガソリン代計算に使うので明細にも表示するため）
        "commute_method": profile.commute_method if profile else "transit",
        "commute_method_label": (
            profile.get_commute_method_display() if profile else "公共交通機関"
        ),
        "commute_distance_km": profile.commute_distance_km if profile else 0,
        "fuel_efficiency_kml": profile.fuel_efficiency_kml if profile else 15,
        "gasoline_yen_per_liter": (
            PayrollPeriod.objects.filter(period_end=period_end).values_list(
                "gasoline_yen_per_liter", flat=True
            ).first() or 0
        ),
        # 有給年度（10月〜9月）の今期サマリ。期間内累計の取得日数を表示。
        "paid_leave_summary": services.paid_leave_summary(staff, period_end),
    }
    return render(request, "attendance/manage_payslip_detail.html", context)


@staff_member_required
def manage_settings(request):
    """給与設定（締め日・割増率・深夜帯・休憩控除・最低賃金・端数）の編集画面。

    Django admin に頼らず、勤怠アプリ内で設定を編集できるようにする。
    設定は組織で1件（PayrollSetting）。
    """
    setting = PayrollSetting.current()
    if request.method == "POST":
        form = PayrollSettingForm(request.POST, instance=setting)
        if form.is_valid():
            form.save()
            messages.success(request, "給与設定を保存しました。")
            return redirect("attendance:manage_settings")
        messages.error(request, "入力内容を確認してください。")
    else:
        form = PayrollSettingForm(instance=setting)

    # テンプレートでセクション分けして並べるためのフィールド名グループ。
    sections = [
        ("締め・最低賃金", ["closing_day", "min_wage", "wage_rounding_unit"]),
        ("雇用保険", ["employment_insurance_rate"]),
        ("割増率", ["overtime_rate", "over60h_rate", "night_rate", "holiday_rate"]),
        ("深夜帯", ["night_start", "night_end"]),
        (
            "休憩の自動控除",
            [
                "break1_threshold_minutes",
                "break1_deduct_minutes",
                "break2_threshold_minutes",
                "break2_deduct_minutes",
            ],
        ),
        (
            "販売事業部の手当単価",
            ["peddling_allowance_yen", "box_wash_allowance_yen", "driver_allowance_yen"],
        ),
        ("端数処理メモ", ["rounding_rule"]),
    ]
    field_sections = [
        {"title": title, "fields": [form[name] for name in names]}
        for title, names in sections
    ]
    return render(
        request,
        "attendance/manage_settings.html",
        {"form": form, "field_sections": field_sections},
    )


@staff_member_required
def manage_staff_profile(request, staff_id):
    """スタッフごとの手当・控除（給与プロフィール）の編集画面。

    Django admin に頼らず、アプリ内で手当・控除を登録できるようにする。
    """
    staff = get_object_or_404(Staff.objects.select_related("store"), pk=staff_id)
    profile = StaffPayrollProfile.objects.filter(staff=staff).first()
    if request.method == "POST":
        # 未作成なら staff を紐付けた空インスタンスを土台にする。
        form = StaffPayrollProfileForm(
            request.POST, instance=profile or StaffPayrollProfile(staff=staff)
        )
        if form.is_valid():
            form.save()
            messages.success(
                request, f"{staff.display_name}の手当・控除を保存しました。"
            )
            return redirect("attendance:manage_staff_profile", staff_id=staff.id)
        messages.error(request, "入力内容を確認してください。")
    else:
        form = StaffPayrollProfileForm(instance=profile)

    sections = [
        ("手当", ["commute_allowance", "other_allowance", "other_allowance_name"]),
        (
            "控除（登録分）",
            [
                "health_insurance",
                "nursing_insurance",
                "pension_insurance",
                "resident_tax",
                "other_deduction",
                "other_deduction_name",
            ],
        ),
        ("所得税・雇用保険", ["dependents_count", "employment_insurance_enrolled"]),
    ]
    # 販売事業部のスタッフだけ「有給計算用の所定労働時間」「ドライバー設定」を
    # 編集できるようにする（食堂は打刻ベースで月次集計するので不要）。
    if staff.business_unit == "sales":
        sections.append(
            ("販売事業：有給計算", ["scheduled_minutes_per_day"])
        )
        sections.append(
            ("販売事業：ドライバー設定", ["is_driver"])
        )
    # 通勤手段とガソリン代計算用パラメータは全員（車通勤の社員もあり得る）
    sections.append((
        "通勤手段（ガソリン代計算用）",
        ["commute_method", "commute_distance_km", "fuel_efficiency_kml"],
    ))
    field_sections = [
        {"title": title, "fields": [form[name] for name in names]}
        for title, names in sections
    ]
    return render(
        request,
        "attendance/manage_staff_profile.html",
        {"staff": staff, "form": form, "field_sections": field_sections},
    )


@staff_member_required
def manage_min_wage_update(request):
    """最低賃金の一括更新画面。

    ランチネットは8割が最低時給。最低賃金が上がったときに、最低時給で働いて
    いるスタッフだけまとめて更新できる。最低賃金より高いスタッフは触らない。
    """
    setting = PayrollSetting.current()
    today = timezone.localdate()

    # 全スタッフを3つに分けてプレビュー表示する。
    on_min, above_min, no_wage = [], [], []
    for staff in (
        Staff.objects.filter(is_active=True).select_related("store")
    ):
        wage = payroll.resolve_hourly_wage(staff, today)
        if wage == 0:
            no_wage.append(staff)
        elif wage == setting.min_wage:
            on_min.append(staff)
        else:
            above_min.append((staff, wage))

    if request.method == "POST":
        form = BulkMinWageUpdateForm(request.POST)
        if form.is_valid():
            new_amount = form.cleaned_data["new_min_wage"]
            effective_from = form.cleaned_data["effective_from"]
            affected, _ = services.bulk_update_min_wage(
                new_amount, effective_from, request.user
            )
            messages.success(
                request,
                f"最低賃金を{new_amount:,}円に更新し、対象スタッフ{len(affected)}名"
                f"の時給を{effective_from}から{new_amount:,}円に更新しました。",
            )
            return redirect("attendance:manage_min_wage_update")
        messages.error(request, "入力内容を確認してください。")
    else:
        form = BulkMinWageUpdateForm(
            initial={"new_min_wage": setting.min_wage, "effective_from": today}
        )

    return render(
        request,
        "attendance/manage_min_wage_update.html",
        {
            "setting": setting,
            "form": form,
            "on_min": on_min,
            "above_min": above_min,
            "no_wage": no_wage,
        },
    )


def _hours_display(minutes):
    """分（int）を時間の文字列にする。0 は空文字。

    入力欄の初期値に使うため、分→時間→分の往復で値がズレないよう小数2桁まで
    保持する（例：1381分＝23.0167h → "23.02" → 保存時 _hours_to_minutes で
    1381分に戻る）。1桁丸めだと 1381→"23.0"→1380分 と1分欠ける問題への対処。
    末尾の余分な0は落として読みやすくする（23.10→"23.1"、23.00→"23"）。
    """
    if not minutes:
        return ""
    hours = (Decimal(minutes) / 60).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(hours.normalize(), "f")


def _parse_hours_post(post):
    """勤怠インライン編集のPOSTから
    (work_days, work_min, ot_min, night_min, holiday_min, break_min) を取り出す。

    work_hours は拘束時間（休憩込み・時間）、break_hours は月の休憩合計（時間）で受ける。
    """
    return (
        services._int_or_zero(post.get("work_days")),
        services._hours_to_minutes(post.get("work_hours")),
        services._hours_to_minutes(post.get("overtime_hours")),
        services._hours_to_minutes(post.get("night_hours")),
        services._hours_to_minutes(post.get("holiday_hours")),
        services._hours_to_minutes(post.get("break_hours")),
    )


def _parse_sales_allowances_post(post):
    """販売事業向けの手当カウントを取り出す: (行商回数, 箱洗い数, 有給日数, ドライバー回数)。"""
    return (
        services._int_or_zero(post.get("peddling_count")),
        services._int_or_zero(post.get("box_wash_count")),
        services._int_or_zero(post.get("paid_leave_days")),
        services._int_or_zero(post.get("driver_count")),
    )


@staff_member_required
def manage_payslip_preview(request, staff_id):
    """勤怠の入力値で給与計算をプレビューし、結果を JSON で返す（保存しない）。

    給与明細画面の勤怠インライン編集から呼ばれ、入力中の手取り計算を動的に更新する。
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    staff = get_object_or_404(Staff, pk=staff_id)
    setting = PayrollSetting.current()
    period_end = _parse_date(request.POST.get("period"))
    if period_end is None:
        return JsonResponse({"error": "invalid period"}, status=400)
    period_start = aggregation.period_start_for(period_end, setting.closing_day)

    work_days, work, ot, night, holiday, break_min = _parse_hours_post(request.POST)
    peddling, box_wash, paid_leave, driver = _parse_sales_allowances_post(request.POST)
    # 一時的な ManualWorkHours（保存しない）でエンジンに食わせる。
    transient = ManualWorkHours(
        staff=staff,
        work_days=work_days,
        work_minutes=work,
        break_minutes=break_min,
        overtime_minutes=ot,
        night_minutes=night,
        holiday_minutes=holiday,
        peddling_count=peddling,
        box_wash_count=box_wash,
        paid_leave_days=paid_leave,
        driver_count=driver,
    )
    calc = payroll.compute_payslip(
        staff, period_start, period_end, setting, manual=transient
    )
    return JsonResponse(
        {
            "work_days": calc.work_days,
            "work_minutes": calc.work_minutes,
            "break_minutes": calc.break_minutes,
            "overtime_minutes": calc.overtime_minutes,
            "night_minutes": calc.night_minutes,
            "holiday_minutes": calc.holiday_minutes,
            # 計算式表示用：拘束分（基本賃金の対象）と実働分。
            "gross_work_minutes": calc.normal_minutes + calc.break_minutes,
            "normal_minutes": calc.normal_minutes,
            "base_wage_yen": calc.base_wage_yen,
            # 支給欄の表示用：基本賃金（拘束分）＝実働分＋休憩控除、と休憩控除そのもの。
            "base_wage_gross_yen": calc.base_wage_yen + calc.break_deduction_yen,
            "break_deduction_yen": calc.break_deduction_yen,
            "overtime_premium_yen": calc.overtime_premium_yen,
            "night_premium_yen": calc.night_premium_yen,
            "holiday_premium_yen": calc.holiday_premium_yen,
            "commute_allowance_yen": calc.commute_allowance_yen,
            "other_allowance_yen": calc.other_allowance_yen,
            "peddling_allowance_yen": calc.peddling_allowance_yen,
            "box_wash_allowance_yen": calc.box_wash_allowance_yen,
            "paid_leave_yen": calc.paid_leave_yen,
            "driver_allowance_yen": calc.driver_allowance_yen,
            "gasoline_yen": calc.gasoline_yen,
            "peddling_count": calc.peddling_count,
            "box_wash_count": calc.box_wash_count,
            "paid_leave_days": calc.paid_leave_days,
            "driver_count": calc.driver_count,
            "gross_yen": calc.gross_yen,
            "health_insurance_yen": calc.health_insurance_yen,
            "nursing_insurance_yen": calc.nursing_insurance_yen,
            "pension_yen": calc.pension_yen,
            "employment_insurance_yen": calc.employment_insurance_yen,
            "income_tax_yen": calc.income_tax_yen,
            "resident_tax_yen": calc.resident_tax_yen,
            "other_deduction_yen": calc.other_deduction_yen,
            "total_deduction_yen": calc.total_deduction_yen,
            "net_pay_yen": calc.net_pay_yen,
        }
    )


@staff_member_required
def manage_payslip_save_hours(request, staff_id):
    """勤怠の入力値を ManualWorkHours として保存する。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    staff = get_object_or_404(Staff, pk=staff_id)
    period_end = _parse_date(request.POST.get("period"))
    if period_end is None:
        messages.error(request, "対象の期間が正しくありません。")
        return redirect("attendance:manage_payslip_detail", staff_id=staff.id)
    period = services.get_or_create_period(period_end)
    work_days, work, ot, night, holiday, break_min = _parse_hours_post(request.POST)
    peddling, box_wash, paid_leave, driver = _parse_sales_allowances_post(request.POST)
    services.save_manual_hours(
        staff, period, work_days, work, ot, night, holiday,
        peddling_count=peddling, box_wash_count=box_wash,
        paid_leave_days=paid_leave, driver_count=driver,
        break_minutes=break_min,
    )
    messages.success(
        request,
        f"✓ {staff.display_name}の勤怠を保存しました（{aggregation.period_label(period_end)}）。",
    )
    # 保存後は給与計算一覧に戻る。次のスタッフへすぐ移れて、流れ作業がスムーズになる。
    url = reverse("attendance:manage_payroll")
    return redirect(f"{url}?period={period_end:%Y-%m-%d}")


@staff_member_required
def manage_payslip_clear_hours(request, staff_id):
    """このスタッフの手動勤務時間を取り消し、打刻データから計算する状態に戻す。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    staff = get_object_or_404(Staff, pk=staff_id)
    period_end = _parse_date(request.POST.get("period"))
    if period_end is None:
        messages.error(request, "対象の期間が正しくありません。")
        return redirect("attendance:manage_payslip_detail", staff_id=staff.id)
    period = services.get_or_create_period(period_end)
    services.clear_manual_hours(staff, period)
    messages.success(
        request,
        f"✓ {staff.display_name}の手入力を解除しました（打刻データで再計算）。",
    )
    # 「打刻データに戻す」も保存系なので、同じく一覧へ戻す。
    url = reverse("attendance:manage_payroll")
    return redirect(f"{url}?period={period_end:%Y-%m-%d}")


@staff_member_required
def manage_payslip_save_adjustments(request, staff_id):
    """臨時項目（年末調整還付・慶弔金・遡及精算など）の行を一括保存する。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    staff = get_object_or_404(Staff, pk=staff_id)
    period_end = _parse_date(request.POST.get("period"))
    if period_end is None:
        messages.error(request, "対象の期間が正しくありません。")
        return redirect("attendance:manage_payslip_detail", staff_id=staff.id)
    period = services.get_or_create_period(period_end)
    try:
        created, updated, deleted = services.save_payslip_adjustments(
            staff, period, request.POST
        )
    except services.PunchError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"臨時項目を保存しました（追加 {created} 件・更新 {updated} 件・削除 {deleted} 件）。",
        )
    url = reverse("attendance:manage_payslip_detail", args=[staff.id])
    return redirect(f"{url}?period={period_end:%Y-%m-%d}")


@staff_member_required
def manage_staff_settings(request):
    """全スタッフの時給・手当・控除を1画面で一括設定する。

    50名以上の従業員・PC操作が苦手な運用者を想定し、スタッフごとの画面遷移を
    やめてスプレッドシート型のテーブルで完結させる。事業区分フィルタ＋名前検索
    （JS）で50人から目的の人を即見つけられる。
    """
    unit = (
        request.POST.get("unit", "") if request.method == "POST"
        else request.GET.get("unit", "")
    )
    company = (
        request.POST.get("company", "") if request.method == "POST"
        else request.GET.get("company", "")
    )
    staff_qs = (
        Staff.objects.filter(is_active=True).select_related("store")
    )
    if unit in dict(BUSINESS_UNIT_CHOICES):
        staff_qs = staff_qs.filter(business_unit=unit)
    if company in dict(COMPANY_CHOICES):
        staff_qs = staff_qs.filter(company=company)
    staff_list = list(staff_qs)

    if request.method == "POST":
        wage_changed, profile_changed = services.save_staff_settings_bulk(
            staff_list, request.POST
        )
        messages.success(
            request,
            f"スタッフ設定を保存しました（時給更新 {wage_changed} 件・手当控除更新 {profile_changed} 件）。",
        )
        url = reverse("attendance:manage_staff_settings")
        query = f"?unit={unit}"
        if company:
            query += f"&company={company}"
        return redirect(f"{url}{query}")

    # 現状の値を1クエリずつで取得（N+1回避）。
    staff_ids = [s.id for s in staff_list]
    today = timezone.localdate()
    profiles = {
        p.staff_id: p
        for p in StaffPayrollProfile.objects.filter(staff_id__in=staff_ids)
    }
    wages_by_staff = {}
    for wage in HourlyWage.objects.filter(
        staff_id__in=staff_ids, effective_from__lte=today
    ):
        cur = wages_by_staff.get(wage.staff_id)
        if cur is None or wage.effective_from > cur.effective_from:
            wages_by_staff[wage.staff_id] = wage

    setting = PayrollSetting.current()
    rows = []
    for staff in staff_list:
        p = profiles.get(staff.id)
        w = wages_by_staff.get(staff.id)
        rows.append(
            {
                "staff": staff,
                "company": staff.company,
                "hourly_wage": w.amount if w else "",
                "below_min_wage": bool(
                    w and 0 < w.amount < setting.min_wage
                ),
                "commute_allowance": p.commute_allowance if p else "",
                "health_insurance": p.health_insurance if p else "",
                "pension_insurance": p.pension_insurance if p else "",
                "resident_tax": p.resident_tax if p else "",
                "dependents_count": p.dependents_count if p else 0,
                "employment_insurance_enrolled": (
                    p.employment_insurance_enrolled if p else True
                ),
            }
        )

    return render(
        request,
        "attendance/manage_staff_settings.html",
        {
            "rows": rows,
            "unit": unit,
            "unit_choices": BUSINESS_UNIT_CHOICES,
            "company": company,
            "company_choices": COMPANY_CHOICES,
            "min_wage": setting.min_wage,
            "effective_from": today,
        },
    )
