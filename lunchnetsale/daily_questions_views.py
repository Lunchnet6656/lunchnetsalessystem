"""日計表の質問の設定（/daily-report-questions/）。食数予測を見られる人だけ。"""
from datetime import datetime, timedelta

from django.contrib import messages
from django.shortcuts import redirect, render

from sales import daily_questions as dq
from sales.models import DailyReport, DailyReportQuestionSetting, SalesLocation
from sales.permissions import menu_permission_required

ONE_IN_OPTIONS = (5, 10, 20)
MAX_CHOICES = 10
LOOKBACK_DAYS = 60


def _estimates(early_before):
    """直近2か月の日計表で数えた「1店あたり月に約○回」。店が無ければ None。"""
    since = datetime.now().date() - timedelta(days=LOOKBACK_DAYS)
    stores = [s for s in SalesLocation.objects.all() if dq.sales_group(s.type)]
    rows_by_store = {}
    for name, sold, rem, t in (DailyReport.objects.filter(date__gte=since, total_quantity__gt=0)
                               .values_list("location", "total_sales_quantity", "total_remaining", "sold_out_time")):
        rows_by_store.setdefault(name, []).append((int(sold or 0), int(rem or 0), t))
    active = [s for s in stores if rows_by_store.get(s.name)]
    if not active:
        return {n: None for n in ONE_IN_OPTIONS}, None, None
    months = LOOKBACK_DAYS / 30
    low = {}
    for n in ONE_IN_OPTIONS:
        hits = 0
        for s in active:
            info = dq.usual_for(s.name, datetime.now().date(), n)
            if not info:
                continue
            hits += sum(1 for sold, rem, _ in rows_by_store[s.name]
                        if rem > 0 and sold <= info["usual"] * info["threshold"])
        low[n] = round(hits / len(active) / months, 1)
    times = sorted(t for s in active for _, _, t in rows_by_store[s.name]
                   if t and t.strftime("%H:%M") != "00:00")
    early = round(sum(1 for t in times if t < early_before) / len(active) / months, 1)
    span = None
    if len(times) >= 8:
        span = (times[len(times) // 4].strftime("%H:%M"), times[len(times) * 3 // 4].strftime("%H:%M"))
    return low, early, span


def _lists_from_post(post):
    lists, errors = {}, []
    for key, label in dq.CHOICE_KEYS:
        items = [v.strip() for v in post.getlist(f"choice_{key}")]
        rows = []
        for i, v in enumerate(items):
            err = ""
            if not v:
                err = "空の選択肢があります。消すか、言葉を入れてください"
            elif len(v) > 40:
                err = "40文字までです"
            elif v == dq.NO_QUESTION:
                err = "「特になし（分からない）」はいつも最後に出るので、ここには入れません"
            elif v in items[:i]:
                err = "同じ言葉がすでにあります"
            rows.append({"text": v, "error": err, "id": f"choice-{key}-{i}"})
            if err:
                errors.append({"label": f"{label}の{i + 1}番目", "msg": err, "id": f"choice-{key}-{i}"})
        if len(items) > MAX_CHOICES:
            errors.append({"label": label, "msg": f"選択肢は{MAX_CHOICES}個までです", "id": f"list-{key}"})
        lists[key] = rows
    return lists, errors


@menu_permission_required("can_view_performance_by_location")
def daily_report_questions_view(request):
    setting = DailyReportQuestionSetting.load()
    errors = []
    if request.method == "POST":
        lists, errors = _lists_from_post(request.POST)
        one_in = request.POST.get("low_one_in", "")
        one_in = int(one_in) if one_in.isdigit() and int(one_in) in ONE_IN_OPTIONS else setting.low_one_in
        early_raw = request.POST.get("early_before", "")
        try:
            early = datetime.strptime(early_raw[:5], "%H:%M").time()
        except ValueError:
            early = None
            errors.append({"label": "早く売り切れた日の時刻", "msg": "時刻を入れてください", "id": "early_before"})
        if not errors:
            setting.low_one_in = one_in
            setting.early_before = early
            setting.choices = {key: [r["text"] for r in rows] for key, rows in lists.items()}
            setting.save()
            messages.success(request, "保存しました。次に開く日計表から変わります。")
            return redirect("daily_report_questions")
        form_one_in, form_early = one_in, early_raw
    else:
        lists = {key: [{"text": t, "error": "", "id": f"choice-{key}-{i}"}
                       for i, t in enumerate(dq.choices_for(setting, key.split("_")[0], key.split("_")[1])[:-1])]
                 for key, _ in dq.CHOICE_KEYS}
        form_one_in, form_early = setting.low_one_in, setting.early_before.strftime("%H:%M")

    low_est, early_est, span = _estimates(setting.early_before)
    sections = []
    for kind, title in (("low", "いつもより売れなかった日の選択肢"),
                        ("early", f"{setting.early_before.strftime('%H:%M')}より前に売り切れた日の選択肢")):
        sections.append({
            "title": title,
            "groups": [
                {"key": f"indoor_{kind}", "label": "室内（オフィス・ビル内）", "rows": lists[f"indoor_{kind}"]},
                {"key": f"outdoor_{kind}", "label": "外売り（車・行商・テーブル）", "rows": lists[f"outdoor_{kind}"]},
            ],
        })
    return render(request, "daily_report_questions.html", {
        "one_in_options": [{"n": n, "est": low_est.get(n)} for n in ONE_IN_OPTIONS],
        "form_one_in": form_one_in,
        "form_early": form_early,
        "early_est": early_est,
        "early_span": span,
        "sections": sections,
        "errors": errors,
        "max_choices": MAX_CHOICES,
        "no_question": dq.NO_QUESTION,
    })
