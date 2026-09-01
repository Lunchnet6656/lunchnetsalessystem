"""狙い撃ち配信（お礼／次回後押し／離反フォロー）の送信ロジック。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプ狙い撃ち配信-要件定義.md

会員の来店行動（StampLog）をトリガーに、LINEへ狙い撃ちpush。運営は「配信設定」画面で
文面・タイミング・対照群%・ON/OFFを編集する（本モジュールはその設定を読んで実行するだけ）。
送信は既存の reservations.line_notify.push_text を流用。効果は対照群（送らない群）と再来率で測る。
"""
import hashlib
import re
from datetime import timedelta

from django.db.models import Count, Max, Min
from django.utils import timezone

from reservations.line_notify import is_configured, push_text
from reservations.models import LineMember
from stamps.models import (
    FollowupSendLog, MessageScenario, RewardTier, StampCard, StampConfig, StampLog,
)

MEASURE_WINDOW_DAYS = 14      # 配信後この日数以内の来店を「再来」とみなす（効果測定）。
FREQ_CAP_DAYS = 7             # 会員あたり狙い撃ちは7日に1通まで。

_VAR_RE = re.compile(r"\{([^}]+)\}")


def next_reward_for(current_pt):
    """現在ptより上で最も近い有効な特典段階（無ければ None＝打ち止め）。"""
    return (RewardTier.objects.filter(active=True, threshold_pt__gt=current_pt)
            .order_by("threshold_pt").first())


def _member_pt(member):
    card = (StampCard.objects.filter(member=member)
            .order_by("-started_on", "-created_at").first())
    return card.stamp_count if card else 0


def expand_template(template, member, menu_url="", pt=None):
    """文面の {変数} を会員の実値で置換。算出不能な変数は空文字（不自然な文を出さない）。

    pt を渡せばその値を使う（テスト・一括処理でクエリを省くため）。未知の変数は素通し。
    """
    if pt is None:
        pt = _member_pt(member)
    nxt = next_reward_for(pt)
    values = {
        "名前": member.name or "",
        "現在pt": str(pt),
        "次の特典まで": str(nxt.threshold_pt - pt) if nxt else "",
        "次の特典": nxt.label if nxt else "",
        "メニューリンク": menu_url or "",
    }
    # 未知の変数は {そのまま} 残す（KeyError で落とさない＝防御的）。
    return _VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def is_held_out(member_id, kind, holdout_pct):
    """対照群（送らない群）か。会員×種類で決定的に振り分ける（毎回同じ群＝ブレさせない）。"""
    if holdout_pct <= 0:
        return False
    h = int(hashlib.md5(f"{member_id}:{kind}".encode()).hexdigest(), 16)
    return (h % 100) < holdout_pct


def _member_stats():
    """会員ごとの初回来店・最終来店・来店回数（1クエリ）。"""
    return {r["card__member_id"]: r for r in
            StampLog.objects.values("card__member_id")
            .annotate(first=Min("stamped_on"), last=Max("stamped_on"),
                      n=Count("stamped_on", distinct=True))}


def _find_targets(scenario, stats, today):
    """シナリオのトリガーに該当する [(member_id, cycle_key), ...] を返す。"""
    off = scenario.offset_days
    out = []
    for mid, s in stats.items():
        if scenario.kind == MessageScenario.KIND_WELCOME:
            if s["first"] == today - timedelta(days=off):
                out.append((mid, s["first"]))
        elif scenario.kind == MessageScenario.KIND_SECOND:
            if s["first"] == today - timedelta(days=off) and s["n"] == 1:
                out.append((mid, s["first"]))
        elif scenario.kind == MessageScenario.KIND_WINBACK:
            if s["last"] == today - timedelta(days=off) and s["n"] >= 2:
                out.append((mid, s["last"]))
    return out


def _recently_messaged(today):
    """直近 FREQ_CAP_DAYS 日に送信済みの会員ID集合（頻度上限用）。"""
    since = today - timedelta(days=FREQ_CAP_DAYS - 1)
    return set(FollowupSendLog.objects
               .filter(status=FollowupSendLog.STATUS_SENT, decided_on__gte=since)
               .values_list("member_id", flat=True))


