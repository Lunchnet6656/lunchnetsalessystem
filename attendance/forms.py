"""勤怠アプリのフォーム。

設定・マスタの編集画面は項目が多く、手書きのPOST処理だと冗長・取りこぼしが
出るため ModelForm を使う。マスタの登録・編集は Django admin に頼らず、必ず
アプリ内の画面で行う方針（運用画面をブラックボックス化しないため）。
"""
from datetime import date as _date

from django import forms

from .models import PayrollSetting, StaffPayrollProfile


class PayrollSettingForm(forms.ModelForm):
    """給与設定の編集フォーム。組織で1件の設定（pk=1）を編集する。"""

    class Meta:
        model = PayrollSetting
        fields = [
            "closing_day",
            "min_wage",
            "wage_rounding_unit",
            "employment_insurance_rate",
            "overtime_rate",
            "over60h_rate",
            "night_rate",
            "holiday_rate",
            "night_start",
            "night_end",
            "break1_threshold_minutes",
            "break1_deduct_minutes",
            "break2_threshold_minutes",
            "break2_deduct_minutes",
            "rounding_rule",
            # 販売事業部の手当単価
            "peddling_allowance_yen",
            "box_wash_allowance_yen",
            "driver_allowance_yen",
        ]
        widgets = {
            "night_start": forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
            "night_end": forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
        }
        help_texts = {
            "closing_day": "毎月この日で締める（1〜31）",
            "min_wage": "地域別最低賃金（円）。これを下回る時給に警告が出る",
            "wage_rounding_unit": "支給額の丸め単位（円）。1＝円単位（丸めなし）",
            "employment_insurance_rate": "雇用保険料率（労働者負担）。例: 0.0055 ＝ 5.5/1000",
            "overtime_rate": "時間外労働の割増率（例: 0.25 ＝ 25%増）",
            "over60h_rate": "月60時間を超えた時間外の割増率（例: 0.50 ＝ 50%増）",
            "night_rate": "深夜労働（深夜帯）の割増率（例: 0.25 ＝ 25%増）",
            "holiday_rate": "休日労働の割増率（現状の計算では未使用）",
            "break1_threshold_minutes": "拘束時間がこの分数を超えたら休憩1を控除（360 ＝ 6時間）",
            "break1_deduct_minutes": "控除する休憩1の分数（例: 45）",
            "break2_threshold_minutes": "拘束時間がこの分数を超えたら休憩2を控除（480 ＝ 8時間）",
            "break2_deduct_minutes": "控除する休憩2の分数（例: 60）",
            "rounding_rule": "労働時間の端数処理の運用メモ（計算は1分単位）",
            "peddling_allowance_yen": "販売事業部の行商手当（1回あたり、円）",
            "box_wash_allowance_yen": "販売事業部の箱洗い手当（1箱あたり、円）",
            "driver_allowance_yen": "配達ドライバー手当（1回あたり、円）。プロフィールで「ドライバー」設定の人のみ加算",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 全フィールドに共通のCSSクラスを付ける（テンプレートを素朴にしない）。
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} input".strip()

    def clean_closing_day(self):
        value = self.cleaned_data["closing_day"]
        if not 1 <= value <= 31:
            raise forms.ValidationError("締め日は1〜31で入力してください。")
        return value

    def clean_wage_rounding_unit(self):
        value = self.cleaned_data["wage_rounding_unit"]
        if value < 1:
            raise forms.ValidationError("丸め単位は1以上で入力してください。")
        return value


