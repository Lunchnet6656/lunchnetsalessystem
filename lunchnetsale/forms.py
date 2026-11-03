from django import forms
from sales.models import Product, ItemQuantity, DailyReport, DailyReportEntry, ShiftRequest
from datetime import date, timedelta, datetime
import calendar


class UploadFileForm(forms.Form):
    location_file = forms.FileField(label='販売場所ファイルを選択してください')

class UploadMenuForm(forms.Form):
    menu_file = forms.FileField(label='メニューファイルを選択してください')

class UploadItemQuantityForm(forms.Form):
    item_quantity_file = forms.FileField(label='持参数データファイルを選択してください')

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'price_A', 'price_B', 'price_C',  'container_type']

class ItemQuantityForm(forms.ModelForm):
    class Meta:
        model = ItemQuantity
        fields = ['target_date', 'target_week', 'product', 'sales_location', 'quantity']

class ProductUpdateForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'price_A', 'price_B', 'price_C', 'container_type']


class DailyReportForm(forms.ModelForm):
    sales_price_quantity_1 = forms.DecimalField(required=False)
    sales_price_quantity_2 = forms.DecimalField(required=False)
    sales_price_quantity_3 = forms.DecimalField(required=False)

    class Meta:
        model = DailyReport
        fields = ['date', 'location', 'location_no', 'person_in_charge', 'weather', 'temp',
                'total_quantity', 'sales_price_quantity_1','sales_price_quantity_2','sales_price_quantity_3','total_sales_quantity', 'total_remaining', 
                'others_sales_1', 'others_price1', 'others_sales_quantity1', 'others_sales_2', 'others_price2', 'others_sales_quantity2', 'total_others_sales', 
                'total_revenue', 'no_rice_quantity', 'extra_rice_quantity', 'coupon_type_600', 'coupon_type_700', 'coupon_type_750', 'discount_50', 'discount_100', 'service_name', 'service_price', 'service_type_600', 'service_type_700', 'service_type_750', 'service_type_100', 'total_discount', 
                'paypay', 'digital_payment', 'cash', 'sales_difference', 
                'departure_time', 'arrival_time', 'opening_time', 'sold_out_time', 'closing_time', 
                'gasolin', 'highway', 'parking', 'part', 'others', 'comments', 'food_count_setting',
                ]

class DailyReportEntryForm(forms.ModelForm):
    class Meta:
        model = DailyReportEntry
        fields = [
            'product', 'quantity', 'sales_quantity', 'remaining_number', 'total_sales',
            'sold_out', 'popular', 'unpopular'
        ]

class TimeForm(forms.Form):
    departure_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control'}), required=False)
    arrival_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control'}), required=False)
    opening_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control'}), required=False)
    sold_out_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control'}), required=False)
    closing_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control'}), required=False)


import calendar
class ShiftSubmissionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        today = date.today()
        start_date, end_date = None, None

        if today.day <= 20 and today.day > 5:
            next_month = today.month + 1 if today.month < 12 else 1
            next_year = today.year if today.month < 12 else today.year + 1
            start_date = date(next_year, next_month, 1)
            end_date = date(next_year, next_month, 15)
        else:  # today.day <= 5 or today.day > 20
            if today.day <= 5:
                current_month = today.month
                current_year = today.year
                start_date = date(current_year, current_month, 16)
                last_day = calendar.monthrange(current_year, current_month)[1]
                end_date = date(current_year, current_month, last_day)
            else:  # today.day > 20
                next_month = today.month + 1 if today.month < 12 else 1
                next_year = today.year if today.month < 12 else today.year + 1
                start_date = date(next_year, next_month, 16)
                last_day = calendar.monthrange(next_year, next_month)[1]
                end_date = date(next_year, next_month, last_day)

        if start_date and end_date:
            for i in range((end_date - start_date).days + 1):
                current_date = start_date + timedelta(days=i)
                field_name = f"status_{current_date.strftime('%Y-%m-%d')}"
                weekday = current_date.strftime('%a')
                weekday_map = {'Mon': '月', 'Tue': '火', 'Wed': '水', 'Thu': '木', 'Fri': '金', 'Sat': '土', 'Sun': '日'}
                self.fields[field_name] = forms.BooleanField(
                    required=False,
                    widget=forms.CheckboxInput(attrs={'class': 'shift-checkbox'}),
                    label=f"{current_date.strftime('%Y/%m/%d')} ({weekday_map[weekday]})"
                )
                
class ShiftRequestForm(forms.ModelForm):
    class Meta:
        model = ShiftRequest
        fields = ['date', 'is_off', 'comment']

    def clean_date(self):
        selected_date = self.cleaned_data['date']
        user = self.instance.user if self.instance.pk else self.initial['user']

        # 締め切り日（5日 or 20日）を過ぎていたら編集不可
        today = date.today()
        if (today.day > 5 and selected_date.day >= 16) or (today.day > 20 and selected_date.day <= 15):
            raise forms.ValidationError("シフト締切後は変更できません。")

        # すでに同じ日付のシフトが存在するか確認
        if ShiftRequest.objects.filter(user=user, date=selected_date).exists():
            raise forms.ValidationError("この日付のシフトは既に送信されています。")

        return selected_date

