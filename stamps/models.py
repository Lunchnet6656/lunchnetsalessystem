"""自作スタンプカード（来店計測）モデル。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md

来店計測（誰が・どの店舗で・いつ）＋来店頻度UPの自作スタンプカード。LINE標準ショップカードは
使わず、LIFF＋Django＋自前DBで作る（全データ取得・店舗別・評価基準測定・引き継ぎのため）。

- RewardTier  … 特典の段階定義（5pt/10pt/20pt）。管理画面で編集可。将来A/B・調整の土台。
- StampCard   … 1会員の1サイクル分の台紙（初回利用から1ヶ月で失効）。
- StampLog    … 来店ログ（card×店舗×日時）＝来店計測の実体。
- Reward      … 到達ptで発行される特典（割引/お弁当無料）。発行・使用・失効を持つ。

会員（友だち）は予約システムと同じ reservations.LineMember を共有する。
＝予約×スタンプ×来店を同一 LINE userId で統合する（プラットフォームの友だち中核エンティティ）。
"""
from django.db import models
from django.utils import timezone

from sales.models import SalesLocation
from reservations.models import LineMember


# --- サイクル・上限の定数 ---------------------------------------------------
CARD_VALIDITY_DAYS = 30    # カード（台紙）の有効日数＝初回利用から（≒1ヶ月）。
REWARD_VALIDITY_DAYS = 30  # 特典（クーポン）の有効日数＝獲得した日から（カードとは独立）。
#   ＝月末ギリギリで獲得しても、獲得日から30日は使える（カード切替後も生きる）。
CAP_PT = 20                # 付与上限＝20pt（お弁当無料2個で打ち止め。3個目以降のスタンプはしない）。

# --- スタンプ2倍イベント ---------------------------------------------------
BONUS_MULTIPLIER = 2       # 該当日は1来店＝2pt（重複しても最大2倍で据え置き）。
BONUS_NONE = ""            # 通常（1pt）。
BONUS_RAIN = "rain"        # 雨の日ボーナス（スタッフが当日ON）。
BONUS_STREAK = "streak"    # 連続来店ボーナス（3日ごとの節目）。
BONUS_REASON_CHOICES = [
    (BONUS_RAIN, "雨の日"),
    (BONUS_STREAK, "連続来店"),
]


