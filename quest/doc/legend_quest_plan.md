# 伝説クエスト（Legend Quest）実装設計書

作成日: 2026-04-16

## 概要

現在のゲームはレベル100（ランチクイーン）が上限で、到達後は新たな目標がなく飽きが生じる。
16人以上のスタッフが参加しており、Lv100達成後も毎日ゲームを楽しめる長期目標チャレンジシステムを追加する。
管理者操作なしで完全自動動作することが条件。

---

## 追加する機能の全体像

- **伝説チャレンジ（6種）**: Lv100到達で自動解放される長期目標
- **月間ランキング**: 毎月のXP獲得量をスタッフ間で競争
- **専用ページ**: `/quest/legend/` でチャレンジ進捗・月間ランキング確認
- **演出拡張**: チャレンジ達成時も celebration ページで演出

---

## チャレンジ種類と報酬

| code | 種別 | 条件 | XP | 称号 |
|------|------|------|----|------|
| legend_streak_30 | streak | 30日連続ストリーク | 1,000 | 鋼の意志 |
| legend_perfect_7 | consecutive_perfect | 7日連続パーフェクトデイ | 800 | なし（バッジのみ） |
| legend_zero_waste_20 | monthly_zero_waste | 月間廃棄ゼロ20日以上 | 700 | なし |
| legend_sales_1000 | cumulative_sales | 累計1000食販売（繰り返し可） | 1,500 | 千食の豪傑 |
| legend_soldout_10 | consecutive_soldout | 完売ミッション連続10日 | 900 | 完売の女神 |
| legend_monthly_rank | monthly_rank | 月間XPトップ3 | 600 | なし |

---

## 実装ステップ

### Step 1: モデル追加 (`quest/models.py`)

**新規モデル3つ:**

```python
class LegendChallenge(models.Model):
    # フィールド: code, name, description, challenge_type (6種), target_value,
    #            xp_reward, title_reward (blank可), icon, sort_order, is_active
    CHALLENGE_TYPES = [
        ('streak',               '継続ストリーク'),
        ('consecutive_perfect',  '連続パーフェクトデイ'),
        ('monthly_zero_waste',   '月間廃棄ゼロ日数'),
        ('cumulative_sales',     '累計販売数'),
        ('consecutive_soldout',  '連続完売日数'),
        ('monthly_rank',         '月間XPランキング'),
    ]

class UserLegendProgress(models.Model):
    # フィールド: user, challenge (FK), completion_count=0,
    #            current_progress=0, last_completed_at=null, title_awarded=False
    # unique_together: (user, challenge)

class MonthlyXPSnapshot(models.Model):
    # フィールド: user, year, month, xp_gained, rank (null), evaluated=False
    # unique_together: (user, year, month)
```

**既存モデル変更2つ:**

- `QuestProfile` に `legend_title = CharField(blank=True)` 追加
  - `award_xp()` が毎回 `title` を 'ランチクイーン' に上書きするため、伝説称号は別フィールドで保持
  - テンプレートで `legend_title or title` の優先度で表示する

- `CelebrationQueue` に `legend_completions_json = JSONField(default=list)` 追加
  - 構造: `[{name, xp_reward, icon, title_reward, completion_count}, ...]`

### Step 2: マイグレーション

```
quest/migrations/0005_add_legend_models.py   ← makemigrations で自動生成
quest/migrations/0006_seed_legend_challenges.py  ← RunPython で初期データ投入
```

`0006` には `reverse_code`（`LegendChallenge.objects.all().delete()`）も実装してロールバック可に。