def run_followups(today=None, dry_run=False):
    """有効な全シナリオを判定して送信（またはdry-run）。サマリー dict を返す。"""
    today = today or timezone.localdate()
    MessageScenario.ensure_defaults()
    cfg = StampConfig.get_solo()
    stats = _member_stats()
    recent = _recently_messaged(today)
    summary = {}

    for scenario in MessageScenario.ordered():
        res = {"sent": 0, "held_out": 0, "failed": 0, "skipped": 0, "targets": 0}
        if not scenario.enabled:
            summary[scenario.kind] = res
            continue
        targets = _find_targets(scenario, stats, today)
        res["targets"] = len(targets)
        # このサイクルで既にログ済みの (member, cycle) は除外（重複防止）。
        done = set(FollowupSendLog.objects
                   .filter(kind=scenario.kind,
                           cycle_key__in=[c for _, c in targets])
                   .values_list("member_id", "cycle_key"))
        member_ids = [mid for mid, c in targets if (mid, c) not in done]
        members = LineMember.objects.in_bulk(member_ids)

        for mid, cycle in targets:
            if (mid, cycle) in done:
                continue
            member = members.get(mid)
            if member is None:
                continue
            # 頻度上限：直近7日に送信済みならスキップ（対照群判定より前＝送りすぎ防止）。
            if mid in recent:
                res["skipped"] += 1
                continue
            if is_held_out(mid, scenario.kind, scenario.holdout_pct):
                if not dry_run:
                    FollowupSendLog.objects.create(
                        member=member, kind=scenario.kind, cycle_key=cycle,
                        decided_on=today, status=FollowupSendLog.STATUS_HELD)
                res["held_out"] += 1
                continue
            # 現在ptは来店回数(n)と別物（2倍ボーナスで増える）＝カードから算出する。
            text = expand_template(scenario.template, member, cfg.menu_url)
            if dry_run:
                res["sent"] += 1
                continue
            ok = bool(member.line_user_id) and is_configured() and push_text(member.line_user_id, text)
            log = FollowupSendLog(member=member, kind=scenario.kind, cycle_key=cycle,
                                  decided_on=today)
            if ok:
                log.status = FollowupSendLog.STATUS_SENT
                log.sent_at = timezone.now()
                res["sent"] += 1
                recent.add(mid)
            else:
                log.status = FollowupSendLog.STATUS_FAILED
                log.detail = "push未送信（未設定/ブロック/失敗）"
                res["failed"] += 1
            log.save()
        summary[scenario.kind] = res
    return summary


def update_revisits(today=None):
    """効果測定：計測窓が締まった送信/対照ログについて、配信後に再来したかを更新する。

    decided_on の翌日〜 +MEASURE_WINDOW_DAYS に来店があれば revisited=True。窓が未了のログは触らない。
    """
    today = today or timezone.localdate()
    mature = FollowupSendLog.objects.filter(
        revisited=False,
        status__in=[FollowupSendLog.STATUS_SENT, FollowupSendLog.STATUS_HELD],
        decided_on__lte=today - timedelta(days=MEASURE_WINDOW_DAYS),
    )
    updated = 0
    for log in mature:
        lo = log.decided_on + timedelta(days=1)
        hi = log.decided_on + timedelta(days=MEASURE_WINDOW_DAYS)
        came = StampLog.objects.filter(
            card__member_id=log.member_id, stamped_on__gte=lo, stamped_on__lte=hi).exists()
        if came:
            log.revisited = True
            log.save(update_fields=["revisited"])
            updated += 1
    return updated


def results_by_kind():
    """画面表示用：kindごとに 送信数/対照数/再来率(送信/対照) を集計。"""
    out = {}
    for kind, _ in MessageScenario.KIND_CHOICES:
        logs = FollowupSendLog.objects.filter(kind=kind)
        sent = logs.filter(status=FollowupSendLog.STATUS_SENT)
        held = logs.filter(status=FollowupSendLog.STATUS_HELD)

        def rate(qs):
            t = qs.count()
            return round(qs.filter(revisited=True).count() / t * 100, 1) if t else None

        out[kind] = {"sent": sent.count(), "held": held.count(),
                     "sent_rate": rate(sent), "held_rate": rate(held)}
    return out
