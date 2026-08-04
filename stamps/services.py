"""スタンプ付与・特典使用のドメインロジック。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md（§4）

来店スタンプの付与は次の順で判定する：
  1. 出店時間内か（店舗ごとの時間帯制限。GPSは使わない＝スピード優先）
  2. 同日すでに押していないか（1日1回／会員単位）
  3. 利用できるカードがあるか（満了・期限切れなら新カードで再スタート＝次回来店から）
  4. スタンプ+1 → 到達ptで特典発行

結果は StampResult（status＋カード＋新規特典）で返す。view はこれを文言に変換して表示する。
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from stamps.models import (
    BONUS_NONE, BONUS_RAIN, BONUS_STREAK,
    CAP_PT, CARD_VALIDITY_DAYS, REWARD_VALIDITY_DAYS,
    Reward, RewardTier, StampCard, StampConfig, StampLog,
)


def effective_cap():
    """カード満了pt（＝有効な段階の最大到達pt）。運用設定・特典段階から動的に決まる。"""
    return RewardTier.effective_cap()


# 出店時間の全店共通デフォルト（店舗が個別設定を持たない場合のフォールバック）。
DEFAULT_OPEN_TIME = time(11, 0)
DEFAULT_CLOSE_TIME = time(13, 30)


# 付与結果のステータス。
STAMPED = "stamped"             # 押せた
ALREADY_TODAY = "already_today"  # 本日すでに押印済み
OUTSIDE_HOURS = "outside_hours"  # 出店時間外
COMPLETED = "completed"          # （旧）打ち止め据え置き。満了→次回新カードへ変更し未使用。互換のため残置


@dataclass
class StampResult:
    status: str
    card: StampCard = None
    new_reward: Reward = None       # 表示用の代表特典（同時獲得なら最上位）。
    points: int = 1                 # この来店で実際に押したスタンプ数（1 or 2）。
    bonus_reason: str = BONUS_NONE  # 2倍になった理由（rain / streak / ""）。

    @property
    def ok(self):
        return self.status == STAMPED

    @property
    def doubled(self):
        return self.points >= 2


def location_open_window(location):
    """その店舗の出店時間 (open, close)。個別設定が無ければ全店共通デフォルト。"""
    open_t = getattr(location, "stamp_open_time", None) or DEFAULT_OPEN_TIME
    close_t = getattr(location, "stamp_close_time", None) or DEFAULT_CLOSE_TIME
    return open_t, close_t


def is_within_open_hours(location, now):
    open_t, close_t = location_open_window(location)
    return open_t <= timezone.localtime(now).time() <= close_t


def _current_card(member):
    """会員の最新カード（無ければ None）。"""
    return member.stamp_cards.order_by("-started_on", "-created_at").first()


def _new_card(member, today):
    return StampCard.objects.create(
        member=member,
        started_on=today,
        expires_on=today + timedelta(days=StampConfig.get_solo().card_validity_days),
    )


def _issue_reward_for_tier(card, tier, now):
    """指定段階の特典を発行して返す（既に発行済みなら再取得して返す）。"""
    # クーポンの利用開始・有効日数は運用設定（StampConfig）に従う。
    #   利用開始＝獲得の翌日（既定）＝次回来店から／期限＝獲得日から N 日（カードと独立）。
    cfg = StampConfig.get_solo()
    issued_date = timezone.localtime(now).date()
    reward_valid_from = issued_date + timedelta(days=1 if cfg.reward_starts_next_day else 0)
    reward_expires = issued_date + timedelta(days=cfg.reward_validity_days)
    try:
        return Reward.objects.create(
            card=card,
            tier=tier,
            threshold_pt=tier.threshold_pt,
            kind=tier.kind,
            label=tier.label,
            benefit_cost=tier.benefit_cost,
            issued_at=now,
            valid_from=reward_valid_from,
            expires_on=reward_expires,
        )
    except IntegrityError:
        # 既に同段階を発行済み（競合）。再取得して返す。
        return Reward.objects.filter(card=card, threshold_pt=tier.threshold_pt).first()


def _issue_rewards_crossing(card, old_count, new_count, now):
    """(old_count, new_count] を跨いだ全段階の特典を発行してリストで返す。

    通常は+1なので1段階だが、2倍で+2したときは 4→6 のように5ptを飛び越すことがある。
    ちょうど到達だけを見ると飛び越した段階の特典が漏れるため、範囲で拾う。
    """
    tiers = RewardTier.objects.filter(
        active=True, threshold_pt__gt=old_count, threshold_pt__lte=new_count,
    ).order_by("threshold_pt")
    return [_issue_reward_for_tier(card, t, now) for t in tiers]


def _streak_len_including_today(member, today):
    """今日を含めた連続来店日数。今日はまだ押していない前提で、昨日から遡って数える。"""
    length = 1  # 今日ぶん
    day = today - timedelta(days=1)
    prior = set(
        StampLog.objects.filter(
            card__member=member, stamped_on__lt=today,
            stamped_on__gte=today - timedelta(days=60),
        ).values_list("stamped_on", flat=True)
    )
    while day in prior:
        length += 1
        day -= timedelta(days=1)
    return length


def _bonus_for(member, today, cfg):
    """今日の来店が2倍か判定して (points, reason) を返す。重複しても最大2倍で据え置き。"""
    if cfg.is_rain_bonus_on(today):
        return 2, BONUS_RAIN
    if cfg.streak_bonus_enabled and cfg.streak_bonus_days >= 1:
        streak = _streak_len_including_today(member, today)
        if streak % cfg.streak_bonus_days == 0:
            return 2, BONUS_STREAK
    return 1, BONUS_NONE


@transaction.atomic
def award_stamp(member, location, now=None):
    """来店スタンプを1個付与する。判定の主体。"""
    now = now or timezone.now()
    today = timezone.localtime(now).date()

    # 1. 出店時間内か
    if not is_within_open_hours(location, now):
        return StampResult(OUTSIDE_HOURS)

    # 2. 本日すでに押していないか（会員単位・1日1回）
    already = StampLog.objects.filter(card__member=member, stamped_on=today).exists()
    if already:
        card = _current_card(member)
        return StampResult(ALREADY_TODAY, card=card)

    # 3. 利用できるカードを決める
    cap = effective_cap()
    card = _current_card(member)
    if card is None or card.is_expired_on(today) or card.stamp_count >= cap:
        # このサイクルを閉じて新カードで再スタートする。閉じる理由は2つ：
        #   ・満了(20pt)  … 満了した次回来店から新カード（2倍デー等で早く貯めても即再スタート）。
        #                   満了カードは COMPLETED のまま履歴に残す（据え置き）。
        #   ・期限切れ    … 有効(ACTIVE)なまま期限を過ぎたカードは EXPIRED に片付ける。
        if card is not None and card.status == StampCard.STATUS_ACTIVE:
            card.status = StampCard.STATUS_EXPIRED
            card.save(update_fields=["status"])
        card = _new_card(member, today)

    # 4. 2倍イベント判定 → スタンプ加算（打ち止めptを超えないようクランプ）
    bonus_pt, reason = _bonus_for(member, today, StampConfig.get_solo())
    old_count = card.stamp_count
    new_count = min(old_count + bonus_pt, cap)
    applied = new_count - old_count  # cap手前だと2倍でも1しか入らないことがある

    try:
        StampLog.objects.create(
            card=card, location=location, stamped_on=today, stamped_at=now,
            points=applied, bonus_reason=(reason if applied >= 2 else BONUS_NONE),
        )
    except IntegrityError:
        # 同日同カードの二重押し（競合）。現状を返す。
        return StampResult(ALREADY_TODAY, card=card)

    card.stamp_count = new_count
    update_fields = ["stamp_count"]
    new_rewards = _issue_rewards_crossing(card, old_count, new_count, now)
    if card.stamp_count >= cap:
        card.status = StampCard.STATUS_COMPLETED
        update_fields.append("status")
    card.save(update_fields=update_fields)

    # 表示用は最上位の新規特典（同時に複数跨いだ場合）。
    new_reward = new_rewards[-1] if new_rewards else None
    return StampResult(STAMPED, card=card, new_reward=new_reward,
                       points=applied, bonus_reason=(reason if applied >= 2 else BONUS_NONE))


@transaction.atomic
def use_reward(reward, now=None):
    """特典を使用済みにする（B案：客が押す→目視確認）。二重使用は弾く。"""
    now = now or timezone.now()
    locked = Reward.objects.select_for_update().get(pk=reward.pk)
    if locked.status != Reward.STATUS_ISSUED:
        return False
    today = timezone.localtime(now).date()
    if today < locked.valid_from:
        # 獲得した来店ではまだ使えない（次回来店から）。状態は据え置き。
        return False
    if today > locked.expires_on:
        locked.status = Reward.STATUS_EXPIRED
        locked.save(update_fields=["status"])
        return False
    locked.status = Reward.STATUS_USED
    locked.used_at = now
    locked.save(update_fields=["status", "used_at"])
    return True


def reward_short_label(tier):
    """カード・POPに出す短い特典名（例：50円引き／お弁当無料）。表示ラベルの単一の出所。"""
    if tier.kind == RewardTier.KIND_FREE:
        return "お弁当無料"
    if tier.discount_yen:
        return f"{tier.discount_yen}円引き"
    return tier.label


def _reward_icon(tier):
    return "🍱" if tier.kind == RewardTier.KIND_FREE else "🎫"


def _chip_label(tier):
    """スタンプ枠の下に出す煽りラベル（絵文字なし・1行）。例：50円引きGET! ／ お弁当1個無料!"""
    if tier.kind == RewardTier.KIND_FREE:
        return "お弁当1個無料!"
    if tier.discount_yen:
        return f"{tier.discount_yen}円引きGET!"
    return tier.label


def usable_rewards(member, exclude_card=None, now=None):
    """会員の「いま使えるクーポン」（発行済・期限内）。カード横断で集める。

    独立期限により、カードが切り替わっても前カードのクーポンは生きている。
    その「前のカードのクーポン」への導線（一覧）を作るために使う。
    """
    if member is None:
        return []
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    qs = (Reward.objects
          .filter(card__member=member, status=Reward.STATUS_ISSUED,
                  valid_from__lte=today, expires_on__gte=today)
          .select_related("card"))
    if exclude_card is not None:
        qs = qs.exclude(card_id=exclude_card.id)
    return list(qs.order_by("expires_on", "threshold_pt"))


def card_view_state(card, just_stamped_pt=1):
    """カード表示用の状態（スタンプ数・各段階の状態・次ゴールまでの残り）。

    just_stamped_pt は今の来店で押した数（2倍なら2）。押した感アニメで光らせる枠数に使う。
    """
    tiers = list(RewardTier.objects.filter(active=True).order_by("threshold_pt"))
    rewards = {r.threshold_pt: r for r in card.rewards.all()} if card else {}
    count = card.stamp_count if card else 0
    rows = []
    for t in tiers:
        r = rewards.get(t.threshold_pt)
        pending = False
        if r is not None:
            if r.status == Reward.STATUS_ISSUED and r.is_pending:
                state = "pending"        # 獲得済みだが次回来店から
                pending = True
            else:
                state = r.status         # issued(=使える) / used / expired
        elif count >= t.threshold_pt:
            state = Reward.STATUS_ISSUED
        else:
            state = "locked"
        rows.append({
            "tier": t,
            "reward": r,
            "state": state,
            "pending": pending,
            "reached": count >= t.threshold_pt,
            "short_label": reward_short_label(t),
            "icon": _reward_icon(t),
            "remaining": max(0, t.threshold_pt - count),
        })
    next_tier = next((t for t in tiers if t.threshold_pt > count), None)
    remaining = (next_tier.threshold_pt - count) if next_tier else 0
    next_label = reward_short_label(next_tier) if next_tier else ""

    # スタンプ枠（1..cap）。goal の枠には短い特典名とアイコンを出す。
    cap = effective_cap()
    goal_pts = {t.threshold_pt: t for t in tiers}
    slots = []
    for num in range(1, cap + 1):
        tier_at = goal_pts.get(num)
        slots.append({
            "num": num,
            "on": num <= count,
            "goal": num in goal_pts,
            "goal_label": (reward_short_label(tier_at) if tier_at else ""),
            "goal_chip": (_chip_label(tier_at) if tier_at else ""),
            "goal_icon": (_reward_icon(tier_at) if tier_at else ""),
            "just_earned": (tier_at is not None and num == count),
            # 今押したばかりの枠（押した感の対象）。2倍なら直近2枠が対象。
            "just_stamped": (count > 0 and count - max(1, just_stamped_pt) < num <= count),
        })

    return {
        "count": count,
        "cap": cap,
        "rows": rows,
        "slots": slots,
        "next_tier": next_tier,
        "next_label": next_label,
        "remaining": remaining,
        "capped": count >= cap,
        "pct": round(count / cap * 100) if cap else 0,
        "started_on": card.started_on if card else None,
        "expires_on": card.expires_on if card else None,
        "validity_days": StampConfig.get_solo().card_validity_days,
    }