class RewardTier(models.Model):
    """特典の段階定義。到達ptごとに1行。管理画面（特典管理）で編集できる。

    benefit_cost は損益・コスト集計用の「1回あたり基準コスト」（円）。実装ロジックには影響しない。
    """
    KIND_DISCOUNT = "discount"
    KIND_FREE = "free"
    KIND_CHOICES = [
        (KIND_DISCOUNT, "割引"),
        (KIND_FREE, "お弁当無料"),
    ]

    threshold_pt = models.PositiveIntegerField(
        unique=True, verbose_name="到達ポイント",
        help_text="このポイント数に到達した瞬間に特典を発行する（例：5/10/20）。",
    )
    kind = models.CharField(
        max_length=10, choices=KIND_CHOICES, default=KIND_DISCOUNT, verbose_name="種類",
    )
    label = models.CharField(
        max_length=60, verbose_name="特典名",
        help_text="お客様に表示する文言（例：50円引き／お弁当1個無料）。",
    )
    discount_yen = models.PositiveIntegerField(
        default=0, verbose_name="割引額（円）",
        help_text="割引特典のときの引く金額。お弁当無料のときは0。",
    )
    benefit_cost = models.PositiveIntegerField(
        default=0, verbose_name="基準コスト（円）",
        help_text="この特典1回あたりの想定コスト（損益集計用）。割引＝割引額／お弁当無料＝お弁当原価。",
    )
    is_cap = models.BooleanField(
        default=False, verbose_name="打ち止め段階",
        help_text="ON の段階に到達したらそのサイクルは満了（以降スタンプを押さない）。20ptに付ける。",
    )
    active = models.BooleanField(default=True, verbose_name="有効")

    class Meta:
        ordering = ["threshold_pt"]
        verbose_name = "特典段階"
        verbose_name_plural = "特典段階"

    def __str__(self):
        return f"{self.threshold_pt}pt：{self.label}"

    @classmethod
    def ensure_defaults(cls):
        """3段階（5/10/20pt）の既定を無ければ作る。テスト・初期化用。"""
        defaults = [
            dict(threshold_pt=5, kind=cls.KIND_DISCOUNT, label="50円引き",
                 discount_yen=50, benefit_cost=50, is_cap=False),
            dict(threshold_pt=10, kind=cls.KIND_FREE, label="お弁当1個無料",
                 discount_yen=0, benefit_cost=293, is_cap=False),
            dict(threshold_pt=20, kind=cls.KIND_FREE, label="お弁当1個無料（2個目・打ち止め）",
                 discount_yen=0, benefit_cost=293, is_cap=True),
        ]
        for d in defaults:
            cls.objects.get_or_create(threshold_pt=d["threshold_pt"], defaults=d)
        cls.sync_cap_flag()

    @classmethod
    def effective_cap(cls):
        """カードの満了pt＝有効な段階の最大到達pt（段階が無ければ CAP_PT を既定）。

        カードの長さ（マス数）はこの値で決まる。段階を追加・変更すると自動で追従する。
        """
        top = cls.objects.filter(active=True).aggregate(m=models.Max("threshold_pt"))["m"]
        return top or CAP_PT

    @classmethod
    def sync_cap_flag(cls):
        """打ち止め(is_cap)フラグを「有効な最大到達pt段階」だけに揃える（表示・意味の一貫性）。"""
        cap = cls.effective_cap()
        cls.objects.filter(active=True, threshold_pt=cap).exclude(is_cap=True).update(is_cap=True)
        cls.objects.exclude(threshold_pt=cap).filter(is_cap=True).update(is_cap=False)
        cls.objects.filter(active=False, is_cap=True).update(is_cap=False)


class StampCard(models.Model):
    """1会員の1サイクル分の台紙。初回利用から CARD_VALIDITY_DAYS 日で失効。"""
    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"   # 打ち止め（20pt到達）でサイクル満了
    STATUS_EXPIRED = "expired"       # 期限切れ
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "利用中"),
        (STATUS_COMPLETED, "満了（打ち止め）"),
        (STATUS_EXPIRED, "期限切れ"),
    ]

    member = models.ForeignKey(
        LineMember, on_delete=models.CASCADE, related_name="stamp_cards",
        verbose_name="会員",
    )
    started_on = models.DateField(db_index=True, verbose_name="初回利用日")
    expires_on = models.DateField(db_index=True, verbose_name="有効期限")
    stamp_count = models.PositiveIntegerField(default=0, verbose_name="スタンプ数")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE,
        db_index=True, verbose_name="状態",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_on", "-created_at"]
        verbose_name = "スタンプカード"
        verbose_name_plural = "スタンプカード"

    def __str__(self):
        return f"{self.member.name}：{self.stamp_count}pt（{self.get_status_display()}）"

    def is_expired_on(self, day):
        return day > self.expires_on

    def is_capped(self):
        return self.stamp_count >= CAP_PT


