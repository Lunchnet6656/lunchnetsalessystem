"""メニューの登録（元のメニュー表から送られた週のメニュー → Product）と、値段の種類・容器の判定。

判定の順番：
  ① メニュー辞書に同じ名前があれば、それを使う
  ② なければ判定ルールで決め、辞書に「要確認」として登録
  ③ どのルールにも当たらなければ「通常・黒容器」で「要確認」
仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md §6.2・§8.3
"""
import datetime
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from sales.models import (
    PRICE_PATTERNS, ClassifyRule, MenuProfile, MenuWeekCheck, PriceRank, Product,
)
from sales.pricing import PriceBook

MENU_SLOTS = 10            # 元のメニュー表の①〜⑩（⑪⑫は今は使わない）
LARGE_RICE_NO = 11
LARGE_RICE_NAME = "大盛りごはん"
DEFAULT_RANK = "通常"
DEFAULT_CONTAINER = "黒容器"
WEEK_START_WEEKDAY = 2     # メニューの週は水曜はじまり

# 過去のメニュー（Product.container_type）の表記ゆれをそろえる
CONTAINER_ALIASES = {"黒容器など": "黒容器", "ごはん容器": "ご飯容器"}


def normalize_name(name):
    return (name or "").strip().strip("　").strip()


def week_end(week):
    return week + datetime.timedelta(days=6)


def week_state(week, today):
    """"upcoming"（開始前）／"selling"（販売中）／"ended"（終わった週）"""
    if today < week:
        return "upcoming"
    if today <= week_end(week):
        return "selling"
    return "ended"


@dataclass
class Classification:
    rank: PriceRank
    container: str
    rank_rule: object = None       # 種類を決めたルール（なければ既定）
    container_rule: object = None


def classify(name, rules=None, ranks=None):
    """判定ルールだけで決める（辞書は見ない）。種類と容器は、それぞれ上から最初に当たったルールで決める。"""
    rules = list(ClassifyRule.objects.select_related("rank")) if rules is None else rules
    ranks = ranks or {r.name: r for r in PriceRank.objects.all()}
    rank_rule = next((r for r in rules if r.rank_id and r.keyword in name), None)
    container_rule = next((r for r in rules if r.container and r.keyword in name), None)
    return Classification(
        rank=rank_rule.rank if rank_rule else ranks[DEFAULT_RANK],
        container=container_rule.container if container_rule else DEFAULT_CONTAINER,
        rank_rule=rank_rule, container_rule=container_rule,
    )


def profile_for(name, week, rules=None, ranks=None):
    """辞書から引く。なければ判定ルールで作って「要確認」にする。"""
    name = normalize_name(name)
    profile = MenuProfile.objects.filter(name=name).select_related("rank").first()
    if profile is None:
        c = classify(name, rules, ranks)
        profile = MenuProfile.objects.create(name=name, rank=c.rank, container=c.container,
                                             confirmed=False, first_seen=week, last_seen=week)
    else:
        changed = []
        if profile.last_seen is None or profile.last_seen < week:
            profile.last_seen = week
            changed.append("last_seen")
        if profile.first_seen is None or profile.first_seen > week:
            profile.first_seen = week
            changed.append("first_seen")
        if changed:
            profile.save(update_fields=changed + ["updated_at"])
    return profile


def prices_for_rank(rank, week):
    """Product.price_A/B/C にも、週の初日の価格表の値段を入れておく（古い画面の表示と予備のため）。"""
    book = PriceBook(week)
    probe = Product(rank=rank)
    return {f"price_{p}": book.price(probe, p) for p in PRICE_PATTERNS}


@dataclass
class RegisterResult:
    week: datetime.date
    products: list
    needs_check: list      # 要確認のメニュー名
    state: str
    resent_after_confirm: bool


@transaction.atomic
def register_week(week, names, now=None):
    """週のメニュー10品＋大盛りごはんを登録する（同じ週を送り直したら上書き）。週は「未確認」に戻す。"""
    now = now or timezone.now()
    rules = list(ClassifyRule.objects.select_related("rank"))
    ranks = {r.name: r for r in PriceRank.objects.all()}
    week_key = week.isoformat()   # Product.week・ItemQuantity.target_week と同じ書き方
    products, needs_check = [], []
    for no, name in [(i + 1, n) for i, n in enumerate(names)] + [(LARGE_RICE_NO, LARGE_RICE_NAME)]:
        profile = profile_for(name, week, rules, ranks)
        if no == LARGE_RICE_NO and not profile.confirmed:
            # 大盛りごはんはアプリが足す固定の行（確認画面でも選べない）ので、要確認にしない
            profile.confirmed = True
            profile.save(update_fields=["confirmed", "updated_at"])
        if not profile.confirmed:
            needs_check.append(profile.name)
        product, _ = Product.objects.update_or_create(
            week=week_key, no=no,
            defaults={"name": profile.name, "rank": profile.rank, "container_type": profile.container,
                      **prices_for_rank(profile.rank, week)},
        )
        products.append(product)

    check, created = MenuWeekCheck.objects.get_or_create(week=week)
    resent = (not created) and check.is_confirmed
    check.received_at, check.received_count = now, len(products)
    check.confirmed_at, check.confirmed_by = None, None
    check.resent_after_confirm = resent
    check.save()
    return RegisterResult(week, products, needs_check, week_state(week, timezone.localdate()), resent)


@transaction.atomic
def update_profile(profile, rank, container, user, today):
    """辞書を直して確認済みにする。開始前の週のメニューにもすぐ反映する（販売中・終わった週は変えない）。
    戻り値：(反映した開始前の週, そのメニューが出ている販売中の週)"""
    profile.rank, profile.container, profile.confirmed = rank, container, True
    profile.updated_by = user if getattr(user, "is_authenticated", False) else None
    profile.save()
    upcoming, selling = [], []
    for product in Product.objects.filter(name=profile.name):
        week = parse_week(product.week)
        if week is None:
            continue
        state = week_state(week, today)
        if state == "upcoming":
            product.rank, product.container_type = rank, container
            for field_name, value in prices_for_rank(rank, week).items():
                setattr(product, field_name, value)
            product.save()
            upcoming.append(week)
        elif state == "selling" and (product.rank_id != rank.id or product.container_type != container):
            selling.append(week)
    return sorted(set(upcoming)), sorted(set(selling))


def parse_week(text):
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(str(text).strip(), fmt).date()
        except ValueError:
            continue
    return None