class AnalysisBasisForm(forms.ModelForm):
    """実績分析の評価基準。固定を選んだときだけ固定値が必要。"""

    class Meta:
        from sales.models import AnalysisBasisSetting
        model = AnalysisBasisSetting
        fields = ["rule_mode", "avg_mode", "avg_fixed", "waste_mode", "waste_fixed", "min_days", "waste_color_mode"]
        widgets = {
            "rule_mode": forms.RadioSelect,
            "waste_color_mode": forms.RadioSelect,
            "avg_mode": forms.RadioSelect,
            "waste_mode": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rule_mode"].required = False  # 送られてこなければ「2本の線」
        self.fields["waste_color_mode"].required = False  # 送られてこなければ「基準と比べる」

    def _clean_rules(self):
        """評価ごとの評価と条件（判定順）。画面では rule-<番号>-label、rule-<番号>-cond-<番号>-metric 等の名前で送られる。
        番号は画面で並べ替え・削除のたびに 0 から振り直すので、label の欄が送られてきた番号だけを上から読む。"""
        from sales.models import (OTHER_RULE_LABEL, RULE_COLOR_CHOICES, RULE_COND_MAX, RULE_LABEL_MAX, RULE_MAX,
                                  RULE_METRIC_KEYS, RULE_METRICS, RULE_OPS)
        metrics = RULE_METRIC_KEYS[self.instance.target]
        colors = dict(RULE_COLOR_CHOICES)
        rules, seen = [], set()
        for i in range(RULE_MAX):
            key = lambda name: self.add_prefix(f"rule-{i}-{name}")
            if key("label") not in self.data:
                continue
            label = self.data.get(key("label"), "").strip()
            name = label or f"{i + 1}番目"
            if not label or len(label) > RULE_LABEL_MAX:
                self.add_error(None, f"{name}の名前は1〜{RULE_LABEL_MAX}文字")
            elif label == OTHER_RULE_LABEL or label in seen:
                self.add_error(None, f"{name}の名前が重複")
            seen.add(label)
            color = self.data.get(key("color"), "")
            if color not in colors:
                self.add_error(None, f"{name}の色を確認")
            conds = []
            for j in range(RULE_COND_MAX):
                metric = self.data.get(key(f"cond-{j}-metric"), "")
                if not metric:
                    continue
                op = self.data.get(key(f"cond-{j}-op"), "")
                if metric not in metrics or op not in RULE_OPS:
                    self.add_error(None, f"{name}の条件を確認")
                    continue
                _, _, digits, hi = RULE_METRICS[metric]
                try:
                    value = round(float(self.data.get(key(f"cond-{j}-value"), "")), digits)
                except ValueError:
                    value = None
                if value is None or not (0 <= value <= hi):
                    self.add_error(None, f"{name}の数値を確認")
                    continue
                if not digits:
                    value = int(value)
                conds.append({"metric": metric, "op": op, "value": value})
            if not conds:
                self.add_error(None, f"{name}の条件を1つ以上")
            rules.append({"label": label, "color": color, "conds": conds})
        if not rules:
            self.add_error(None, "評価を1つ以上")
        return rules

    BAND_ROWS = 5

    def _clean_bands(self):
        """廃棄率の色の段。数値が空の行は使わない。"""
        from sales.models import AnalysisBasisSetting
        colors = dict(AnalysisBasisSetting.COLOR_CHOICES)
        bands = []
        for i in range(self.BAND_ROWS):
            raw = self.data.get(self.add_prefix(f"band-{i}-min"), "").strip()
            if not raw:
                continue
            try:
                value = round(float(raw), 1)
            except ValueError:
                value = None
            if value is None or not (0 <= value <= 100):
                self.add_error(None, "色の段の数値を確認")
                continue
            color = self.data.get(self.add_prefix(f"band-{i}-color"), "")
            if color not in colors:
                self.add_error(None, "色の段の色を確認")
                continue
            bands.append({"min": value, "color": color, "bold": bool(self.data.get(self.add_prefix(f"band-{i}-bold")))})
        if not bands:
            self.add_error(None, "色の段を1つ以上")
        return sorted(bands, key=lambda b: -b["min"])

    def clean(self):
        data = super().clean()
        data["rule_mode"] = data.get("rule_mode") or "lines"
        data["waste_color_mode"] = data.get("waste_color_mode") or "basis"
        if data["waste_color_mode"] == "bands":
            self.instance.waste_bands = self._clean_bands()
        days = data.get("min_days")
        if days is not None and not (1 <= days <= 31):
            self.add_error("min_days", "1〜31の値を入力")
        if data.get("rule_mode") == "rules":
            data["rules"] = self._clean_rules()
            # 線の設定は使わないので、前の値のまま残す（線に戻したとき元どおり）
            for name in ("avg_mode", "avg_fixed", "waste_mode", "waste_fixed"):
                self.errors.pop(name, None)
                data[name] = getattr(self.instance, name)
            self.instance.rules = data["rules"]
            return data
        if data.get("avg_mode") == "fixed":
            v = data.get("avg_fixed")
            if v is None:
                self.add_error("avg_fixed", "固定値を入力")
            elif v <= 0:
                self.add_error("avg_fixed", "0より大きい値を入力")
        else:
            data["avg_fixed"] = None
        if data.get("waste_mode") == "fixed":
            v = data.get("waste_fixed")
            if v is None:
                self.add_error("waste_fixed", "固定値を入力")
            elif not (0 <= v <= 100):
                self.add_error("waste_fixed", "0〜100の値を入力")
        else:
            data["waste_fixed"] = None
        return data
