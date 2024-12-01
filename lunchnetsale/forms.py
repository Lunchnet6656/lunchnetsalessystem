from django import forms
from sales.models import Product, ItemQuantity, DailyReport, DailyReportEntry


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
        fields = [
            'date', 'location', 'location_no', 'person_in_charge', 'weather', 'temp',
            'total_quantity', 'total_sales_quantity', 'total_remaining', 'total_revenue', 'total_discount', 'paypay', 'digital_payment',
            'cash', 'sales_difference', 'departure_time', 'arrival_time',
            'opening_time', 'sold_out_time', 'closing_time', 'comments',
            'food_count_setting'
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
