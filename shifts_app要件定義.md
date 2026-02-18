# ✅ 目的
既存Djangoプロジェクト（lunchnetsale）に、2週間単位のシフト希望提出とステータス確認、管理者による売り場割当を増設する。既存のsales日報機能は壊さない。

# ✅ 重要前提（必ず守る）
- 既存プロジェクトは settings.py に AUTH_USER_MODEL が未設定のため、現状の稼働は標準の auth.User の可能性が高い。
- ただし将来変更可能性を考慮し、ユーザー参照は必ず `get_user_model()` と `settings.AUTH_USER_MODEL` を用いる（直接 `from django.contrib.auth.models import User` をFKに使わない）。
- 新機能は新Django app `shifts` として分離する。salesアプリに大量追加しない。
- Tailwindは shiftsアプリのテンプレだけで使用する（shifts専用base.htmlにCDNで読み込み）。既存sales/templatesには影響させない。
- 既存の sales.models.ShiftRequest / Holiday は試作であり今回は使用しない。

# ✅ 既存モデル（把握済み）
- sales.models.SalesLocation が売り場マスター
- sales.models.DailyReport は日報で稼働中

# ✅ 追加要件
## A) シフト提出（2週間）
- 各ユーザーが2週間分の各日を「出勤 / 休み」で選択し提出する。
- 休みの場合は4カテゴリ（NORMAL/SPECIAL/SUBSTITUTE/CEREMONY）を選択する。
- SUBSTITUTE の場合は代替者を指定する。
- 各自が自分の提出状況（未提出/提出済/差し戻し等）を確認できる。

## B) ドライバー制約
- 「ドライバー」は販売員の一部（別職種ではない）。
- 売り場には requires_drive があり、requires_drive=True の売り場は「運転可能な人」しか割り当てできない。
- 運転可能フラグはユーザー側に持つ（UserProfile.can_drive）。

## C) 休日判定（重要）
- 土日祝は休日（休み判定）。
- 会社カレンダーで連休（特別休業日）も休日判定。
- 休日は提出UIで「休みがデフォルト」、原則出勤に変更できない（disabled）。
- 休日は管理割当対象から除外する（休日の日に割当画面を開くと「休日のため割当不要」表示）。

## D) 募集開始/締切（重要）＋ B方式（締切後は管理者のみ）
- 2週間の募集期間（SchedulePeriod）は「募集開始日時」と「募集締切日時」を持つ。
- 本人が提出できるのは募集期間中のみ。
- 締切後の変更/提出は管理者のみ可能（代理入力）。
- 代理入力したかどうかが記録される（submitted_by_admin / is_late_submission）。

# ✅ 権限（管理者1アカウント）
- 管理者判定は以下のいずれかで実装（どちらでもOK、既存運用に合わせる）
  - user.is_staff == True または user.is_superuser == True を利用（推奨）
  - もしくは sales.CustomUser.role を参照する場合は、「AUTH_USER_MODEL未設定」前提なので使わない
- 管理者画面 (/shifts/admin/...) は必ずログイン＋管理者権限を要求するデコレータ/ミックスインで保護する。

# ✅ 実装方針（ユーザー拡張の安全策）
- AUTH_USER_MODEL未設定でも確実に動くように、ユーザー拡張は shifts アプリ側で UserProfile を作る。
- Profile は OneToOne で get_user_model() に紐づける。
- Profile 自動生成は signals を使用（User作成時）。既存ユーザー分は、管理コマンドまたはadminから作成できるようにする（最小は signals + “未作成ならget_or_create”でも可）。

---

# ✅ 実装詳細：モデル

## 1) sales.models.SalesLocation を拡張（最小）
追加フィールド：
- requires_drive = models.BooleanField(default=False)
- priority = models.CharField(max_length=1, choices=[("S","S"),("A","A"),("B","B")], default="A")

注意：
- 既存データがあるため default を必ず付ける。
- 既存画面に影響を与えない（表示変更しない）。

