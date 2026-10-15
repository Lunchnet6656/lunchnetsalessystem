"""ユーザー管理（#188 H10 S1b）の権限の並び・フォーム・変更履歴・ロック判定。"""
from dataclasses import dataclass

from axes.models import AccessAttempt
from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.utils import timezone

from sales.models import UserChangeLog, UserMenuPermission


@dataclass(frozen=True)
class Perm:
    field: str
    label: str
    help: str
    honbu: bool = False  # 本部の人だけにONにする強い権限


# サイドバー（templates/base.html）と同じグループ・同じ並び。見比べて設定できるように
PERMISSION_GROUPS = [
    ("管理系", [
        Perm("can_view_mypage", "マイページ", "自分のプロフィールを見る"),
        Perm("can_view_upload", "データアップロード", "販売場所・メニュー・持参数のファイルを取り込む", honbu=True),
        Perm("can_view_register", "新規ユーザー登録", "スタッフのアカウントを作る（管理者のみ使える）", honbu=True),
    ]),
    ("データ管理", [
        Perm("can_view_user_list", "ユーザー管理", "権限・パスワード設定・無効化（管理者のみ使える）", honbu=True),
        Perm("can_view_location_list", "販売場所データ確認", "販売場所の追加・編集・削除"),
        Perm("can_view_product_list", "メニューデータ確認", "週ごとのメニューの編集・削除"),
        Perm("can_view_item_quantity", "持参数データ確認", "店ごとの持参数の確認・編集"),
        Perm("can_view_others_item", "その他の項目設定", "日計表の「その他」の項目"),
        Perm("can_view_direct_return_attendance", "直行直帰出勤記録", "直行直帰の出勤と勤務時間（管理者以外は自分の分だけ）"),
    ]),
    ("価格・メニュー設定", [
        Perm("can_view_price_master", "価格・メニュー設定", "価格表・割引・メニューデータベースを変える", honbu=True),
    ]),
    ("実績", [
        Perm("can_view_dashboard", "売上ダッシュボード", "売上と受注をまとめたグラフ"),
        Perm("can_view_daily_report_list", "日計表 送信結果（管理者）", "全員の日計表・現金・確認済み・CSV出力", honbu=True),
        Perm("can_view_performance_data", "日別実績集計データ", "日ごとの売上の集計"),
        Perm("can_view_performance_by_location", "販売場所別実績（管理者）", "店ごとの実績・ランキング・食数予測"),
        Perm("can_view_menu_history", "メニュー別販売履歴", "メニューごとの販売数・ランキング"),
    ]),
    ("勤怠", [
        Perm("can_view_attendance", "勤怠アプリ（管理）", "勤怠の管理画面（管理者のみ使える）"),
    ]),
    ("公式LINE", [
        Perm("can_view_stamp", "公式LINE（スタンプ）", "スタンプカードの管理（管理者のみ使える）"),
    ]),
    ("一般", [
        Perm("can_view_daily_report_rol", "日計表 送信結果（一般）", "自分の日計表の送信結果"),
        Perm("can_view_performance_by_location_rol", "販売場所別実績データ（一般）", "店ごとの月の実績"),
    ]),
    ("日計表", [
        Perm("can_view_daily_report_form", "日計表入力フォーム", "日計表を入力して送信する"),
    ]),
    ("受注", [
        Perm("can_view_orders", "受注管理", "注文・配達・請求書。細かい権限は受注アプリの設定で決める"),
    ]),
    ("シフト", [
        Perm("can_view_shift_app", "シフトアプリ", "シフト希望の提出・自分のシフト"),
    ]),
    ("ランチクエスト", [
        Perm("can_view_quest", "ランチクエスト", "販売のクエスト・レベル"),
    ]),
]
PERMS = [p for _, group in PERMISSION_GROUPS for p in group]
PERM_FIELDS = [p.field for p in PERMS]
PERM_LABELS = {p.field: p.label for p in PERMS}
ASSIGNMENTS = [("direct_return", "直行直帰"), ("shin_yokohama", "新横浜担当")]

ROLE_CHOICES = [("general", "一般"), ("admin", "管理者")]
STANDARD_COPY = ""  # 権限のコピー元「一般スタッフの標準」＝モデルの既定値

USERNAME_HELP = "半角英数字と _ . - @ + が使えます。あとから変更できます"
username_validator = RegexValidator(r"^[A-Za-z0-9_.@+-]+$", "ユーザー名は半角英数字と _ . - @ + で入力してください。")

# settings.LANGUAGE_CODE が en-us なので、パスワードの条件のエラーはここで日本語にする
PASSWORD_ERRORS = {
    "password_too_short": "8文字以上にしてください。",
    "password_too_common": "よく使われるパスワードは使えません。",
    "password_entirely_numeric": "数字だけのパスワードは使えません。",
    "password_too_similar": "ユーザー名や名前に似たパスワードは使えません。",
}
PASSWORD_RULES = ["8文字以上", "数字だけではない", "ユーザー名や名前に似ていない", "よく使われるパスワードではない"]


