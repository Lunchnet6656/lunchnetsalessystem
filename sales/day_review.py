"""その日の振り返り（日計表一覧で入力）。画面の設計＝engineering/harness/specs/lunchnetsale-日計表-その日の振り返り-画面設計.md"""
from django.db import transaction
from django.utils import timezone

from sales.models import DayReview, DayReviewEvent, SalesLocation

MAX_EVENTS = 10
MAX_COMMENT = 1000

# 種類バッジの色。販売が止まった＝赤、遅れ＝黄、事故ではない調整＝灰
KIND_TONE = {"stop": "danger", "absence": "danger", "production": "warning", "delivery": "warning",
             "reallocation": "secondary", "other": "secondary"}
KIND_LABELS = dict(DayReviewEvent.KIND_CHOICES)


def reviews_by_date(dates):
    """{日付: DayReview}。出来事と店は1回で取る。"""
    qs = DayReview.objects.filter(date__in=list(dates)).prefetch_related("events__locations").select_related("updated_by")
    return {r.date: r for r in qs}


def _updated_label(r):
    u = r.updated_by
    name = f"{u.last_name} {u.first_name}".strip() if u else ""
    return f"{timezone.localtime(r.updated_at):%Y/%m/%d %H:%M}" + (f" {name or u.username}" if u else "")


def display_rows(review):
    """一覧の振り返り行に出す出来事（店は no 順の店名）。"""
    rows = []
    for e in review.events.all():
        names = [] if e.all_locations else [l.name for l in sorted(e.locations.all(), key=lambda l: l.no)]
        rows.append({"label": KIND_LABELS.get(e.kind, e.kind), "tone": KIND_TONE.get(e.kind, "secondary"),
                     "all": e.all_locations, "names": names, "exclude": e.exclude, "note": e.note, "tip": False})
    # ⓘ は行の最初の「予測除外」にだけ付ける
    first = next((r for r in rows if r["exclude"]), None)
    if first:
        first["tip"] = True
    return rows


def modal_data(reviews):
    """モーダルに差し込む JSON（json_script 用）。店は SalesLocation の id。"""
    return {
        d.isoformat(): {
            "comment": r.comment,
            "events": [{"kind": e.kind, "all": e.all_locations, "locations": [l.id for l in e.locations.all()],
                        "exclude": e.exclude, "note": e.note} for e in r.events.all()],
            "updated": _updated_label(r),
        }
        for d, r in reviews.items()
    }


def location_marks(reviews):
    """絞り込み表示の印。{(日付, 店名): 'exclude' | 'event'}。全店の出来事は店名 None。"""
    marks = {}
    for d, r in reviews.items():
        for e in r.events.all():
            keys = [(d, None)] if e.all_locations else [(d, l.name.strip()) for l in e.locations.all()]
            for k in keys:
                if e.exclude:
                    marks[k] = "exclude"
                else:
                    marks.setdefault(k, "event")
    return marks


def mark_for(marks, d, name):
    name = (name or "").strip()
    found = {marks.get((d, None)), marks.get((d, name))}
    return "exclude" if "exclude" in found else "event" if "event" in found else None


def parse_events(post):
    """POST から出来事の並びを読む。戻り値＝(出来事の並び, エラーの並び)。"""
    try:
        total = int(post.get("ev-TOTAL", "0"))
    except ValueError:
        total = 0
    valid_ids = set(SalesLocation.objects.values_list("id", flat=True))
    events, errors = [], []
    for i in range(min(total, MAX_EVENTS + 1)):
        kind = post.get(f"ev-{i}-kind")
        if kind is None:          # 削除予定のカードは送られない
            continue
        all_locations = post.get(f"ev-{i}-scope", "all") == "all"
        loc_ids = [] if all_locations else [int(x) for x in post.getlist(f"ev-{i}-locations") if x.isdigit() and int(x) in valid_ids]
        if kind not in KIND_LABELS:
            errors.append("種類を選択")
        if not all_locations and not loc_ids:
            errors.append("店を1つ以上選択")
        events.append({"kind": kind, "all": all_locations, "locations": loc_ids,
                       "exclude": post.get(f"ev-{i}-exclude") == "1", "note": post.get(f"ev-{i}-note", "").strip()[:100]})
    if len(events) > MAX_EVENTS:
        errors.append(f"出来事は{MAX_EVENTS}件まで")
    return events, errors


def save_review(day, post, user):
    """保存（出来事は送られたもので置き換え）。戻り値＝エラーの並び（空なら保存済み）。"""
    comment = post.get("comment", "").strip()
    events, errors = parse_events(post)
    if len(comment) > MAX_COMMENT:
        errors.append(f"コメントは{MAX_COMMENT}字まで")
    if not comment and not events:
        errors.append("コメントか出来事を入力")
    if errors:
        return list(dict.fromkeys(errors))
    with transaction.atomic():
        review, _ = DayReview.objects.update_or_create(date=day, defaults={"comment": comment, "updated_by": user})
        review.events.all().delete()
        for e in events:
            ev = DayReviewEvent.objects.create(review=review, kind=e["kind"], all_locations=e["all"],
                                               exclude=e["exclude"], note=e["note"])
            if e["locations"]:
                ev.locations.set(e["locations"])
    return []