class StampLog(models.Model):
    """来店ログ。スタンプ1個＝1来店。来店計測の実体（誰が・どの店舗で・いつ）。"""
    card = models.ForeignKey(
        StampCard, on_delete=models.CASCADE, related_name="logs", verbose_name="カード",
    )
    location = models.ForeignKey(
        SalesLocation, on_delete=models.PROTECT, related_name="stamp_logs",
        verbose_name="店舗",
    )
    stamped_on = models.DateField(db_index=True, verbose_name="来店日")
    stamped_at = models.DateTimeField(verbose_name="来店日時")
    points = models.PositiveSmallIntegerField(
        default=1, verbose_name="付与pt",
        help_text="この来店で押したスタンプ数。通常1、2倍イベント該当日は2（打ち止め手前で1になることもある）。",
    )
    bonus_reason = models.CharField(
        max_length=10, choices=BONUS_REASON_CHOICES, blank=True, default=BONUS_NONE,
        verbose_name="2倍の理由",
        help_text="2倍になった要因（雨の日／連続来店）。通常来店は空。集計・検証用。",
    )

    class Meta:
        ordering = ["-stamped_at"]
        verbose_name = "来店ログ"
        verbose_name_plural = "来店ログ"
        constraints = [
            # 1日1回：同一カードは同じ日に1回まで（最後の砦。サービス層でも会員単位で先に弾く）。
            models.UniqueConstraint(
                fields=["card", "stamped_on"],
                name="uniq_stamp_per_card_per_day",
            ),
        ]
        indexes = [
            models.Index(fields=["location", "stamped_on"]),
        ]

    def __str__(self):
        return f"{self.card.member.name}＠{self.location.name} {self.stamped_on}"


class Reward(models.Model):
    """到達ptで発行される特典。発行時に文言・コストをスナップショットする。"""
    STATUS_ISSUED = "issued"
    STATUS_USED = "used"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_ISSUED, "獲得済"),
        (STATUS_USED, "使用済"),
        (STATUS_EXPIRED, "期限切れ"),
    ]

    card = models.ForeignKey(
        StampCard, on_delete=models.CASCADE, related_name="rewards", verbose_name="カード",
    )
    tier = models.ForeignKey(
        RewardTier, on_delete=models.PROTECT, related_name="rewards", verbose_name="特典段階",
    )
    threshold_pt = models.PositiveIntegerField(verbose_name="到達ポイント")
    kind = models.CharField(max_length=10, verbose_name="種類")
    label = models.CharField(max_length=60, verbose_name="特典名")
    benefit_cost = models.PositiveIntegerField(default=0, verbose_name="基準コスト（円）")
    issued_at = models.DateTimeField(verbose_name="発行日時")
    valid_from = models.DateField(
        verbose_name="利用開始日",
        help_text="この日から使える。獲得の翌日＝次回来店から（獲得した来店では使えない）。",
    )
    expires_on = models.DateField(verbose_name="有効期限")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="使用日時")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED,
        db_index=True, verbose_name="状態",
    )

    class Meta:
        ordering = ["threshold_pt"]
        verbose_name = "特典"
        verbose_name_plural = "特典"
        constraints = [
            # 1サイクルに同じ段階は1つだけ。
            models.UniqueConstraint(
                fields=["card", "threshold_pt"],
                name="uniq_reward_per_card_per_tier",
            ),
        ]

    def __str__(self):
        return f"{self.label}（{self.get_status_display()}）"

    @property
    def is_redeemable_now(self):
        """今すぐ使えるか（発行済・利用開始日に達し・未失効）。"""
        today = timezone.localdate()
        return (self.status == self.STATUS_ISSUED
                and self.valid_from <= today <= self.expires_on)

    @property
    def is_pending(self):
        """獲得したが、まだ利用開始前（＝獲得した来店では使えない／次回から）。"""
        return self.status == self.STATUS_ISSUED and timezone.localdate() < self.valid_from

    # 後方互換：旧名 is_usable は「今すぐ使える」を指す。
    @property
    def is_usable(self):
        return self.is_redeemable_now


