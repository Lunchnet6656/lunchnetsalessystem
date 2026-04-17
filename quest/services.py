import logging
from datetime import date, time, timedelta
from decimal import Decimal

from django.db.models import Sum, Q, F, Count
from django.utils import timezone

from .models import (
    QuestProfile, MissionTemplate, DailyMission,
    Achievement, UserAchievement,
)

logger = logging.getLogger(__name__)

# レベルテーブル: (level, required_total_xp, title, character_image)
# 称号はマイルストーンレベルで切り替わる（例: Lv1-4は「見習い販売員」、Lv5-9は「販売スタッフ」）
_T = 'quest/img/titles/'
LEVEL_TABLE = [
    # Lv1-4: 見習い販売員
    (1,   0,        '見習い販売員',     _T + 'lv001_minarai.png'),
    (2,   75,       '見習い販売員',     _T + 'lv001_minarai.png'),
    (3,   150,      '見習い販売員',     _T + 'lv001_minarai.png'),
    (4,   225,      '見習い販売員',     _T + 'lv001_minarai.png'),
    # Lv5-9: 販売スタッフ
    (5,   300,      '販売スタッフ',     _T + 'lv005_hanbai_staff.png'),
    (6,   480,      '販売スタッフ',     _T + 'lv005_hanbai_staff.png'),
    (7,   660,      '販売スタッフ',     _T + 'lv005_hanbai_staff.png'),
    (8,   840,      '販売スタッフ',     _T + 'lv005_hanbai_staff.png'),
    (9,   1020,     '販売スタッフ',     _T + 'lv005_hanbai_staff.png'),
    # Lv10-14: ひとり立ち販売員
    (10,  1200,     'ひとり立ち販売員', _T + 'lv010_hitoridachi.png'),
    (11,  1460,     'ひとり立ち販売員', _T + 'lv010_hitoridachi.png'),
    (12,  1720,     'ひとり立ち販売員', _T + 'lv010_hitoridachi.png'),
    (13,  1980,     'ひとり立ち販売員', _T + 'lv010_hitoridachi.png'),
    (14,  2240,     'ひとり立ち販売員', _T + 'lv010_hitoridachi.png'),
    # Lv15-19: 頼れる販売員
    (15,  2500,     '頼れる販売員',     _T + 'lv015_tayoreru.png'),
    (16,  3100,     '頼れる販売員',     _T + 'lv015_tayoreru.png'),
    (17,  3700,     '頼れる販売員',     _T + 'lv015_tayoreru.png'),
    (18,  4300,     '頼れる販売員',     _T + 'lv015_tayoreru.png'),
    (19,  4650,     '頼れる販売員',     _T + 'lv015_tayoreru.png'),
    # Lv20-29: 人気販売員
    (20,  5000,     '人気販売員',       _T + 'lv020_ninki.png'),
    (21,  6500,     '人気販売員',       _T + 'lv020_ninki.png'),
    (22,  8000,     '人気販売員',       _T + 'lv020_ninki.png'),
    (23,  9500,     '人気販売員',       _T + 'lv020_ninki.png'),
    (24,  11000,    '人気販売員',       _T + 'lv020_ninki.png'),
    (25,  12500,    '人気販売員',       _T + 'lv020_ninki.png'),
    (26,  14000,    '人気販売員',       _T + 'lv020_ninki.png'),
    (27,  15500,    '人気販売員',       _T + 'lv020_ninki.png'),
    (28,  17000,    '人気販売員',       _T + 'lv020_ninki.png'),
    (29,  18500,    '人気販売員',       _T + 'lv020_ninki.png'),
    # Lv30-39: ランチのベテラン
    (30,  20000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    (31,  23000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    (32,  26000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    (33,  29000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    (34,  32000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    (35,  35000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    (36,  38000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    (37,  41000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    (38,  44000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    (39,  47000,    'ランチのベテラン', _T + 'lv030_veteran.png'),
    # Lv40-49: ランチの看板娘
    (40,  50000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    (41,  55000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    (42,  60000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    (43,  65000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    (44,  70000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    (45,  75000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    (46,  80000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    (47,  85000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    (48,  90000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    (49,  95000,    'ランチの看板娘',   _T + 'lv040_kanban.png'),
    # Lv50-59: ランチのエース
    (50,  100000,   'ランチのエース',   _T + 'lv050_ace.png'),
    (51,  110000,   'ランチのエース',   _T + 'lv050_ace.png'),
    (52,  120000,   'ランチのエース',   _T + 'lv050_ace.png'),
    (53,  130000,   'ランチのエース',   _T + 'lv050_ace.png'),
    (54,  140000,   'ランチのエース',   _T + 'lv050_ace.png'),
    (55,  150000,   'ランチのエース',   _T + 'lv050_ace.png'),
    (56,  160000,   'ランチのエース',   _T + 'lv050_ace.png'),
    (57,  170000,   'ランチのエース',   _T + 'lv050_ace.png'),
    (58,  180000,   'ランチのエース',   _T + 'lv050_ace.png'),
    (59,  190000,   'ランチのエース',   _T + 'lv050_ace.png'),
    # Lv60-69: ランチのアイドル
    (60,  200000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    (61,  220000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    (62,  240000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    (63,  260000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    (64,  280000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    (65,  300000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    (66,  320000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    (67,  340000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    (68,  360000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    (69,  380000,   'ランチのアイドル', _T + 'lv060_idol.png'),
    # Lv70-79: ランチの魔術師
    (70,  400000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    (71,  430000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    (72,  460000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    (73,  490000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    (74,  520000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    (75,  550000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    (76,  580000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    (77,  610000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    (78,  640000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    (79,  670000,   'ランチの魔術師',   _T + 'lv070_magician.png'),
    # Lv80-89: ランチの匠
    (80,  700000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    (81,  730000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    (82,  760000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    (83,  790000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    (84,  820000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    (85,  850000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    (86,  880000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    (87,  910000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    (88,  940000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    (89,  970000,   'ランチの匠',       _T + 'lv080_takumi.png'),
    # Lv90-94: 伝説の販売員
    (90,  1000000,  '伝説の販売員',     _T + 'lv090_legend.png'),
    (91,  1060000,  '伝説の販売員',     _T + 'lv090_legend.png'),
    (92,  1120000,  '伝説の販売員',     _T + 'lv090_legend.png'),
    (93,  1180000,  '伝説の販売員',     _T + 'lv090_legend.png'),
    (94,  1240000,  '伝説の販売員',     _T + 'lv090_legend.png'),
    # Lv95-98: 至高の販売員
    (95,  1300000,  '至高の販売員',     _T + 'lv095_supreme.png'),
    (96,  1375000,  '至高の販売員',     _T + 'lv095_supreme.png'),
    (97,  1450000,  '至高の販売員',     _T + 'lv095_supreme.png'),
    (98,  1525000,  '至高の販売員',     _T + 'lv095_supreme.png'),
    # Lv99: 究極の販売員
    (99,  1600000,  '究極の販売員',     _T + 'lv099_ultimate.png'),
    # Lv100: ランチクイーン
    (100, 2000000,  'ランチクイーン',   _T + 'lv100_queen.png'),
]

# 称号説明文: level -> description
TITLE_DESCRIPTIONS = {
    1:   'ランチ販売の世界に足を踏み入れたばかりの新人。笑顔と元気を武器に、今日も一歩ずつ成長中。',
    5:   '売場の流れを少しずつ覚えはじめた販売員。お弁当を手に、お客様への声かけにも慣れてきた。',
    10:  '自分の力で売場をまわせるようになった販売員。落ち着いた接客で、安心感のある存在へ。',
    15:  '周りからも信頼される、売場の頼れる存在。忙しい時間でも笑顔を忘れず、お客様を迎える。',
    20:  '親しみやすい接客で、多くのお客様に覚えられる存在。思わずまた会いに来たくなる販売員。',
    30:  '経験を重ね、売場の空気を読む力を身につけた実力派。落ち着きと安定感でランチ時間を支える。',
    40:  '明るさと華やかさで売場を彩る人気者。その場にいるだけで、ランチの時間が少し楽しくなる。',
    50:  '売上も接客も頼りになる、売場の中心的存在。ここぞという場面で力を発揮する実力者。',
    60:  '多くのお客様に愛され、自然と人を惹きつける存在。笑顔ひとつで売場の雰囲気を明るく変える。',
    70:  'お客様の心をつかむ接客で、次々と笑顔を生み出す特別な販売員。その手腕はまるで魔法のよう。',
    80:  '接客、気配り、売場づくり、そのすべてを高いレベルで極めた職人級の存在。まさにランチ販売の匠。',
    90:  '長年の経験と圧倒的な実力で語り継がれる存在。その姿を見た誰もが、一流と認める販売員。',
    95:  '技術も魅力も磨き上げられた、まさに最高峰の販売員。たどり着ける人はほんのひと握り。',
    99:  '販売員としての力を極限まで高めた究極の存在。接客も売場づくりも、すべてが完成の域にある。',
    100: 'ランチ販売の頂点に立つ、気品と実力を兼ね備えた特別な存在。誰もが憧れる、最高称号の販売員。',
}


def get_title_by_level(level):
    """指定レベルのLEVEL_TABLEエントリを返す。なければNone"""
    for entry in LEVEL_TABLE:
        if entry[0] == level:
            return entry
    return None


def get_title_description(level):
    """レベルに対応する称号説明文を返す。
    中間レベルは同じ称号グループのマイルストーンレベルの説明文を使用する。"""
    entry = get_title_by_level(level)
    if entry is None:
        return ''
    title_name = entry[2]
    # 同じ称号名を持つ最小（マイルストーン）レベルを探す
    for e in LEVEL_TABLE:
        if e[2] == title_name:
            return TITLE_DESCRIPTIONS.get(e[0], '')
    return ''


def get_level_info(total_xp):
    """total_xpに対応するレベル情報を返す"""
    result = LEVEL_TABLE[0]
    for entry in LEVEL_TABLE:
        if total_xp >= entry[1]:
            result = entry
        else:
            break
    return result


def get_next_level_info(current_level):
    """次のレベル情報を返す。最大レベルならNone"""
    for i, entry in enumerate(LEVEL_TABLE):
        if entry[0] == current_level and i + 1 < len(LEVEL_TABLE):
            return LEVEL_TABLE[i + 1]
    return None


def get_xp_for_next_level(total_xp):
    """次レベルまでに必要な残りXPを返す"""
    next_info = None
    for entry in LEVEL_TABLE:
        if total_xp < entry[1]:
            next_info = entry
            break
    if next_info is None:
        return 0
    return next_info[1] - total_xp


def get_xp_progress(total_xp):
    """現在レベル内でのXP進捗率(0-100)を返す"""
    current = get_level_info(total_xp)
    next_info = get_next_level_info(current[0])
    if next_info is None:
        return 100
    level_start = current[1]
    level_end = next_info[1]
    if level_end == level_start:
        return 100
    progress = (total_xp - level_start) / (level_end - level_start) * 100
    return min(100, int(progress))


def get_or_create_profile(user):
    """QuestProfile取得/作成"""
    profile, created = QuestProfile.objects.get_or_create(user=user)
    return profile


def award_xp(profile, amount):
    """XP加算、レベルアップ・称号変化判定

    戻り値: (new_level, leveled_up, title_changed, prev_title, new_title)
    """
    old_level = profile.level
    old_title = profile.title
    profile.xp += amount
    profile.total_xp += amount

    level_info = get_level_info(profile.total_xp)
    profile.level = level_info[0]
    profile.title = level_info[2]
    profile.character_image = level_info[3]
    profile.save()

    leveled_up = profile.level > old_level
    title_changed = profile.title != old_title
    return profile.level, leveled_up, title_changed, old_title, profile.title


def get_user_performance_baseline(user, mission_date, days=30):
    """過去N日間の販売実績からパーソナルベースラインを計算する。

    戻り値: {'avg_sales_qty': Decimal, 'avg_revenue': Decimal, 'data_days': int}
    実績データが存在しない場合は None を返す。
    """
    from sales.models import DailyReport

    start_date = mission_date - timedelta(days=days)
    person_name = f'{user.last_name} {user.first_name}'.strip()

    reports = DailyReport.objects.filter(
        person_in_charge=person_name,
        date__gte=start_date,
        date__lt=mission_date,
    )

    count = reports.count()
    if count == 0:
        return None

    totals = reports.aggregate(
        sum_qty=Sum('total_sales_quantity'),
        sum_rev=Sum('total_revenue'),
    )

    avg_sales_qty = (totals['sum_qty'] or Decimal('0')) / count
    avg_revenue = (totals['sum_rev'] or Decimal('0')) / count

    return {
        'avg_sales_qty': avg_sales_qty,
        'avg_revenue': avg_revenue,
        'data_days': count,
    }


def get_difficulty_multiplier(level):
    """レベルに応じた難易度倍率を返す。

    低レベル: 平均より低いターゲット（達成しやすく動機づけ）
    高レベル: 平均を超えるターゲット（実力に見合う挑戦）
    """
    if level <= 10:
        return Decimal('0.85')   # 初心者: 平均の85%
    elif level <= 30:
        return Decimal('0.95')   # 成長期: 平均の95%
    elif level <= 60:
        return Decimal('1.00')   # 中級者: ちょうど平均
    elif level <= 90:
        return Decimal('1.08')   # 上級者: 平均の108%
    else:
        return Decimal('1.15')   # 達人: 平均の115%


def _calc_xp_reward(base_xp, target, baseline_avg):
    """難易度に基づいたXP報酬を計算する。

    ターゲットが自分の平均を超えるほどボーナスXPが加算される。
    最大2倍まで。
    """
    if baseline_avg is None or baseline_avg <= 0:
        return base_xp

    stretch_ratio = target / baseline_avg
    # 平均超過分の50%をボーナス（例: 10%上 → +5% XP）
    bonus_factor = Decimal('1.0') + max(Decimal('0'), stretch_ratio - Decimal('1.0')) * Decimal('0.5')
    bonus_factor = min(bonus_factor, Decimal('2.0'))

    return max(base_xp, int(base_xp * bonus_factor))


def generate_daily_missions(user, mission_date):
    """日次ミッションを自動生成（未参加者はスキップ）。

    ミッションのターゲット値はユーザーの過去実績とレベルに応じた
    難易度倍率を組み合わせて算出する。
    """
    # 未参加者はミッション生成しない
    profile = get_or_create_profile(user)
    if not profile.is_participating:
        return []

    existing = DailyMission.objects.filter(user=user, date=mission_date)
    if existing.exists():
        return list(existing)

    templates = MissionTemplate.objects.filter(is_active=True)
    missions = []

    # パーソナルベースラインと難易度係数を取得
    baseline = get_user_performance_baseline(user, mission_date)
    multiplier = get_difficulty_multiplier(profile.level)

    for tmpl in templates:
        target = Decimal('0')
        title = tmpl.name_template
        description = tmpl.description
        xp_reward = tmpl.base_xp

        if tmpl.mission_type == 'sales_count':
            if baseline and baseline['avg_sales_qty'] > 0:
                # 個人平均 × 難易度係数、5食単位で切り上げ
                raw = baseline['avg_sales_qty'] * multiplier
                target = Decimal(str(int((raw / 5).to_integral_value() * 5)))
                target = max(target, Decimal('10'))  # 最低10食
                xp_reward = _calc_xp_reward(tmpl.base_xp, target, baseline['avg_sales_qty'])
                title = f"{int(target)}食以上を販売せよ！"
            else:
                target = Decimal('30')
                title = "30食以上を販売せよ！"

        elif tmpl.mission_type == 'zero_waste':
            target = Decimal('0')
            title = "廃棄ゼロを達成せよ！"

        elif tmpl.mission_type == 'sold_out':
            target = Decimal('1')
            title = "完売を達成せよ！"

        elif tmpl.mission_type == 'sold_out_time':
            target = Decimal('1230')  # 12:30を数値で表現
            title = "12:30までに完売せよ！"

        elif tmpl.mission_type == 'revenue':
            if baseline and baseline['avg_revenue'] > 0:
                # 個人平均売上 × 難易度係数、1,000円単位で切り上げ
                raw = baseline['avg_revenue'] * multiplier
                target = Decimal(str(int((raw / 1000).to_integral_value() * 1000)))
                target = max(target, Decimal('5000'))  # 最低5,000円
                xp_reward = _calc_xp_reward(tmpl.base_xp, target, baseline['avg_revenue'])
                title = f"売上{int(target):,}円以上を達成せよ！"
            else:
                target = Decimal('20000')
                title = "売上20,000円以上を達成せよ！"

        mission = DailyMission.objects.create(
            user=user,
            date=mission_date,
            template=tmpl,
            title=title,
            description=description,
            target_value=target,
            xp_reward=xp_reward,
        )
        missions.append(mission)

    return missions


def evaluate_missions(user, daily_report):
    """DailyReport保存時にミッション達成を判定

    戻り値: dict {
        'completed_missions': list[DailyMission],
        'xp_gained': int,
        'prev_level': int,
        'new_level': int,
        'leveled_up': bool,
        'prev_title': str,
        'new_title': str,
        'title_changed': bool,
        'new_achievements': list[UserAchievement],
    }
    """
    from sales.models import DailyReportEntry

    missions = DailyMission.objects.filter(
        user=user,
        date=daily_report.date,
        status='pending',
    )

    empty_result = {
        'completed_missions': [],
        'xp_gained': 0,
        'prev_level': 0,
        'new_level': 0,
        'leveled_up': False,
        'prev_title': '',
        'new_title': '',
        'title_changed': False,
        'new_achievements': [],
    }

    if not missions.exists():
        profile = get_or_create_profile(user)
        empty_result['prev_level'] = profile.level
        empty_result['new_level'] = profile.level
        empty_result['prev_title'] = profile.title
        empty_result['new_title'] = profile.title
        return empty_result

    profile = get_or_create_profile(user)
    prev_level = profile.level
    prev_title = profile.title
    completed_missions = []
    total_xp_gained = 0

    # 同日の全レポートを集約
    from sales.models import DailyReport as DR
    day_reports = DR.objects.filter(
        date=daily_report.date,
        person_in_charge=f'{user.last_name} {user.first_name}'.strip(),
    )
    total_sales_qty = day_reports.aggregate(
        total=Sum('total_sales_quantity')
    )['total'] or Decimal('0')
    total_remaining = day_reports.aggregate(
        total=Sum('total_remaining')
    )['total'] or Decimal('0')
    total_revenue = day_reports.aggregate(
        total=Sum('total_revenue')
    )['total'] or Decimal('0')

    # 完売判定: いずれかのレポートのエントリにsold_out=Trueがあるか
    has_sold_out = DailyReportEntry.objects.filter(
        report__in=day_reports,
        sold_out=True
    ).exists()

    # 完売時間: 最も早いsold_out_timeを取得（00:00は未設定扱いで除外）
    earliest_sold_out = None
    for r in day_reports:
        if r.sold_out_time and r.sold_out_time != time(0, 0):
            if earliest_sold_out is None or r.sold_out_time < earliest_sold_out:
                earliest_sold_out = r.sold_out_time

    now = timezone.now()

    for mission in missions:
        completed = False
        actual = Decimal('0')

        if mission.template.mission_type == 'sales_count':
            actual = total_sales_qty
            completed = total_sales_qty >= mission.target_value

        elif mission.template.mission_type == 'zero_waste':
            actual = total_remaining
            completed = total_remaining == 0 and total_sales_qty > 0

        elif mission.template.mission_type == 'sold_out':
            actual = Decimal('1') if has_sold_out else Decimal('0')
            completed = has_sold_out

        elif mission.template.mission_type == 'sold_out_time':
            if earliest_sold_out:
                actual = Decimal(str(
                    earliest_sold_out.hour * 100 + earliest_sold_out.minute
                ))
                completed = earliest_sold_out <= time(12, 30)

        elif mission.template.mission_type == 'revenue':
            actual = total_revenue
            completed = total_revenue >= mission.target_value

        mission.actual_value = actual
        if completed:
            mission.status = 'completed'
            mission.completed_at = now
            award_xp(profile, mission.xp_reward)
            # profileをリフレッシュして最新状態を反映
            profile.refresh_from_db()
            total_xp_gained += int(mission.xp_reward)
            completed_missions.append(mission)
        mission.save()

    # ストリーク更新
    if completed_missions:
        update_streak(profile, daily_report.date)

    # 実績チェック（XP加算を含む）
    new_achievements = check_achievements(user)
    if new_achievements:
        profile.refresh_from_db()
        for ua in new_achievements:
            total_xp_gained += ua.achievement.xp_reward

    return {
        'completed_missions': completed_missions,
        'xp_gained': total_xp_gained,
        'prev_level': prev_level,
        'new_level': profile.level,
        'leveled_up': profile.level > prev_level,
        'prev_title': prev_title,
        'new_title': profile.title,
        'title_changed': profile.title != prev_title,
        'new_achievements': new_achievements,
    }


def update_streak(profile, mission_date):
    """連続達成日数を更新"""
    if profile.last_mission_date is None:
        profile.current_streak = 1
    elif profile.last_mission_date == mission_date:
        # 同日の再評価は無視
        return
    elif profile.last_mission_date == mission_date - timedelta(days=1):
        profile.current_streak += 1
    else:
        profile.current_streak = 1

    profile.last_mission_date = mission_date
    if profile.current_streak > profile.longest_streak:
        profile.longest_streak = profile.current_streak
    profile.save()


def check_achievements(user):
    """実績バッジ解除チェック"""
    profile = get_or_create_profile(user)
    earned_codes = set(
        UserAchievement.objects.filter(user=user).values_list('achievement__code', flat=True)
    )
    new_achievements = []

    all_achievements = Achievement.objects.all()
    completed_mission_count = DailyMission.objects.filter(
        user=user, status='completed'
    ).count()

    for achievement in all_achievements:
        if achievement.code in earned_codes:
            continue

        unlocked = False

        if achievement.category == 'streak':
            if profile.current_streak >= achievement.threshold or \
               profile.longest_streak >= achievement.threshold:
                unlocked = True

        elif achievement.category == 'cumulative':
            if completed_mission_count >= achievement.threshold:
                unlocked = True

        elif achievement.category == 'special':
            if achievement.code == 'first_sold_out':
                unlocked = DailyMission.objects.filter(
                    user=user,
                    template__mission_type='sold_out',
                    status='completed',
                ).exists()

            elif achievement.code == 'first_zero_waste':
                unlocked = DailyMission.objects.filter(
                    user=user,
                    template__mission_type='zero_waste',
                    status='completed',
                ).exists()

            elif achievement.code == 'perfect_day':
                # いずれかの日で全ミッション達成
                dates_with_all_completed = (
                    DailyMission.objects.filter(user=user)
                    .values('date')
                    .annotate(
                        total=Count('id'),
                        completed=Count('id', filter=Q(status='completed'))
                    )
                    .filter(total__gt=0, total=F('completed'))
                )
                unlocked = dates_with_all_completed.exists()

        if unlocked:
            ua = UserAchievement.objects.create(user=user, achievement=achievement)
            award_xp(profile, achievement.xp_reward)
            profile.refresh_from_db()
            new_achievements.append(ua)

    return new_achievements


def _find_user_by_person_in_charge(person_in_charge):
    """person_in_charge文字列からユーザーを特定する"""
    from django.contrib.auth import get_user_model
    User = get_user_model()

    if not person_in_charge:
        return None

    name = person_in_charge.strip()

    # 「姓 名」形式でマッチ（DailyReportの標準形式）
    for user in User.objects.all():
        full = f'{user.last_name} {user.first_name}'.strip()
        if full == name:
            return user

    # full_nameフィールドでマッチ
    users = User.objects.filter(full_name=name)
    if users.count() == 1:
        return users.first()

    # first_nameのみでマッチ（フォールバック）
    users = User.objects.filter(first_name=name)
    if users.count() == 1:
        return users.first()

    logger.warning(f"ユーザー '{name}' が見つかりません。スキップします。")
    return None


def evaluate_missions_for_report(daily_report):
    """DailyReportからユーザーを特定してミッション評価を実行（未参加者はスキップ）

    戻り値: evaluate_missions() の結果 dict、または None（スキップ時）
    """
    if not daily_report.person_in_charge:
        return None

    user = _find_user_by_person_in_charge(daily_report.person_in_charge)
    if user is None:
        return None

    # 未参加者はミッション評価しない
    profile = get_or_create_profile(user)
    if not profile.is_participating:
        return None

    # ミッション未生成なら生成
    if not DailyMission.objects.filter(user=user, date=daily_report.date).exists():
        generate_daily_missions(user, daily_report.date)

    # ミッション評価
    return evaluate_missions(user, daily_report)