## 2) shifts.models.UserProfile（運転可否・勤務パターン）
User = get_user_model()

class UserProfile(models.Model):
- user = OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
- can_drive = BooleanField(default=False)
- work_pattern = CharField(
    max_length=10,
    choices=[("DAILY","DAILY"),("FIXED","FIXED"),("FLEX","FLEX"),("HELPER","HELPER")],
    default="FLEX"
  )
- uses_app = BooleanField(default=True)
- min_shifts_per_week = IntegerField(default=0)
- max_shifts_per_week = IntegerField(default=0)
- fixed_weekdays = CharField(max_length=20, blank=True, default="")  # "MON,TUE,THU"
- created_at / updated_at

実装メモ：
- UserProfile は user.profile で参照できるようにする
- 未作成の場合に備え views では get_or_create を使ってもOK（事故防止）

## 3) shifts.models.CompanyHoliday（会社カレンダー）
class CompanyHoliday(models.Model):
- date = DateField(unique=True)
- name = CharField(max_length=100, blank=True, default="")
- created_at / updated_at

## 4) shifts.models.SchedulePeriod（2週間）
class SchedulePeriod(models.Model):
- start_date = DateField()
- end_date = DateField()
- submission_open_at = DateTimeField()
- submission_close_at = DateTimeField()
- status = CharField(
    max_length=20,
    choices=[("OPEN","OPEN"),("REVIEW","REVIEW"),("FIXED","FIXED"),("PUBLISHED","PUBLISHED")],
    default="OPEN"
  )
- created_at / updated_at

制約：
- start_date はユニーク推奨（unique=True）
- end_date は start_date + 13 days を推奨（フォーム/管理画面側で補助してよい）

ロジック：
- now < submission_open_at → 募集前（本人提出不可）
- submission_open_at <= now <= submission_close_at → 募集中（本人提出可）
- now > submission_close_at → 締切後（本人不可、管理者のみ）

## 5) shifts.models.AvailabilitySubmission（ユーザー×期間）
class AvailabilitySubmission(models.Model):
- user = FK to User
- period = FK to SchedulePeriod (related_name="submissions")
- status = CharField(choices=[("DRAFT","DRAFT"),("SUBMITTED","SUBMITTED"),("RETURNED","RETURNED")], default="DRAFT")
- submitted_at = DateTimeField(null=True, blank=True)
- admin_note = TextField(blank=True, default="")
- submitted_by_admin = BooleanField(default=False)
- is_late_submission = BooleanField(default=False)
- created_at / updated_at

制約：
- unique_together = ("user", "period")

意味：
- 本人提出時：submitted_by_admin=False
- 締切後の管理者代理入力：submitted_by_admin=True かつ is_late_submission=True

## 6) shifts.models.AvailabilityDay（各日）
class AvailabilityDay(models.Model):
- submission = FK to AvailabilitySubmission (related_name="days")
- date = DateField()
- availability = CharField(choices=[("WORK","WORK"),("OFF","OFF")], default="WORK")
- absence_category = CharField(
    max_length=20,
    choices=[("NORMAL","NORMAL"),("SPECIAL","SPECIAL"),("SUBSTITUTE","SUBSTITUTE"),("CEREMONY","CEREMONY")],
    blank=True,
    default=""
  )
- substitute_user = FK to User (null=True, blank=True, on_delete=SET_NULL, related_name="substitute_days")
- comment = TextField(blank=True, default="")

制約：
- unique_together = ("submission", "date")

バリデーション（必須）：
- 休日（is_non_working_day(date)=True）の場合：
  - availability は OFF 固定
  - absence_category は空でもOK
  - substitute_user は空
- 非休日の場合：
  - availability=WORK → absence_category は空、substitute_user は空
  - availability=OFF → absence_category 必須
  - absence_category=SUBSTITUTE → substitute_user 必須

実装場所：
- Model.clean() で実装し、フォーム保存前に full_clean() が走るようにする（forms.pyでも可）。