class StaffPayrollProfileForm(forms.ModelForm):
    """スタッフごとの手当・控除（StaffPayrollProfile）の編集フォーム。"""

    class Meta:
        model = StaffPayrollProfile
        fields = [
            "commute_allowance",
            "other_allowance",
            "other_allowance_name",
            "health_insurance",
            "nursing_insurance",
            "pension_insurance",
            "resident_tax",
            "other_deduction",
            "other_deduction_name",
            "dependents_count",
            "employment_insurance_enrolled",
            "scheduled_minutes_per_day",
            "is_driver",
            "commute_method",
            "commute_distance_km",
            "fuel_efficiency_kml",
        ]
        help_texts = {
            "other_allowance_name": "給与明細に表示する手当の名称",
            "health_insurance": "社労士・年金事務所から渡る月額。未加入なら0",
            "nursing_insurance": "40歳以上が対象。月額。未加入なら0",
            "pension_insurance": "社労士・年金事務所から渡る月額。未加入なら0",
            "resident_tax": "自治体が決めた特別徴収額（月額）。無ければ0",
            "other_deduction_name": "給与明細に表示する控除の名称",
            "employment_insurance_enrolled": "加入していれば総支給額×料率で自動計算",
            "scheduled_minutes_per_day": (
                "有給1日あたりの支払い時間（販売事業部のみ使用）。例：8時間勤務なら480"
            ),
            "is_driver": "チェックすると給与計算で「ドライバー回数」入力欄が出る",
            "commute_method": "ガソリン代の自動計算に使用（車/バイクのみ）",
            "commute_distance_km": "自宅↔職場の片道距離（km）。ガソリン代計算用",
            "fuel_efficiency_kml": "車/バイクの燃費（km/L）。一般車は15目安",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 食堂事業部の編集画面ではこのフィールドを描画しないため、POSTに含まれ
        # なくても保存できるよう required=False にする（モデル既定値が効く）。
        for name in (
            "scheduled_minutes_per_day", "commute_method",
            "commute_distance_km", "fuel_efficiency_kml",
        ):
            self.fields[name].required = False
        # チェックボックス以外に共通のCSSクラスを付ける。
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} input".strip()

    def clean_scheduled_minutes_per_day(self):
        """空欄なら既存値（編集時）またはモデル既定（新規時）を維持する。"""
        value = self.cleaned_data.get("scheduled_minutes_per_day")
        if value in (None, ""):
            if self.instance and self.instance.pk:
                return self.instance.scheduled_minutes_per_day
            return 480
        return value

    def _default_or_existing(self, name, default):
        """空欄時に既存値 or モデル既定を保持するヘルパ。"""
        value = self.cleaned_data.get(name)
        if value in (None, ""):
            if self.instance and self.instance.pk:
                return getattr(self.instance, name)
            return default
        return value

    def clean_commute_method(self):
        return self._default_or_existing("commute_method", "transit")

    def clean_commute_distance_km(self):
        from decimal import Decimal
        return self._default_or_existing("commute_distance_km", Decimal("0"))

    def clean_fuel_efficiency_kml(self):
        from decimal import Decimal
        return self._default_or_existing("fuel_efficiency_kml", Decimal("15"))


class BulkMinWageUpdateForm(forms.Form):
    """最低賃金の一括更新フォーム。

    8割が最低時給というランチネットの実運用で、最低賃金が上がったときに
    「現在最低時給で働いている人」だけまとめて更新するための入力。
    """

    new_min_wage = forms.IntegerField(
        label="新しい最低賃金（円）",
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "input"}),
    )
    effective_from = forms.DateField(
        label="効力発生日",
        widget=forms.DateInput(
            attrs={"type": "date", "class": "input"},
            format="%Y-%m-%d",
        ),
        input_formats=["%Y-%m-%d"],
    )


