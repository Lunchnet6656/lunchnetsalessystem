import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from sales.models import DailyReport
from .services import evaluate_missions_for_report, _find_user_by_person_in_charge

logger = logging.getLogger(__name__)


@receiver(post_save, sender=DailyReport)
def on_daily_report_save(sender, instance, **kwargs):
    """DailyReport保存時にクエストミッションを評価し、演出キューに保存"""
    result = evaluate_missions_for_report(instance)

    # 演出が不要な場合（未参加、ミッションなし等）はスキップ
    if result is None:
        return

    # ミッション達成がない場合もスキップ（演出ページに飛ばす意味がない）
    if not result['completed_missions'] and not result['new_achievements']:
        return

    # ユーザーを特定
    user = _find_user_by_person_in_charge(instance.person_in_charge)
    if user is None:
        return

    # 演出キューに保存（未読のものがあれば上書きせず追加）
    from .models import CelebrationQueue

    completed_missions_data = [
        {
            'title': m.title,
            'xp_reward': int(m.xp_reward),
        }
        for m in result['completed_missions']
    ]

    new_achievements_data = [
        {
            'name': ua.achievement.name,
            'xp_reward': ua.achievement.xp_reward,
            'icon': ua.achievement.icon,
        }
        for ua in result['new_achievements']
    ]

    # キャラクター画像を取得
    from .services import get_or_create_profile
    profile = get_or_create_profile(user)

    CelebrationQueue.objects.create(
        user=user,
        prev_level=result['prev_level'],
        new_level=result['new_level'],
        leveled_up=result['leveled_up'],
        prev_title=result['prev_title'],
        new_title=result['new_title'],
        title_changed=result['title_changed'],
        new_character_image=profile.character_image,
        xp_gained=result['xp_gained'],
        completed_missions_json=completed_missions_data,
        new_achievements_json=new_achievements_data,
        is_read=False,
    )