def check_password_rules(password, user):
    try:
        validate_password(password, user)
    except ValidationError as e:
        raise ValidationError([PASSWORD_ERRORS.get(err.code, err.messages[0]) for err in e.error_list])


def full_name(user):
    name = f"{user.last_name} {user.first_name}".strip()
    return name or user.username


class BootstrapMixin:
    """入力欄に Bootstrap のクラスを付け、エラーのある欄は赤枠（is-invalid）にする。"""

    def apply_bootstrap(self):
        # self.errors を触ると検証が走ってしまう（後ろの欄の日本語エラーが間に合わない）ので _errors を見る
        errors = self._errors or {}
        for name, field in self.fields.items():
            # settings が en-us なので、必須・選択肢のエラーもここで日本語にする
            field.error_messages.update({"required": "入力してください。", "invalid_choice": "選択肢から選んでください。"})
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                css = "form-check-input"
            elif isinstance(widget, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
            if name in errors:
                css += " is-invalid"
            widget.attrs["class"] = css

    def full_clean(self):
        super().full_clean()
        self.apply_bootstrap()


class PasswordFields(BootstrapMixin, forms.Form):
    new_password = forms.CharField(label="新しいパスワード", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    confirm_password = forms.CharField(label="確認", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def check_passwords(self, user):
        new, confirm = self.cleaned_data.get("new_password"), self.cleaned_data.get("confirm_password")
        if new and confirm and new != confirm:
            self.add_error("confirm_password", "確認のパスワードが一致しません。")
        elif new:
            try:
                check_password_rules(new, user)
            except ValidationError as e:
                self.add_error("new_password", e)


class PasswordSetForm(PasswordFields):
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.apply_bootstrap()

    def clean(self):
        cleaned = super().clean()
        self.check_passwords(self.user)
        return cleaned


def _copy_choices():
    users = User.objects.filter(is_active=True).order_by("date_joined")
    return [(STANDARD_COPY, "一般スタッフの標準")] + [
        (str(u.pk), f"{full_name(u)}（{'管理者' if u.is_staff else '一般'}）") for u in users
    ]


class RegisterForm(PasswordFields):
    last_name = forms.CharField(label="姓", max_length=150)
    first_name = forms.CharField(label="名", max_length=150)
    username = forms.CharField(label="ユーザー名（ログインに使う）", max_length=150, validators=[username_validator],
                               help_text=USERNAME_HELP)
    role = forms.ChoiceField(label="区分", choices=ROLE_CHOICES, initial="general", widget=forms.RadioSelect)
    copy_from = forms.ChoiceField(label="権限のコピー元", required=False)
    direct_return = forms.BooleanField(label="直行直帰", required=False)
    shin_yokohama = forms.BooleanField(label="新横浜担当", required=False)

    field_order = ["last_name", "first_name", "username", "new_password", "confirm_password",
                   "role", "copy_from", "direct_return", "shin_yokohama"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password"].label = "初期パスワード"
        self.fields["copy_from"].choices = _copy_choices()
        self.apply_bootstrap()

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("このユーザー名はすでに使われています。別のユーザー名にしてください。")
        return username

    def clean(self):
        cleaned = super().clean()
        probe = User(username=cleaned.get("username", ""), first_name=cleaned.get("first_name", ""),
                     last_name=cleaned.get("last_name", ""))
        self.check_passwords(probe)
        return cleaned


class UserEditForm(BootstrapMixin, forms.Form):
    last_name = forms.CharField(label="姓", max_length=150)
    first_name = forms.CharField(label="名", max_length=150)
    username = forms.CharField(label="ユーザー名", max_length=150, validators=[username_validator], help_text=USERNAME_HELP)
    role = forms.ChoiceField(label="区分", choices=ROLE_CHOICES, widget=forms.RadioSelect)
    direct_return = forms.BooleanField(label="直行直帰", required=False)
    shin_yokohama = forms.BooleanField(label="新横浜担当", required=False)

    def __init__(self, *args, target, lock_permissions=False, **kwargs):
        perm = target.menu_permission
        initial = {"last_name": target.last_name, "first_name": target.first_name, "username": target.username,
                   "role": "admin" if target.is_staff else "general",
                   "direct_return": perm.direct_return, "shin_yokohama": perm.shin_yokohama}
        initial.update({f: getattr(perm, f) for f in PERM_FIELDS})
        super().__init__(*args, initial=initial, **kwargs)
        self.target = target
        for f in PERM_FIELDS:
            self.fields[f] = forms.BooleanField(label=PERM_LABELS[f], required=False)
        if lock_permissions:
            # disabled のフィールドは POST を無視して initial を使う（画面を書き換えて送られても変わらない）
            self.fields["role"].disabled = True
            for f in PERM_FIELDS:
                self.fields[f].disabled = True
        self.apply_bootstrap()

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exclude(pk=self.target.pk).exists():
            raise ValidationError("このユーザー名はすでに使われています。")
        return username

    def permission_groups(self):
        """テンプレート用：グループごとに (Perm, BoundField) を並べる。"""
        return [(name, [(p, self[p.field]) for p in group]) for name, group in PERMISSION_GROUPS]


# ===== 変更履歴 =====

def log(target, by, action, detail=""):
    UserChangeLog.objects.create(target=target, changed_by=by, action=action, detail=detail[:255])


def on_off(value):
    return "ON" if value else "OFF"


def save_edit(form, by):
    """編集フォームの内容を保存し、変わった項目を1項目1行で履歴に残す。"""
    user, perm, data = form.target, form.target.menu_permission, form.cleaned_data
    entries = []
    for field, label in (("last_name", "姓"), ("first_name", "名"), ("username", "ユーザー名")):
        old, new = getattr(user, field), data[field]
        if old != new:
            entries.append(("profile", f"{label}：{old or '（空）'} → {new}"))
            setattr(user, field, new)
    is_staff = data["role"] == "admin"
    if user.is_staff != is_staff:
        entries.append(("role", f"区分：{'管理者' if is_staff else '一般'}に変更"))
        user.is_staff = is_staff
    for f in PERM_FIELDS:
        if getattr(perm, f) != data[f]:
            entries.append(("permission", f"{PERM_LABELS[f]}を{on_off(data[f])}"))
            setattr(perm, f, data[f])
    for f, label in ASSIGNMENTS:
        if getattr(perm, f) != data[f]:
            entries.append(("assignment", f"{label}を{on_off(data[f])}"))
            setattr(perm, f, data[f])
    user.save()
    perm.save()
    for action, detail in entries:
        log(user, by, action, detail)
    return len(entries)


def create_user(form, by):
    data = form.cleaned_data
    is_staff = data["role"] == "admin"
    user = User.objects.create_user(username=data["username"], password=data["new_password"],
                                    first_name=data["first_name"], last_name=data["last_name"], is_staff=is_staff)
    # post_save のシグナルが既定の権限で作っている。コピー元があればそれで上書きする
    perm = user.menu_permission
    source = None
    if data["copy_from"]:
        source = User.objects.select_related("menu_permission").get(pk=data["copy_from"])
        for f in PERM_FIELDS:
            setattr(perm, f, getattr(source.menu_permission, f))
    else:
        for f in PERM_FIELDS:
            setattr(perm, f, UserMenuPermission._meta.get_field(f).default)
    perm.direct_return = data["direct_return"]
    perm.shin_yokohama = data["shin_yokohama"]
    perm.save()
    copied = f"{full_name(source)}さんと同じ" if source else "一般スタッフの標準"
    log(user, by, "create", f"新規作成（区分：{'管理者' if is_staff else '一般'}／権限：{copied}）")
    return user


# ===== 最終ログイン・ロック =====

def last_login_label(user, today=None):
    if not user.last_login:
        return "未ログイン"
    today = today or timezone.localdate()
    days = (today - timezone.localtime(user.last_login).date()).days
    if days <= 0:
        return "今日"
    if days == 1:
        return "昨日"
    if days <= 30:
        return f"{days}日前"
    return timezone.localtime(user.last_login).strftime("%Y-%m-%d")


def idle_days(user, today=None):
    """最終ログインからの日数。一度もログインしていなければ登録日から数える。"""
    today = today or timezone.localdate()
    since = user.last_login or user.date_joined
    return (today - timezone.localtime(since).date()).days


def _cooloff_start():
    return timezone.now() - settings.AXES_COOLOFF_TIME


def _locked_ips():
    """ロック中の回線（IP）。axes は IP 単位でブロックしているので、IP ごとの失敗回数で見る。"""
    totals = {}
    for ip, failures in AccessAttempt.objects.filter(attempt_time__gte=_cooloff_start()).values_list(
            "ip_address", "failures_since_start"):
        totals[ip] = totals.get(ip, 0) + failures
    return {ip for ip, total in totals.items() if total >= settings.AXES_FAILURE_LIMIT}


def locked_usernames():
    """ロック中の回線でログインに失敗したユーザー名。"""
    ips = _locked_ips()
    if not ips:
        return set()
    return set(
        AccessAttempt.objects.filter(ip_address__in=ips, attempt_time__gte=_cooloff_start())
        .values_list("username", flat=True)
    )


def is_locked(user):
    return user.username in locked_usernames()


def unlock(user, by):
    """そのユーザー名で失敗した回線の記録を消す（同じ回線の人も一緒に解除される）。"""
    ips = set(AccessAttempt.objects.filter(username=user.username).values_list("ip_address", flat=True))
    removed = AccessAttempt.objects.filter(ip_address__in=ips).delete()[0] if ips else 0
    removed += AccessAttempt.objects.filter(username=user.username).delete()[0]
    log(user, by, "unlock", "ロック解除")
    return removed