初期データ（`0006` の `RunPython` 内容）:
```python
challenges = [
    {'code': 'legend_streak_30',    'challenge_type': 'streak',              'target_value': 30,   'xp_reward': 1000, 'title_reward': '鋼の意志',    'sort_order': 1},
    {'code': 'legend_perfect_7',    'challenge_type': 'consecutive_perfect', 'target_value': 7,    'xp_reward': 800,  'title_reward': '',            'sort_order': 2},
    {'code': 'legend_zero_waste_20','challenge_type': 'monthly_zero_waste',  'target_value': 20,   'xp_reward': 700,  'title_reward': '',            'sort_order': 3},
    {'code': 'legend_sales_1000',   'challenge_type': 'cumulative_sales',    'target_value': 1000, 'xp_reward': 1500, 'title_reward': '千食の豪傑',  'sort_order': 4},
    {'code': 'legend_soldout_10',   'challenge_type': 'consecutive_soldout', 'target_value': 10,   'xp_reward': 900,  'title_reward': '完売の女神',  'sort_order': 5},
    {'code': 'legend_monthly_rank', 'challenge_type': 'monthly_rank',        'target_value': 3,    'xp_reward': 600,  'title_reward': '',            'sort_order': 6},
]
```

### Step 3: サービス層 (`quest/services.py`)

**新規インポート:** `LegendChallenge`, `UserLegendProgress`, `MonthlyXPSnapshot`

**追加する関数（依存順）:**

```
_count_consecutive_soldout(user, as_of_date) -> int
    直近から遡り sold_out ミッション completed の連続日数を返す

_get_consecutive_perfect_days(user, as_of_date) -> int
    直近から遡り「その日の全ミッション completed」の連続日数を返す

_get_monthly_zero_waste_count(user, year, month) -> int
    指定月の zero_waste ミッション completed 日数を返す

_get_cumulative_sales_qty(user) -> int
    sales_count ミッション completed の actual_value 合計を返す

_get_monthly_xp_gained(user, year, month) -> int
    指定月に completed になった DailyMission の xp_reward 合計
    + UserAchievement.earned_at が同月のものの xp_reward 合計
    ※ DailyMission.date フィールドを基準に集計（timezone問題を回避）

evaluate_monthly_ranking(year, month) -> list[MonthlyXPSnapshot]
    全参加ユーザーの _get_monthly_xp_gained を集計
    MonthlyXPSnapshot を upsert し rank を付与（同率は同じランク）
    rank<=3 のユーザーに complete_legend_challenge('legend_monthly_rank') を呼ぶ
    evaluated=True で確定保存

_maybe_evaluate_previous_month(today: date) -> None
    今日の前月分スナップショットが evaluated=True でなければ evaluate_monthly_ranking を呼ぶ
    ※ generate_daily_missions() 冒頭から呼ぶ

complete_legend_challenge(user, challenge_code) -> dict or None
    チャレンジを達成としてマーク
    - UserLegendProgress.completion_count += 1
    - award_xp(profile, challenge.xp_reward) を呼ぶ
    - title_reward があり title_awarded=False なら profile.legend_title = title_reward
    戻り値: {challenge, completion_count, title_awarded (bool)}

check_legend_challenges(user, date) -> list[dict]
    profile.level < 100 なら即 return []
    各チャレンジ種別を判定:
      streak:               profile.current_streak >= 30
                            かつ (未達成 OR last_completed_at から30日以上経過)
      consecutive_perfect:  _get_consecutive_perfect_days >= 7
                            かつ前回達成日以降に新たな7連続ブロックが完成
      monthly_zero_waste:   _get_monthly_zero_waste_count(当月) >= 20
                            かつ当月未達成
      cumulative_sales:     _get_cumulative_sales_qty >= 1000 * (completion_count+1)
      consecutive_soldout:  _count_consecutive_soldout >= 10
                            かつ前回達成日以降に新たな10連続が完成
      monthly_rank:         evaluate_monthly_ranking 側で処理するためスキップ
    達成条件を満たすものを complete_legend_challenge して結果リストで返す
```

**既存関数への変更（最小限）:**

- `generate_daily_missions()` 冒頭に `_maybe_evaluate_previous_month(mission_date)` を追加

- `evaluate_missions()` 末尾の return 直前に:
  ```python
  legend_completions = []
  if profile.level == 100:
      legend_completions = check_legend_challenges(user, date)
  result['legend_completions'] = legend_completions
  ```

