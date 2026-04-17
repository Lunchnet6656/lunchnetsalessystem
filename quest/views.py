from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect

from .models import DailyMission, Achievement, UserAchievement, CelebrationQueue
from .services import (
    get_or_create_profile,
    generate_daily_missions,
    get_xp_progress,
    get_next_level_info,
    get_xp_for_next_level,
    get_title_by_level,
    get_title_description,
    LEVEL_TABLE,
)


def _milestone_levels():
    """称号が切り替わるマイルストーンレベルのみ返す"""
    seen_titles = set()
    result = []
    for entry in LEVEL_TABLE:
        if entry[2] not in seen_titles:
            seen_titles.add(entry[2])
            result.append(entry)
    return result


@login_required
def quest_start(request):
    """クエスト参加確認ページ（未参加者向け）"""
    profile = get_or_create_profile(request.user)
    if profile.is_participating:
        return redirect('quest:dashboard')
    return render(request, 'quest/start.html', {'profile': profile})


@login_required
def quest_join(request):
    """クエスト参加処理"""
    if request.method == 'POST':
        profile = get_or_create_profile(request.user)
        profile.is_participating = True
        profile.save(update_fields=['is_participating'])
        return redirect('quest:dashboard')
    return redirect('quest:start')


@login_required
def quest_dashboard(request):
    """メインクエストダッシュボード"""
    profile = get_or_create_profile(request.user)
    if not profile.is_participating:
        return redirect('quest:start')
    today = date.today()

    # 今日のミッションがなければ生成
    missions = DailyMission.objects.filter(user=request.user, date=today)
    if not missions.exists():
        generate_daily_missions(request.user, today)
        missions = DailyMission.objects.filter(user=request.user, date=today)

    # 最近の実績
    recent_achievements = UserAchievement.objects.filter(
        user=request.user
    ).select_related('achievement').order_by('-earned_at')[:5]

    # XP進捗
    xp_progress = get_xp_progress(profile.total_xp)
    next_level = get_next_level_info(profile.level)
    xp_remaining = get_xp_for_next_level(profile.total_xp)

    # レベルアップ通知（セッションから取得）
    level_up_data = request.session.pop('quest_level_up', None)
    new_achievement_ids = request.session.pop('quest_new_achievements', [])
    new_achievements = []
    if new_achievement_ids:
        new_achievements = Achievement.objects.filter(id__in=new_achievement_ids)

    # ミッション達成数
    completed_today = missions.filter(status='completed').count()
    total_today = missions.count()

    context = {
        'profile': profile,
        'missions': missions,
        'recent_achievements': recent_achievements,
        'xp_progress': xp_progress,
        'next_level': next_level,
        'xp_remaining': xp_remaining,
        'level_up_data': level_up_data,
        'new_achievements': new_achievements,
        'completed_today': completed_today,
        'total_today': total_today,
        'today': today,
    }
    return render(request, 'quest/dashboard.html', context)


@login_required
def mission_history(request):
    """ミッション履歴"""
    # 月指定パラメータ
    year = request.GET.get('year')
    month = request.GET.get('month')

    today = date.today()
    if year and month:
        try:
            year = int(year)
            month = int(month)
        except (ValueError, TypeError):
            year = today.year
            month = today.month
    else:
        year = today.year
        month = today.month

    # 当月のミッション
    missions = DailyMission.objects.filter(
        user=request.user,
        date__year=year,
        date__month=month,
    ).select_related('template').order_by('-date')

    # 日付ごとにグループ化
    missions_by_date = {}
    for mission in missions:
        if mission.date not in missions_by_date:
            missions_by_date[mission.date] = []
        missions_by_date[mission.date].append(mission)

    # 月ナビゲーション
    current_month = date(year, month, 1)
    if month == 1:
        prev_month = date(year - 1, 12, 1)
    else:
        prev_month = date(year, month - 1, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)

    # 月の集計
    total_missions = missions.count()
    completed_missions = missions.filter(status='completed').count()
    completion_rate = int(completed_missions / total_missions * 100) if total_missions > 0 else 0

    context = {
        'missions_by_date': dict(sorted(missions_by_date.items(), reverse=True)),
        'current_month': current_month,
        'prev_month': prev_month,
        'next_month': next_month,
        'total_missions': total_missions,
        'completed_missions': completed_missions,
        'completion_rate': completion_rate,
    }
    return render(request, 'quest/missions.html', context)


