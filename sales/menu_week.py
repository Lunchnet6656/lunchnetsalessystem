"""週のメニュー確認画面の裏側（表示の中身・価格の段・確認の保存）。

毎週必ず人が「種類・容器・価格」を確かめてから確認済みにする（2026-10-07 しょうへい指示）。
仕様: 要件定義 §8.6／画面設計 §4
"""
import datetime
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from sales.menu_registry import LARGE_RICE_NO, prices_for_rank, week_end, week_state
from sales.models import (
    CONTAINER_CHOICES, PRICE_PATTERNS, DailyReport, MenuProfile, MenuWeekCheck, PriceRank, PriceTable, Product,
)
from sales.price_admin import cells_of, md


def week_products(week):
    """その週のメニュー（No順）。Product.week は "2026-10-14" と "20261014" が混在している。"""
    return list(Product.objects.filter(week__in=[week.isoformat(), week.strftime("%Y%m%d")])
                .select_related("rank").order_by("no"))


def price_segments(week):
    """その週（水〜火）に使う価格表の段。週の途中で切り替わるときだけ2段以上になる。"""
    start, end = week, week_end(week)
    first = PriceTable.objects.filter(valid_from__lte=start).order_by("-valid_from").prefetch_related("cells").first()
    later = list(PriceTable.objects.filter(valid_from__gt=start, valid_from__lte=end)
                 .order_by("valid_from").prefetch_related("cells"))
    tables = ([first] if first else []) + later
    segments = []
    for i, table in enumerate(tables):
        seg_start = start if i == 0 else table.valid_from
        seg_end = tables[i + 1].valid_from - datetime.timedelta(days=1) if i + 1 < len(tables) else end
        segments.append({"start": seg_start, "end": seg_end, "cells": cells_of(table)})
    for seg in segments:
        if len(segments) == 1:
            seg["label"] = ""
        elif seg["start"] == start:
            seg["label"] = f"〜{seg['end'].month}/{seg['end'].day}"
        else:
            seg["label"] = f"{seg['start'].month}/{seg['start'].day}〜"
    return segments


def prices_json(segments, ranks):
    """種類を変えたら価格がその場で変わるように、画面へ埋め込む値段の一覧。"""
    return [{"label": seg["label"],
             "cells": {str(r.id): {p: seg["cells"].get((r.id, p)) for p in PRICE_PATTERNS} for r in ranks}}
            for seg in segments]


@dataclass
class WeekRow:
    product: Product
    needs_check: bool
    fixed: bool            # 大盛りごはん（アプリが足す行）・古い登録方法の行はプルダウンにしない
    prices: list = field(default_factory=list)   # [{"label", "A", "B", "C"}]

    @property
    def price_cells(self):
        """テンプレート用：価格A/B/Cごとに [(段の見出し, 値段), ...]。"""
        return [{"pattern": pt, "lines": [(pr["label"], pr[pt]) for pr in self.prices]} for pt in PRICE_PATTERNS]


def week_view(week, today=None):
    today = today or timezone.localdate()
    products = week_products(week)
    segments = price_segments(week)
    unconfirmed = set(MenuProfile.objects.filter(name__in=[p.name for p in products], confirmed=False)
                      .values_list("name", flat=True))
    rows = []
    for p in products:
        if p.rank_id:
            prices = [{"label": s["label"], **{pt: s["cells"].get((p.rank_id, pt)) for pt in PRICE_PATTERNS}}
                      for s in segments] or [{"label": "", **{pt: None for pt in PRICE_PATTERNS}}]
        else:
            prices = [{"label": "", **{pt: int(getattr(p, f"price_{pt}") or 0) for pt in PRICE_PATTERNS}}]
        rows.append(WeekRow(p, p.name in unconfirmed and p.no != LARGE_RICE_NO,
                            fixed=p.no == LARGE_RICE_NO or not p.rank_id, prices=prices))
    rows.sort(key=lambda r: (not r.needs_check, r.product.no))   # 要確認の行を上に寄せる
    legacy = bool(products) and not any(p.rank_id for p in products)
    return {
        "week": week,
        "label": f"{week.month}/{week.day}週",
        "state": week_state(week, today),
        "check": MenuWeekCheck.objects.filter(week=week).select_related("confirmed_by").first(),
        "rows": rows,
        "needs_check_count": sum(r.needs_check for r in rows),
        "segments": segments,
        "multi_segment": len(segments) > 1,
        "missing_price": any(v is None for r in rows if not r.fixed for pr in r.prices
                             for k, v in pr.items() if k in PRICE_PATTERNS),
        "legacy": legacy,
        "prev_week": week - datetime.timedelta(days=7),
        "next_week": week + datetime.timedelta(days=7),
    }