### Step 4: シグナル更新 (`quest/signals.py`)

`evaluate_missions_for_report()` の `CelebrationQueue.objects.create()` に `legend_completions_json` を追加:
```python
legend_completions_json=[
    {
        'name': lc['challenge'].name,
        'xp_reward': lc['challenge'].xp_reward,
        'icon': lc['challenge'].icon,
        'title_reward': lc['challenge'].title_reward if lc['title_awarded'] else '',
        'completion_count': lc['completion_count'],
    }
    for lc in result.get('legend_completions', [])
],
```

キュー作成トリガー条件を更新:
```python
# legend_completions がある場合も演出ページへ送る
if not result['completed_missions'] and not result['new_achievements'] \
        and not result.get('legend_completions'):
    return
```

### Step 5: ビュー追加・更新 (`quest/views.py`)

**新規ビュー: `legend_quest_view(request)`**
- `@login_required`
- `profile.level < 100` なら dashboard にリダイレクト（アクセス制御）
- 各チャレンジの `current_progress` をリアルタイム計算してコンテキストに渡す
- 当月・前月の `MonthlyXPSnapshot` を取得してランキング表示

コンテキスト構造:
```python
context = {
    'profile': profile,
    'challenges': [
        {
            'challenge': LegendChallenge,
            'progress': UserLegendProgress or None,
            'current_value': int,
            'progress_pct': int,  # 0-100
        }
    ],
    'monthly_ranking': list[MonthlyXPSnapshot],   # 当月（rank順）
    'prev_month_ranking': list[MonthlyXPSnapshot], # 前月（rank順）
}
```

**`quest_dashboard()` 更新:**
```python
legend_data = None
if profile.level == 100:
    legend_data = {
        'total': LegendChallenge.objects.filter(is_active=True).count(),
        'completed_count': UserLegendProgress.objects.filter(
            user=request.user, completion_count__gt=0
        ).count(),
    }
context['legend_data'] = legend_data
```

**`quest_celebration()` 更新:**
- `queue_entry.legend_completions_json` をコンテキストに追加

### Step 6: URL追加 (`quest/urls.py`)

```python
path('legend/', views.legend_quest_view, name='legend'),
```

### Step 7: Admin登録 (`quest/admin.py`)

```python
@admin.register(LegendChallenge)
class LegendChallengeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'challenge_type', 'target_value', 'xp_reward', 'title_reward', 'sort_order', 'is_active')
    list_filter  = ('challenge_type', 'is_active')

@admin.register(UserLegendProgress)
class UserLegendProgressAdmin(admin.ModelAdmin):
    list_display  = ('user', 'challenge', 'completion_count', 'current_progress', 'last_completed_at', 'title_awarded')
    list_filter   = ('challenge', 'title_awarded')
    search_fields = ('user__username',)

@admin.register(MonthlyXPSnapshot)
class MonthlyXPSnapshotAdmin(admin.ModelAdmin):
    list_display = ('user', 'year', 'month', 'xp_gained', 'rank', 'evaluated')
    list_filter  = ('year', 'month', 'evaluated')
```

### Step 8: テンプレート

**新規: `templates/quest/legend.html`**
- `{% extends 'quest/base.html' %}` 継承
- セクション1: チャレンジカード一覧（進捗バー付き、達成回数・報酬表示）
- セクション2: 月間ランキングボード（当月・前月、トップ3ハイライト）
- スタイル: 既存の `dq-dialog` スタイルを踏襲

**更新: `templates/quest/dashboard.html`**
- Lv100 かつ `legend_data` がある場合のみ「伝説クエスト」セクションを表示（`quest:legend` へのリンク付き）

**更新: `templates/quest/celebration.html`**
- `new_achievements` ループの後に `legend_completions` ループを追加:
  ```html
  {% for lc in legend_completions %}
  <div class="mission-item">
    <div class="mission-check" style="background: linear-gradient(#9040ff, #c080ff);">★</div>
    <span>伝説達成：{{ lc.name }}</span>
    {% if lc.title_reward %}
    <span>称号「{{ lc.title_reward }}」獲得！</span>
    {% endif %}
    <span class="mission-xp-badge">+{{ lc.xp_reward }}XP</span>
  </div>
  {% endfor %}
  ```

