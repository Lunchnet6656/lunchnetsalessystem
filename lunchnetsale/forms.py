from django import forms
from sales.models import Product, ItemQuantity, DailyReport, DailyReportEntry,ShiftRequest
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
    class Meta:
        model = DailyReport
        fields = ['date', 'location', 'location_no', 'person_in_charge', 'weather', 'temp',
                'total_quantity', 'total_sales_quantity', 'total_remaining', 
                'others_sales_1', 'others_price1', 'others_sales_quantity1', 'others_sales_2', 'others_price2', 'others_sales_quantity2', 'total_others_sales', 
                'total_revenue', 'no_rice_quantity', 'extra_rice_quantity', 'coupon_type_600', 'coupon_type_700', 'discount_50', 'discount_100', 'service_name', 'service_price', 'service_type_600', 'service_type_700', 'service_type_100', 'total_discount', 
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
    closing_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control'}), required=True)


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