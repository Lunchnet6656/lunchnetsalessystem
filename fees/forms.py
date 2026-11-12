import datetime
from decimal import Decimal, InvalidOperation

from django import forms

from sales.models import IssuerSettings


def parse_month(value):
    """'2026-10' → date(2026, 10, 1)。読めなければ None。"""
    try:
        return datetime.datetime.strptime((value or "").strip(), "%Y-%m").date()
    except ValueError:
        return None


class DestinationForm(forms.Form):
    enabled = forms.BooleanField(required=False)
    addressee = forms.CharField(required=False, max_length=100)
    include_service = forms.BooleanField(required=False)

    def clean_addressee(self):
        # 「御中」は自動で付くので、入れてしまったら外す
        value = self.cleaned_data["addressee"].strip()
        if value.endswith("御中"):
            value = value[:-2].rstrip("　 ")
        return value

    def clean(self):
        data = super().clean()
        if data.get("enabled") and not data.get("addressee"):
            self.add_error("addressee", "宛名を入力")
        return data


class RateForm(forms.Form):
    start_month = forms.CharField(error_messages={"required": "開始月を入力"})
    rate = forms.CharField(error_messages={"required": "率を入力"})

    def __init__(self, *args, destination=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.destination = destination

    def clean_start_month(self):
        month = parse_month(self.cleaned_data["start_month"])
        if month is None:
            raise forms.ValidationError("開始月を入力")
        if self.destination is not None and self.destination.pk and \
                self.destination.rates.filter(start_month=month).exists():
            raise forms.ValidationError("同じ開始月あり")
        return month

    def clean_rate(self):
        try:
            rate = Decimal(self.cleaned_data["rate"].strip())
        except InvalidOperation:
            raise forms.ValidationError("率は0〜100")
        if not (Decimal(0) <= rate <= Decimal(100)):
            raise forms.ValidationError("率は0〜100")
        if rate != rate.quantize(Decimal("0.1")):
            raise forms.ValidationError("小数は1桁まで")
        return rate.quantize(Decimal("0.1"))


class IssuerForm(forms.ModelForm):
    class Meta:
        model = IssuerSettings
        fields = ["company_name", "postal_code", "address", "tel", "fax", "registration_number",
                  "show_registration_on_fee"]
        error_messages = {"company_name": {"required": "会社名を入力"}}


class IssueForm(forms.Form):
    issue_date = forms.DateField(error_messages={"required": "発行日を入力", "invalid": "発行日を入力"})
    pay_date = forms.DateField(error_messages={"required": "支払日を入力", "invalid": "支払日を入力"})

    def clean(self):
        data = super().clean()
        if data.get("issue_date") and data.get("pay_date") and data["pay_date"] < data["issue_date"]:
            self.add_error("pay_date", "支払日が発行日より前")
        return data