**更新: `templates/quest/base.html`**
- ドロワーナビに条件付きリンク追加:
  ```html
  {% if request.user.quest_profile.level == 100 %}
  <a href="{% url 'quest:legend' %}">★ 伝説クエスト</a>
  {% endif %}
  ```

---

## 重要な設計判断

### 称号の衝突回避
`award_xp()` (services.py:241) は毎回 `profile.title = level_info[2]` で上書きする。
Lv100 では常に 'ランチクイーン' に戻るため、**伝説称号は `QuestProfile.legend_title` に保存し、テンプレートで優先表示** する方針を採用。
`award_xp()` 自体には変更不要。

テンプレートの表示ロジック:
```html
{{ profile.legend_title|default:profile.title }}
```

### 月間ランキングの自動評価タイミング
`generate_daily_missions()` は参加ユーザーが販売報告を提出するたびに呼ばれる。
月が変わった後の最初の実行時（月初の最初の報告時）に `_maybe_evaluate_previous_month()` が前月評価を1度だけ走らせる。
`MonthlyXPSnapshot.evaluated=True` フラグで二重評価を防止。

### 月間XP算出の基準
`DailyMission.date` フィールドを基準に集計する（`completed_at` の timezone問題を回避）:
```python
DailyMission.objects.filter(
    user=user, status='completed',
    date__year=year, date__month=month
).aggregate(total=Sum('xp_reward'))
```

---

## 検証方法

1. **スキーマ・初期データ確認:**
   ```bash
   python manage.py makemigrations quest
   python manage.py migrate
   python manage.py shell -c "from quest.models import LegendChallenge; print(LegendChallenge.objects.count())"  # 6件
   ```

2. **Lv100ユーザーでのチャレンジ評価:**
   ```python
   # シェルで確認
   from quest.services import check_legend_challenges
   from django.contrib.auth import get_user_model
   import datetime
   user = get_user_model().objects.get(username='...')
   user.quest_profile.level = 100
   user.quest_profile.save()
   results = check_legend_challenges(user, datetime.date.today())
   print(results)
   ```

3. **月間ランキング評価:**
   ```python
   from quest.services import evaluate_monthly_ranking
   snapshots = evaluate_monthly_ranking(2026, 3)  # 前月分
   for s in snapshots:
       print(s.user, s.rank, s.xp_gained)
   ```

4. **称号テスト:**
   ```python
   from quest.services import complete_legend_challenge, award_xp
   result = complete_legend_challenge(user, 'legend_streak_30')
   assert user.quest_profile.legend_title == '鋼の意志'
   # award_xp を呼んでも legend_title が消えないことを確認
   award_xp(user.quest_profile, 100)
   user.quest_profile.refresh_from_db()
   assert user.quest_profile.legend_title == '鋼の意志'  # OK
   ```

---

## 変更対象ファイル一覧

| ファイル | 変更種別 |
|---------|---------|
| `quest/models.py` | モデル追加・既存モデル変更 |
| `quest/migrations/0005_add_legend_models.py` | 新規（`makemigrations` で自動生成） |
| `quest/migrations/0006_seed_legend_challenges.py` | 新規（手動作成・`RunPython`） |
| `quest/services.py` | 関数追加・既存関数修正（最小限） |
| `quest/signals.py` | 既存関数修正 |
| `quest/views.py` | ビュー追加・既存ビュー修正 |
| `quest/urls.py` | URL 1件追加 |
| `quest/admin.py` | Admin 3モデル登録追加 |
| `quest/templates/quest/legend.html` | 新規テンプレート |
| `quest/templates/quest/dashboard.html` | 伝説クエスト導線追加 |
| `quest/templates/quest/celebration.html` | 伝説チャレンジ演出追加 |
| `quest/templates/quest/base.html` | ナビリンク追加 |
