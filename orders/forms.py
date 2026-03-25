from django import forms
from .models import Customer, Order, OrderItem, OrderSettings, PaymentMethod


class OrderSettingsForm(forms.ModelForm):
    class Meta:
        model = OrderSettings
        fields = ['tax_rate', 'company_name', 'postal_code', 'address', 'tel', 'fax']


class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ['name', 'sort_order', 'is_active']


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            'customer_type', 'company_name', 'contact_person', 'name',
            'postal_code', 'address', 'phone', 'fax', 'email',
            'price_type', 'payment_method', 'is_regular', 'notes',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['payment_method'].queryset = PaymentMethod.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('customer_type') == 'B2B' and not cleaned.get('company_name'):
            self.add_error('company_name', '法人の場合、会社名は必須です。')
        return cleaned


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['customer', 'order_date', 'delivery_date', 'notes']
        widgets = {
            'order_date': forms.DateInput(attrs={'type': 'date'}),
            'delivery_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].queryset = Customer.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get('customer')
        delivery_date = cleaned.get('delivery_date')
        if customer and delivery_date:
            qs = Order.objects.filter(customer=customer, delivery_date=delivery_date)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    f'この顧客の {delivery_date.strftime("%Y/%m/%d")} の受注は既に登録されています。'
                )
        return cleaned

    def _post_clean(self):
        # UniqueConstraintのデフォルトエラーメッセージを抑制
        # clean()で日本語のカスタムメッセージを既に表示している
        errors_before = len(self.errors.get('__all__', []))
        super()._post_clean()
        all_errors = self.errors.get('__all__')
        if all_errors and len(all_errors) > errors_before:
            # clean()で追加済みのエラーだけ残し、constraint由来のエラーを除去
            self._errors['__all__'] = self.error_class(all_errors[:errors_before])


class OrderItemForm(forms.ModelForm):
    class Meta:
        model = OrderItem
        fields = ['product', 'product_name', 'quantity', 'quantity_large',
                  'quantity_regular', 'quantity_small', 'unit_price', 'subtotal']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product_name'].required = False
        self.fields['product'].required = False
        self.fields['quantity'].required = False
        self.fields['subtotal'].required = False


OrderItemFormSet = forms.inlineformset_factory(
    Order,
    OrderItem,
    form=OrderItemForm,
    extra=10,
    can_delete=True,
)