class StaffRosterForm(forms.ModelForm):
    """労働者名簿フォーム。労基法107条の必須項目に対応。

    Staff の基本情報（氏名・事業区分・店舗・会社）に加えて、名簿項目
    （生年月日・性別・住所・連絡先・業務の種類・退職情報）を編集する。
    """

    class Meta:
        from .models import Staff as _Staff  # 循環インポート回避
        model = _Staff
        fields = [
            "display_name",
            "business_unit",
            "company",
            "store",
            "hired_on",
            "birthday",
            "gender",
            "address",
            "phone",
            "job_description",
            "paid_leave_granted_days",
            "is_active",
            "retired_on",
            "retire_reason",
        ]
        widgets = {
            "hired_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "birthday": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "retired_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        help_texts = {
            "job_description": "例：「お弁当製造」「ホール販売」「配達運転手」",
            "paid_leave_granted_days": "10月〜9月の有給年度の付与日数（労基法39条）。労基ルール：6ヶ月勤続=10日、1.5年=11日、…6.5年以上=20日",
            "retired_on": "退職した日。在籍中は空欄のまま",
            "retire_reason": "「契約満了」「自己都合」など。在籍中は空欄",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # date は format で str → 既存値表示を効かせる
        for name in ("hired_on", "birthday", "retired_on"):
            self.fields[name].input_formats = ["%Y-%m-%d"]
        # 付与日数は空欄でも既定値を維持できるよう required=False
        self.fields["paid_leave_granted_days"].required = False
        # 全フィールドに共通CSSクラス
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} input".strip()

    def clean_paid_leave_granted_days(self):
        """空欄なら既存値（編集時）またはモデル既定（新規時）を維持。"""
        value = self.cleaned_data.get("paid_leave_granted_days")
        if value in (None, ""):
            if self.instance and self.instance.pk:
                return self.instance.paid_leave_granted_days
            return 10
        return value

    def clean(self):
        data = super().clean()
        retired_on = data.get("retired_on")
        reason = data.get("retire_reason")
        is_active = data.get("is_active")
        # 退職日が入っていれば在籍フラグも自動でOFFにする（運用ミス防止）
        if retired_on and is_active:
            data["is_active"] = False
            self.instance.is_active = False
        # 退職日が無いのに退職事由だけ入っているのは不整合
        if reason and not retired_on:
            self.add_error("retired_on", "退職事由を入力する場合は退職年月日も必要です。")
        return data


class StaffCreateForm(forms.Form):
    """スタッフ新規追加フォーム。基本情報＋個人情報＋勤務情報＋初期時給を1つに。

    User と Staff の2モデルを跨ぐので ModelForm にせず、サービス層
    services.create_staff_with_user に委譲する設計。
    """

    # 認証アカウント
    username = forms.CharField(
        label="ユーザー名（ログイン用）", max_length=150,
        help_text="半角英数。打刻URLには使わないが本人ログインや代理打刻で必要",
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "taro_yamada"}),
    )
    password = forms.CharField(
        label="初期パスワード", min_length=8, max_length=128,
        initial="password6656",
        help_text="初期値は password6656。本人に渡したあと、必要に応じて管理者が変更できます。最低8文字",
        widget=forms.PasswordInput(attrs={"class": "input"}, render_value=True),
    )
    # 基本情報
    last_name = forms.CharField(
        label="苗字", max_length=50,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    first_name = forms.CharField(
        label="名前", max_length=50,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    business_unit = forms.ChoiceField(
        label="事業区分",
        widget=forms.Select(attrs={"class": "input"}),
    )
    company = forms.ChoiceField(
        label="会社",
        widget=forms.Select(attrs={"class": "input"}),
    )
    store = forms.ModelChoiceField(
        label="所属店舗", queryset=None,
        widget=forms.Select(attrs={"class": "input"}),
    )
    # 勤務情報
    hired_on = forms.DateField(
        label="入社日", required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "input"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    job_description = forms.CharField(
        label="業務の種類", max_length=100, required=False,
        help_text="例：「お弁当製造」「ホール販売」「配達運転手」",
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    initial_hourly_wage = forms.IntegerField(
        label="初期時給（円）", min_value=0, required=False,
        help_text="入社日（無ければ今日）から適用される時給。後で「スタッフ設定」で変更可",
        widget=forms.NumberInput(attrs={"class": "input"}),
    )
    # 個人情報（労働者名簿）
    birthday = forms.DateField(
        label="生年月日", required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "input"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    gender = forms.ChoiceField(
        label="性別", required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    address = forms.CharField(
        label="住所", max_length=255, required=False,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    phone = forms.CharField(
        label="連絡先", max_length=32, required=False,
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import (
            BUSINESS_UNIT_CHOICES, COMPANY_CHOICES, Staff as _Staff, Store as _Store,
        )
        self.fields["business_unit"].choices = BUSINESS_UNIT_CHOICES
        self.fields["company"].choices = COMPANY_CHOICES
        self.fields["store"].queryset = _Store.objects.filter(is_active=True)
        self.fields["gender"].choices = _Staff.GENDER_CHOICES

    def clean_username(self):
        from django.contrib.auth.models import User as _User
        value = self.cleaned_data["username"].strip()
        if _User.objects.filter(username=value).exists():
            raise forms.ValidationError("このユーザー名は既に使われています。")
        return value