class RichMenu(models.Model):
    """自作プラットフォームのリッチメニュー定義（画像＋タップ領域）。

    A(MVP)：画面で画像アップ＋4ボタンのURLを入力→APIで登録、line_rich_menu_id をDB保存。
    B(将来)：areas(JSON) をビジュアルエディタで自由に編集できるよう、領域はJSONで保持する。
    画像はHeroku等でも消えないよう DB（BinaryField）に保持する。
    """
    PURPOSE_STAMP = "stamp"
    PURPOSE_CHOICES = [(PURPOSE_STAMP, "スタンプ利用者")]

    purpose = models.CharField(
        max_length=20, choices=PURPOSE_CHOICES, default=PURPOSE_STAMP, unique=True,
        verbose_name="用途（セグメント）",
    )
    name = models.CharField(max_length=80, default="スタンプ利用者メニュー", verbose_name="名前")
    image_data = models.BinaryField(null=True, blank=True, editable=False, verbose_name="画像(PNG)")
    image_width = models.PositiveIntegerField(default=2500)
    image_height = models.PositiveIntegerField(default=1686)
    # areas: [{"bounds":{"x","y","width","height"}, "action":{"type":"uri","uri","label"}}, ...]
    areas = models.JSONField(default=list, verbose_name="タップ領域")
    chat_bar_text = models.CharField(max_length=14, default="メニュー", verbose_name="メニューバー文言")
    line_rich_menu_id = models.CharField(max_length=64, blank=True, verbose_name="LINEリッチメニューID")
    registered_at = models.DateTimeField(null=True, blank=True, verbose_name="登録日時")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "リッチメニュー"
        verbose_name_plural = "リッチメニュー"

    def __str__(self):
        return f"{self.name}（{'登録済' if self.line_rich_menu_id else '未登録'}）"

    @property
    def is_registered(self):
        return bool(self.line_rich_menu_id and self.image_data)


class RichMenuLink(models.Model):
    """スタンプ用リッチメニューの会員ごとの割当て状況（per-user 配信の記録）。

    初回スタンプ時に、その会員の LINE userId へスタンプ用リッチメニューをリンクし、結果を残す。
    管理画面で「誰に出ているか・失敗していないか」を確認・再適用するための台帳。
    """
    STATUS_LINKED = "linked"
    STATUS_UNLINKED = "unlinked"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_LINKED, "適用中"),
        (STATUS_UNLINKED, "解除"),
        (STATUS_FAILED, "失敗"),
    ]

    member = models.OneToOneField(
        LineMember, on_delete=models.CASCADE, related_name="stamp_richmenu_link",
        verbose_name="会員",
    )
    rich_menu_id = models.CharField(max_length=64, blank=True, verbose_name="リッチメニューID")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_FAILED,
        db_index=True, verbose_name="状態",
    )
    detail = models.CharField(max_length=255, blank=True, verbose_name="詳細/エラー")
    linked_at = models.DateTimeField(null=True, blank=True, verbose_name="適用日時")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "リッチメニュー割当て"
        verbose_name_plural = "リッチメニュー割当て"

    def __str__(self):
        return f"{self.member.name}：{self.get_status_display()}"