@dataclass
class Change:
    product: Product
    rank: PriceRank
    container: str
    rank_changed: bool
    container_changed: bool


def read_choices(post, products, ranks):
    """POSTの種類・容器を読む。戻り値：(changes, errors)。大盛りごはん・古い登録方法の行は読まない。"""
    by_id = {str(r.id): r for r in ranks}
    changes, errors = [], []
    for p in products:
        if p.no == LARGE_RICE_NO or not p.rank_id:
            continue
        rank = by_id.get(post.get(f"rank_{p.no}", str(p.rank_id)))
        container = post.get(f"container_{p.no}", p.container_type)
        if rank is None or container not in CONTAINER_CHOICES:
            errors.append(f"No.{p.no} {p.name} の値段の種類と容器を選択してください。")
            continue
        changes.append(Change(p, rank, container, rank.id != p.rank_id, container != p.container_type))
    return changes, errors


def selling_week_summary(week, changes, today):
    """販売中の週を変えるときの確認画面の中身。"""
    lines = []
    for c in changes:
        if not (c.rank_changed or c.container_changed):
            continue
        parts = []
        if c.rank_changed:
            parts.append(f"{c.product.rank.name} → {c.rank.name}")
        if c.container_changed:
            parts.append(f"{c.product.container_type} → {c.container}")
        lines.append(f"No.{c.product.no} {c.product.name}　{'／'.join(parts)}")
    sent = DailyReport.objects.filter(date__gte=week, date__lte=today).count()
    return {"lines": lines, "today": md(today), "sent": sent,
            "sent_range": f"{md(week)}〜{md(today)}" if today >= week else ""}


@transaction.atomic
def confirm_week(week, changes, user, now=None):
    """週のメニューに反映し、辞書にも覚えさせ、週を確認済みにする。"""
    now = now or timezone.now()
    by = user if getattr(user, "is_authenticated", False) else None
    for c in changes:
        p = c.product
        if c.rank_changed or c.container_changed:
            p.rank, p.container_type = c.rank, c.container
            for field_name, value in prices_for_rank(c.rank, week).items():
                setattr(p, field_name, value)
            p.save()
        MenuProfile.objects.update_or_create(
            name=p.name, defaults={"rank": c.rank, "container": c.container, "confirmed": True, "updated_by": by},
        )
    check, _ = MenuWeekCheck.objects.get_or_create(week=week)
    check.confirmed_at, check.confirmed_by, check.resent_after_confirm = now, by, False
    check.save()
    return check


def recent_weeks(today, count=8):
    """週の一覧：今の週・次の週を含む直近の週（新しい順）。"""
    this_week = today - datetime.timedelta(days=(today.weekday() - 2) % 7)
    weeks = [this_week + datetime.timedelta(days=7 * i) for i in range(1, -count + 1, -1)]
    checks = {c.week: c for c in MenuWeekCheck.objects.filter(week__in=weeks).select_related("confirmed_by")}
    unconfirmed_names = set(MenuProfile.objects.filter(confirmed=False).values_list("name", flat=True))
    rows = []
    for w in weeks:
        products = week_products(w)
        rows.append({
            "week": w, "label": f"{w.month}/{w.day}週", "state": week_state(w, today), "check": checks.get(w),
            "has_menu": bool(products), "legacy": bool(products) and not any(p.rank_id for p in products),
            "needs_check": sum(1 for p in products if p.name in unconfirmed_names and p.no != LARGE_RICE_NO),
        })
    return rows


def week_alerts(today):
    """TOPと価格表画面に出す「未確認」のお知らせ（今の週・次の週）。週が始まっても未確認なら急ぎ（赤）。"""
    this_week = today - datetime.timedelta(days=(today.weekday() - 2) % 7)
    alerts = []
    for week in (this_week, this_week + datetime.timedelta(days=7)):
        products = week_products(week)
        if not products or not any(p.rank_id for p in products):
            continue
        check = MenuWeekCheck.objects.filter(week=week).first()
        if check and check.is_confirmed:
            continue
        names = [p.name for p in products if p.no != LARGE_RICE_NO]
        needs = MenuProfile.objects.filter(name__in=names, confirmed=False).count()
        alerts.append({"week": week, "label": f"{week.month}/{week.day}週", "needs_check": needs,
                       "selling": week_state(week, today) == "selling"})
    return alerts