@login_required
def achievement_gallery(request):
    """実績バッジ一覧"""
    all_achievements = Achievement.objects.all()
    earned_ids = set(
        UserAchievement.objects.filter(user=request.user).values_list('achievement_id', flat=True)
    )
    earned_achievements = UserAchievement.objects.filter(
        user=request.user
    ).select_related('achievement')
    earned_map = {ua.achievement_id: ua.earned_at for ua in earned_achievements}

    achievements = []
    for a in all_achievements:
        achievements.append({
            'achievement': a,
            'earned': a.id in earned_ids,
            'earned_at': earned_map.get(a.id),
        })

    # カテゴリ別にグループ化
    categories = {
        'streak': {'label': '連続達成', 'items': []},
        'cumulative': {'label': '累計達成', 'items': []},
        'special': {'label': '特別', 'items': []},
    }
    for item in achievements:
        cat = item['achievement'].category
        if cat in categories:
            categories[cat]['items'].append(item)

    total = len(achievements)
    earned_count = len(earned_ids)

    context = {
        'categories': categories,
        'total': total,
        'earned_count': earned_count,
    }
    return render(request, 'quest/achievements.html', context)


@login_required
def quest_profile(request):
    """プレイヤーステータス詳細"""
    profile = get_or_create_profile(request.user)
    xp_progress = get_xp_progress(profile.total_xp)
    next_level = get_next_level_info(profile.level)
    xp_remaining = get_xp_for_next_level(profile.total_xp)

    total_completed = DailyMission.objects.filter(
        user=request.user, status='completed'
    ).count()
    total_missions = DailyMission.objects.filter(user=request.user).count()
    total_achievements = UserAchievement.objects.filter(user=request.user).count()
    total_possible_achievements = Achievement.objects.count()

    # 直近7日のミッション達成率
    seven_days_ago = date.today() - timedelta(days=7)
    recent_missions = DailyMission.objects.filter(
        user=request.user, date__gte=seven_days_ago
    )
    recent_completed = recent_missions.filter(status='completed').count()
    recent_total = recent_missions.count()
    recent_rate = int(recent_completed / recent_total * 100) if recent_total > 0 else 0

    context = {
        'profile': profile,
        'xp_progress': xp_progress,
        'next_level': next_level,
        'xp_remaining': xp_remaining,
        'total_completed': total_completed,
        'total_missions': total_missions,
        'total_achievements': total_achievements,
        'total_possible_achievements': total_possible_achievements,
        'recent_rate': recent_rate,
        'level_table': _milestone_levels(),
    }
    return render(request, 'quest/profile.html', context)


@login_required
def quest_celebration(request):
    """ミッション達成・レベルアップ演出ページ

    CelebrationQueue から未読の最新1件を取り出して表示する。
    キューが空の場合はダッシュボードへリダイレクト。
    """
    profile = get_or_create_profile(request.user)

    # 未読の演出キューを古い順に取得（複数ある場合は最初の1件）
    queue_entry = CelebrationQueue.objects.filter(
        user=request.user,
        is_read=False,
    ).order_by('created_at').first()

    if queue_entry is None:
        return redirect('quest:dashboard')

    # 既読にする
    queue_entry.is_read = True
    queue_entry.save(update_fields=['is_read'])

    context = {
        'profile': profile,
        'prev_level': queue_entry.prev_level,
        'new_level': queue_entry.new_level,
        'leveled_up': queue_entry.leveled_up,
        'prev_title': queue_entry.prev_title,
        'new_title': queue_entry.new_title,
        'title_changed': queue_entry.title_changed,
        'new_character_image': queue_entry.new_character_image,
        'xp_gained': queue_entry.xp_gained,
        'completed_missions': queue_entry.completed_missions_json,
        'new_achievements': queue_entry.new_achievements_json,
    }
    return render(request, 'quest/celebration.html', context)


@login_required
def title_detail(request, level):
    """称号詳細ページ"""
    entry = get_title_by_level(level)
    if entry is None:
        return redirect('quest:profile')

    profile = get_or_create_profile(request.user)
    # 自分より上の称号は閲覧不可
    if level > profile.level:
        return redirect('quest:profile')

    description = get_title_description(level)
    context = {
        'profile': profile,
        'title_level': entry[0],
        'title_name': entry[2],
        'title_image': entry[3],
        'title_description': description,
        'required_xp': entry[1],
    }
    return render(request, 'quest/title_detail.html', context)


@login_required
def quest_api_status(request):
    """JSON APIエンドポイント"""
    profile = get_or_create_profile(request.user)
    today = date.today()
    missions = DailyMission.objects.filter(user=request.user, date=today)
    completed = missions.filter(status='completed').count()

    return JsonResponse({
        'level': profile.level,
        'title': profile.title,
        'xp': profile.xp,
        'total_xp': profile.total_xp,
        'character_image': profile.character_image,
        'current_streak': profile.current_streak,
        'xp_progress': get_xp_progress(profile.total_xp),
        'missions_completed': completed,
        'missions_total': missions.count(),
    })