class StampConfig(models.Model):
    """スタンプ運用の可変設定（シングルトン）。管理画面から編集する。

    以前はコード定数だった有効日数などを、運営が画面から調整できるようにする。
    行は常に1件（pk=1）。get_solo() で取得する。
    """
    card_validity_days = models.PositiveIntegerField(
        default=CARD_VALIDITY_DAYS, verbose_name="カードの有効日数",
        help_text="初回スタンプから何日でカードが切り替わるか。",
    )
    reward_validity_days = models.PositiveIntegerField(
        default=REWARD_VALIDITY_DAYS, verbose_name="クーポンの有効日数",
        help_text="特典を獲得した日から何日使えるか（カードとは独立）。",
    )
    reward_starts_next_day = models.BooleanField(
        default=True, verbose_name="クーポンは翌日から",
        help_text="ON＝獲得した来店では使えず次回来店から（10回購入で1個無料を守る）。OFF＝当日から使える。",
    )
    # --- スタンプ2倍イベント設定 ---------------------------------------------
    rain_bonus_date = models.DateField(
        null=True, blank=True, verbose_name="雨の日ボーナス対象日",
        help_text="この日付の来店はスタンプ2倍。雨の朝にスタッフが当日をセット。翌日は自動でOFF（押し忘れ防止）。",
    )
    streak_bonus_enabled = models.BooleanField(
        default=True, verbose_name="連続来店ボーナス",
        help_text="ON＝連続来店の節目でスタンプ2倍。",
    )
    streak_bonus_days = models.PositiveSmallIntegerField(
        default=3, verbose_name="連続来店の節目（日）",
        help_text="何日連続の節目で2倍にするか。3なら3・6・9日目…が2倍。",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "スタンプ運用設定"
        verbose_name_plural = "スタンプ運用設定"

    def __str__(self):
        return "スタンプ運用設定"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def is_rain_bonus_on(self, day):
        """指定日が雨の日ボーナス対象か（当日セットのみ有効・翌日は自動失効）。"""
        return self.rain_bonus_date is not None and self.rain_bonus_date == day


class FriendTagConfig(models.Model):
    """友だち「自動タグ」のしきい値（シングルトン）。管理画面（タグ設定）から編集する。

    以前はコード直書きだった判定基準（◯日以内・来店◯回…）を、運営が画面から調整できるようにする。
    保存すると全員の自動タグが即追従する（タグはDB保存せず毎回この基準で計算するため）。
    行は常に1件（pk=1）。get_solo() で取得する。
    """
    new_within_days = models.PositiveIntegerField(
        default=14, verbose_name="新規：登録からの日数",
        help_text="登録からこの日数以内、かつ来店が少ない人を「新規」にする。",
    )
    new_max_visits = models.PositiveIntegerField(
        default=2, verbose_name="新規：来店回数の上限",
        help_text="来店がこの回数以下なら「新規」の対象（超えたらリピーター等に切り替わる）。",
    )
    repeater_min_visits = models.PositiveIntegerField(
        default=1, verbose_name="リピーター：来店回数の下限",
    )
    regular_min_visits = models.PositiveIntegerField(
        default=5, verbose_name="常連：来店回数の下限",
    )
    heavy_min_visits = models.PositiveIntegerField(
        default=10, verbose_name="ヘビー：来店回数の下限",
    )
    dormant_days = models.PositiveIntegerField(
        default=21, verbose_name="離反ぎみ：最終来店からの日数",
        help_text="最終来店からこの日数以上あいたら「離反ぎみ」にする。",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "友だちタグ設定"
        verbose_name_plural = "友だちタグ設定"

    def __str__(self):
        return "友だちタグ設定"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class MemberTag(models.Model):
    """手動タグの定義（運営が任意に付けるラベル）。例：VIP／クレーム対応中／試食会来店。

    自動タグ（来店データから毎回計算）とは別枠。定義はここ、付与は MemberTagLink。
    """
    COLOR_CHOICES = [
        ("blue", "青"), ("green", "緑"), ("orange", "オレンジ"),
        ("red", "赤"), ("purple", "紫"), ("gray", "グレー"),
    ]
    name = models.CharField(max_length=20, unique=True, verbose_name="タグ名")
    color = models.CharField(
        max_length=10, choices=COLOR_CHOICES, default="blue", verbose_name="色",
    )
    order = models.PositiveIntegerField(default=0, verbose_name="表示順")
    active = models.BooleanField(default=True, verbose_name="有効")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "手動タグ"
        verbose_name_plural = "手動タグ"

    def __str__(self):
        return self.name


class MemberTagLink(models.Model):
    """友だち×手動タグの割当て（誰にどの手動タグが付いているか）。"""
    member = models.ForeignKey(
        LineMember, on_delete=models.CASCADE, related_name="tag_links", verbose_name="会員",
    )
    tag = models.ForeignKey(
        MemberTag, on_delete=models.CASCADE, related_name="member_links", verbose_name="手動タグ",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["tag__order", "tag_id"]
        verbose_name = "手動タグ割当て"
        verbose_name_plural = "手動タグ割当て"
        constraints = [
            models.UniqueConstraint(fields=["member", "tag"], name="uniq_member_tag"),
        ]

    def __str__(self):
        return f"{self.member.name}：{self.tag.name}"