## 7) shifts.models.ShiftAssignment（確定割当）
class ShiftAssignment(models.Model):
- date = DateField()
- sales_location = FK to sales.SalesLocation
- user = FK to User
- status = CharField(choices=[("DRAFT","DRAFT"),("CONFIRMED","CONFIRMED")], default="DRAFT")
- admin_note = TextField(blank=True, default="")
- created_at / updated_at

制約：
- unique_together = ("date", "sales_location")

バリデーション（必須）：
- is_non_working_day(date)=True → 作成/更新禁止
- sales_location.requires_drive=True → user.profile.can_drive=True 必須（profileが無ければ作成して判定）
- 候補者表示時にも絞り込み（フォーム側）を行い、保存時にも二重でチェックする

---

# ✅ 祝日判定ライブラリ
- requirements.txt に jpholiday を追加（未導入なら）
- shifts/utils/calendar.py を作成
  - is_weekend(d)
  - is_jp_holiday(d) using jpholiday
  - is_company_holiday(d) query CompanyHoliday
  - is_non_working_day(d) = weekend or jp_holiday or company_holiday
  - reason(d) = "WEEKEND"/"HOLIDAY"/"COMPANY"/""（UI表示用）

---

# ✅ 画面（URL）と画面挙動（詳細）

## shifts/urls.py を作成し、lunchnetsale/urls.py に include する
例：path("shifts/", include("shifts.urls"))

## 共通（ログイン必須）
- 全画面 login_required
- 管理者画面は admin_required（自作デコレータ or UserPassesTestMixin）

---

## 1) 本人：2週間提出
GET/POST /shifts/period/current/submit/

画面要件：
- 現在提出対象の period を以下優先で取得
  1) status="OPEN" の最新
  2) 無ければ「提出対象期間がありません」表示
- 提出可能判定：
  - now が submission_open_at～submission_close_at の間 → 本人提出可
  - それ以外 → 本人提出不可（説明文表示、フォームは無効/非表示）

UI要件（スマホ最適）：
- 2週間分を縦リスト表示（例：02/16(月) など）
- 各日：WORK/OFF の切替（radio or select）
- 休日は OFF 固定（入力disabled）＋「休日」バッジ表示
- OFF の場合のみカテゴリselectを表示（ただし休日OFFはカテゴリ不要なのでカテゴリUIは出さない）
- SUBSTITUTE選択時のみ substitute_user select を表示
- 送信ボタン：提出可能時のみ表示

保存仕様：
- AvailabilitySubmission を user+period で get_or_create
- status=SUBMITTED、submitted_at=now
- submitted_by_admin=False
- is_late_submission=False
- AvailabilityDay は period範囲の日付分を upsert（全日分を保存）
- 休日分は availability=OFF, absence_category="" を保存

---

## 2) 本人：自分の提出状況
GET /shifts/mine/

表示仕様：
- 直近の period を最大3件程度表示（OPEN/REVIEW/FIXED/PUBLISHED）
- 各 period に対して submission.status, submitted_at を表示
- RETURNED の場合 admin_note を目立つ形で表示
- 現在期の AvailabilityDay 一覧を表示（date, availability, category, substitute_user, comment）

---

## 3) 本人：確定シフト閲覧
GET /shifts/schedule/current/

表示仕様：
- 対象 period を以下優先で取得
  1) status="PUBLISHED"
  2) 無ければ status="FIXED"
  3) 無ければ「確定シフトはまだありません」
- period 範囲内の ShiftAssignment を date順に表示
- 自分の割当は強調表示
- 売り場が requires_drive の場合はアイコンやラベル表示

---

## 4) 管理者：会社カレンダー管理
GET/POST /shifts/admin/company-holidays/

要件：
- CompanyHoliday の一覧表示
- 追加フォーム（日付 + 名称）
- 削除ボタン（POST）
- 追加済み日付の重複は弾く（unique）

---

## 5) 管理者：募集期間管理（追加）
GET/POST /shifts/admin/periods/

要件：
- SchedulePeriod の一覧（新規作成/編集）
- 新規作成フォーム：
  - start_date（必須）
  - end_date（任意、未入力なら start_date+13 で自動補完）
  - submission_open_at（必須）
  - submission_close_at（必須）
  - status（初期 OPEN 推奨）
- バリデーション：
  - open_at < close_at
  - start_date <= end_date
- 既存期を編集可能（運用上必要）

---

## 6) 管理者：ダッシュボード（運転不足警告）
GET /shifts/admin/dashboard/

対象 period：
- OPEN/REVIEW/FIXED/PUBLISHED のうち「最新のOPENを優先」、無ければ最新のFIXED/PUBLISHED

日別表示（period範囲の各日）：
- 休日判定（休日なら明示）
- 運転必須売り場数：SalesLocation.objects.filter(requires_drive=True).count()
  ※MVPでは「全売り場営業前提」なので日別に同数でOK
- 出勤可能人数：AvailabilityDay(該当date, availability=WORK) の人数
- 運転可能人数：上記のうち profile.can_drive=True の人数
- 運転不足判定：
  - driver_available < driver_required → 赤表示
- 割当済み数：ShiftAssignment(該当date) の件数
- 「割当へ」リンク：/shifts/admin/assign/<date>/

---

## 7) 管理者：日別割当
GET/POST /shifts/admin/assign/<date>/

GET表示：
- date が休日なら「休日のため割当不要」表示して終了（割当フォーム非表示）
- 売り場一覧：SalesLocation を priority順に表示（S→A→B）
- 各売り場ごとに担当者selectを表示
  - 候補者は、当日の AvailabilityDay が WORK のユーザー（提出がない場合は候補から外す or 全員出すは選べる）
  - requires_drive=True の売り場は候補者を profile.can_drive=True に絞る
- 既に割当がある場合は初期選択値として表示

POST保存：
- 売り場ごとに user を受け取り ShiftAssignment を upsert
- 保存時にバリデーション（二重チェック）
- status は DRAFT のままでもよい、別途「確定」ボタンで CONFIRMED にできる（MVPは一括でCONFIRMEDでもOK）

---

# ✅ Tailwind（shiftsのみ）
- shifts/templates/shifts/base.html を作り、そこにのみ <script src="https://cdn.tailwindcss.com"></script> を入れる
- shifts配下テンプレートは必ず base.html を継承
- sales側テンプレートは変更しない
- shifts/base.html はスマホ前提で以下最低限を用意
  - container, card, button, badge, form control の簡単な共通class

---

# ✅ 実装順（必須）
1) shifts app 作成、urls include
2) SalesLocation に requires_drive/priority 追加 → migration
3) shifts models（UserProfile/CompanyHoliday/SchedulePeriod/Submission/Day/Assignment）作成 → migration
4) calendar utils（休日判定）
5) 管理者：period管理 / company-holidays
6) スタッフ：submit/mine/schedule
7) 管理者：dashboard/assign
8) 手動テスト手順作成

---

# ✅ 手動テスト要件（必ず書く）
1) 募集前は本人が submit できない
2) 募集中は本人が submit できる
3) 締切後は本人が submit できず、管理者のみ代理入力できる（submitted_by_admin=True, is_late_submission=True）
4) 土日祝が OFF 固定、カテゴリ不要で保存される
5) 会社休日を登録するとその日が休日扱いになる
6) 運転必須売り場に運転不可ユーザーを割り当てようとすると保存が拒否される
7) 休日の日付で assign を開くと「割当不要」になる
8) 確定シフト閲覧が period 範囲内で表示される

---

# ✅ 出力してほしい
- 変更ファイル一覧
- migrations
- モデル定義（models.py）
- views/urls/templates（主要部分）
- 権限デコレータ/ミックスイン
- 手動テスト手順（上記観点を含む）
