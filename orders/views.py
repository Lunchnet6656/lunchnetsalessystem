from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db.models import Count, Sum, Q
from django.utils import timezone
import json
from .models import Customer, Order, OrderItem, OrderSettings, PaymentMethod, ExtraProduct, OrderExtraItem, DeliveryBin
from .forms import CustomerForm, OrderForm, OrderItemFormSet, OrderSettingsForm, PaymentMethodForm, ExtraProductForm, OrderExtraItemFormSet, DeliveryBinForm
from sales.models import Product
from datetime import datetime, timedelta


def _get_extra_products_json():
    products = ExtraProduct.objects.filter(is_active=True)
    return json.dumps([
        {'id': p.pk, 'name': p.name, 'unit_price': int(p.unit_price)}
        for p in products
    ], ensure_ascii=False)


@login_required
def order_list(request):
    return redirect('orders:regular_dashboard')


@login_required
def order_create(request, customer_id=None):
    today = timezone.now().date()
    initial = {'order_date': today, 'delivery_date': today}
    delivery_date_str = request.GET.get('delivery_date')
    if delivery_date_str:
        try:
            initial['delivery_date'] = datetime.strptime(delivery_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    if customer_id:
        customer = get_object_or_404(Customer, pk=customer_id, is_active=True)
        initial['customer'] = customer
        if customer.notes:
            initial['notes'] = customer.notes

    if request.method == 'POST':
        form = OrderForm(request.POST)
        formset = OrderItemFormSet(request.POST, prefix='items')
        extra_formset = OrderExtraItemFormSet(request.POST, prefix='extra_items')
        if form.is_valid() and formset.is_valid() and extra_formset.is_valid():
            order = form.save(commit=False)
            order.created_by = request.user
            order.save()
            formset.instance = order
            items = formset.save(commit=False)
            for item in items:
                total_qty = item.quantity_large + item.quantity_regular + item.quantity_small
                if total_qty == 0:
                    continue
                if item.product and not item.product_name:
                    item.product_name = item.product.name
                item.quantity = total_qty
                item.save()
            for obj in formset.deleted_objects:
                obj.delete()
            extra_formset.instance = order
            extra_items = extra_formset.save(commit=False)
            for ei in extra_items:
                if not ei.product_name and not ei.extra_product_id:
                    continue
                if not ei.quantity or not ei.unit_price:
                    continue
                if not ei.product_name and ei.extra_product:
                    ei.product_name = ei.extra_product.name
                ei.subtotal = ei.unit_price * ei.quantity
                ei.save()
            for obj in extra_formset.deleted_objects:
                obj.delete()
            messages.success(request, f'受注 {order.order_number} を登録しました。')
            return redirect('orders:order_detail', pk=order.pk)
    else:
        form = OrderForm(initial=initial)
        formset = OrderItemFormSet(prefix='items')
        extra_formset = OrderExtraItemFormSet(prefix='extra_items')

    customers = Customer.objects.filter(is_active=True)
    is_catering = False
    if customer_id:
        _c = get_object_or_404(Customer, pk=customer_id, is_active=True)
        is_catering = _c.bento_type == 'CATERING'
    context = {
        'form': form,
        'formset': formset,
        'extra_formset': extra_formset,
        'extra_products_json': _get_extra_products_json(),
        'customers': customers,
        'is_edit': False,
        'is_catering': is_catering,
    }
    return render(request, 'orders/order_form.html', context)


@login_required
def order_edit(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        form = OrderForm(request.POST, instance=order)
        formset = OrderItemFormSet(request.POST, instance=order, prefix='items')
        extra_formset = OrderExtraItemFormSet(request.POST, instance=order, prefix='extra_items')
        if form.is_valid() and formset.is_valid() and extra_formset.is_valid():
            form.save()
            items = formset.save(commit=False)
            for item in items:
                total_qty = item.quantity_large + item.quantity_regular + item.quantity_small
                if total_qty == 0:
                    continue
                if item.product and not item.product_name:
                    item.product_name = item.product.name
                item.quantity = total_qty
                item.save()
            for obj in formset.deleted_objects:
                obj.delete()
            extra_items = extra_formset.save(commit=False)
            for ei in extra_items:
                if not ei.product_name and not ei.extra_product_id:
                    continue
                if not ei.quantity or not ei.unit_price:
                    continue
                if not ei.product_name and ei.extra_product:
                    ei.product_name = ei.extra_product.name
                ei.subtotal = ei.unit_price * ei.quantity
                ei.save()
            for obj in extra_formset.deleted_objects:
                obj.delete()
            messages.success(request, f'受注 {order.order_number} を更新しました。')
            return redirect('orders:order_detail', pk=order.pk)
    else:
        form = OrderForm(instance=order)
        formset = OrderItemFormSet(instance=order, prefix='items')
        extra_formset = OrderExtraItemFormSet(instance=order, prefix='extra_items')

    customers = Customer.objects.filter(is_active=True)
    context = {
        'form': form,
        'formset': formset,
        'extra_formset': extra_formset,
        'extra_products_json': _get_extra_products_json(),
        'order': order,
        'customers': customers,
        'is_edit': True,
        'is_catering': order.customer.bento_type == 'CATERING',
    }
    return render(request, 'orders/order_form.html', context)


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related('items', 'extra_items'),
        pk=pk
    )
    return render(request, 'orders/order_detail.html', {'order': order})


@login_required
def order_delete(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        order_number = order.order_number
        order.delete()
        messages.success(request, f'受注 {order_number} を削除しました。')
        return redirect('orders:regular_dashboard')
    return render(request, 'orders/order_confirm_delete.html', {'order': order})


@login_required
def order_pdf(request, pk):
    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related('items', 'extra_items'),
        pk=pk
    )
    from .pdf import generate_delivery_slip
    buffer = generate_delivery_slip(order)
    if not order.pdf_printed_at:
        order.pdf_printed_at = timezone.now()
        order.save(update_fields=['pdf_printed_at'])
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="delivery_slip_{order.order_number}.pdf"'
    return response


@login_required
def order_print_preview(request, pk):
    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related('items', 'extra_items'),
        pk=pk
    )
    from .pdf import build_delivery_slip_context
    context = build_delivery_slip_context(order)
    if not order.pdf_printed_at:
        order.pdf_printed_at = timezone.now()
        order.save(update_fields=['pdf_printed_at'])
    return render(request, 'orders/delivery_slip_preview.html', context)


@login_required
def receipt_print_preview(request, pk):
    order = get_object_or_404(Order.objects.select_related('customer'), pk=pk)
    from .pdf import build_receipt_context
    context = build_receipt_context(order)
    return render(request, 'orders/receipt_preview.html', context)


@login_required
def receipt_pdf(request, pk):
    order = get_object_or_404(Order.objects.select_related('customer'), pk=pk)
    from .pdf import generate_receipt
    buffer = generate_receipt(order)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="receipt_{order.order_number}.pdf"'
    return response


# --- Customer views ---

@login_required
def customer_list(request):
    customers = Customer.objects.filter(is_active=True)
    q = request.GET.get('q', '').strip()
    customer_type = request.GET.get('type', '')
    if q:
        from django.db.models import Q
        customers = customers.filter(
            Q(name__icontains=q) | Q(company_name__icontains=q) |
            Q(phone__icontains=q) | Q(contact_person__icontains=q)
        )
    if customer_type:
        customers = customers.filter(customer_type=customer_type)
    context = {
        'customers': customers,
        'q': q,
        'customer_type': customer_type,
    }
    return render(request, 'orders/customer_list.html', context)


@login_required
def customer_create(request):
    if request.method == 'POST':
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save()
            messages.success(request, f'顧客「{customer}」を登録しました。')
            return redirect('orders:customer_list')
    else:
        form = CustomerForm()
    return render(request, 'orders/customer_form.html', {
        'form': form, 'is_edit': False,
        'day_fields': [('月', 'schedule_mon'), ('火', 'schedule_tue'), ('水', 'schedule_wed'),
                       ('木', 'schedule_thu'), ('金', 'schedule_fri'), ('土', 'schedule_sat'), ('日', 'schedule_sun')],
    })


@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    recent_orders = customer.orders.prefetch_related('items')[:20]
    return render(request, 'orders/customer_detail.html', {
        'customer': customer,
        'recent_orders': recent_orders,
    })


@login_required
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, f'顧客「{customer}」を更新しました。')
            return redirect('orders:customer_detail', pk=customer.pk)
    else:
        form = CustomerForm(instance=customer)
    return render(request, 'orders/customer_form.html', {
        'form': form, 'customer': customer, 'is_edit': True,
        'day_fields': [('月', 'schedule_mon'), ('火', 'schedule_tue'), ('水', 'schedule_wed'),
                       ('木', 'schedule_thu'), ('金', 'schedule_fri'), ('土', 'schedule_sat'), ('日', 'schedule_sun')],
    })


@login_required
def customer_delete(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        customer.is_active = False
        customer.save()
        messages.success(request, f'顧客「{customer}」を無効にしました。')
        return redirect('orders:customer_list')
    return render(request, 'orders/customer_confirm_delete.html', {'customer': customer})


# --- API endpoints ---

@login_required
def api_customer_info(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    return JsonResponse({
        'price_type': customer.price_type,
        'name': str(customer),
        'customer_type': customer.customer_type,
        'bento_type': customer.bento_type,
        'notes': customer.notes,
    })


@login_required
def api_products(request, date):
    try:
        if '-' in date:
            target = datetime.strptime(date, '%Y-%m-%d').date()
        else:
            target = datetime.strptime(date, '%Y%m%d').date()
    except ValueError:
        return JsonResponse({'products': []})

    candidates = [(target - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    products = Product.objects.filter(week__in=candidates).order_by('no')
    data = [
        {
            'id': p.id,
            'no': p.no,
            'name': p.name,
            'price_A': int(p.price_A),
            'price_B': int(p.price_B),
            'price_C': int(p.price_C),
            'container_type': p.container_type,
        }
        for p in products
    ]
    return JsonResponse({'products': data})


@login_required
def regular_order_dashboard(request):
    today = timezone.now().date()
    target_date_str = request.GET.get('date')
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except ValueError:
            target_date = today
    else:
        target_date = today

    weekday = target_date.weekday()  # 0=月, 1=火, 2=水, 3=木, 4=金, 5=土, 6=日
    DAY_FIELDS = ['schedule_mon', 'schedule_tue', 'schedule_wed',
                  'schedule_thu', 'schedule_fri', 'schedule_sat', 'schedule_sun']
    WEEKDAY_LABELS = ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日']
    weekday_label = WEEKDAY_LABELS[weekday]

    # 平日毎日の顧客（月〜金のみ対象）
    if weekday < 5:
        weekday_customers = Customer.objects.filter(
            is_active=True, is_regular=True, regular_type='WEEKDAY'
        )
    else:
        weekday_customers = Customer.objects.none()

    # 曜日指定の顧客
    day_field = DAY_FIELDS[weekday]
    custom_customers = Customer.objects.filter(
        is_active=True, is_regular=True, regular_type='CUSTOM',
        **{day_field: True}
    )

    regular_customers = (weekday_customers | custom_customers).select_related('payment_method')

    orders_for_date = Order.objects.filter(
        delivery_date=target_date,
        customer__in=regular_customers
    ).select_related('customer').prefetch_related('items')

    order_map = {o.customer_id: o for o in orders_for_date}

    dashboard_rows = []
    for customer in regular_customers:
        order = order_map.get(customer.pk)
        dashboard_rows.append({
            'customer': customer,
            'order': order,
            'has_order': order is not None,
            'pdf_printed': order.pdf_printed_at is not None if order else False,
            'total_quantity': order.total_quantity if order else 0,
            'total': order.total if order else 0,
        })

    total_customers = len(dashboard_rows)
    ordered_count = sum(1 for r in dashboard_rows if r['has_order'])

    # その他の注文（非定期顧客）
    other_orders = Order.objects.filter(
        delivery_date=target_date
    ).exclude(
        customer__in=regular_customers
    ).select_related('customer', 'customer__payment_method').prefetch_related('items')

    # PDF印刷済み数: その日の全注文から算出
    all_orders_for_date = Order.objects.filter(delivery_date=target_date)
    all_orders_count = all_orders_for_date.count()
    all_printed_count = all_orders_for_date.filter(pdf_printed_at__isnull=False).count()

    # メニュー別注文数集計（No1〜10すべて表示）
    week_candidates = [(target_date - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    week_products = Product.objects.filter(week__in=week_candidates, no__lte=10).order_by('no')

    order_totals = {
        row['product__id']: row
        for row in OrderItem.objects.filter(
            order__delivery_date=target_date
        ).values('product__id').annotate(
            total_large=Sum('quantity_large'),
            total_regular=Sum('quantity_regular'),
            total_small=Sum('quantity_small'),
            total_quantity=Sum('quantity'),
        )
    }

    menu_summary = []
    for p in week_products:
        totals = order_totals.get(p.id, {})
        menu_summary.append({
            'no': p.no,
            'product_name': p.name,
            'total_large': totals.get('total_large', 0) or 0,
            'total_regular': totals.get('total_regular', 0) or 0,
            'total_small': totals.get('total_small', 0) or 0,
            'total_quantity': totals.get('total_quantity', 0) or 0,
        })

    menu_totals = {
        'large': sum(m['total_large'] for m in menu_summary),
        'regular': sum(m['total_regular'] for m in menu_summary),
        'small': sum(m['total_small'] for m in menu_summary),
        'quantity': sum(m['total_quantity'] for m in menu_summary),
    }

    # 支払い方法別合計集計
    _pm_map = {}
    for row in dashboard_rows:
        if row['has_order']:
            pm = row['customer'].payment_method
            _key = pm.id if pm else 0
            if _key not in _pm_map:
                _pm_map[_key] = {
                    'name': pm.name if pm else 'その他',
                    'total': 0,
                    'count': 0,
                    'sort_order': pm.sort_order if pm else 999,
                }
            _pm_map[_key]['total'] += row['total']
            _pm_map[_key]['count'] += 1
    for order in other_orders:
        pm = order.customer.payment_method
        _key = pm.id if pm else 0
        if _key not in _pm_map:
            _pm_map[_key] = {
                'name': pm.name if pm else 'その他',
                'total': 0,
                'count': 0,
                'sort_order': pm.sort_order if pm else 999,
            }
        _pm_map[_key]['total'] += order.total
        _pm_map[_key]['count'] += 1
    payment_method_totals = sorted(_pm_map.values(), key=lambda x: x['sort_order'])

    context = {
        'target_date': target_date,
        'target_date_str': target_date.strftime('%Y-%m-%d'),
        'dashboard_rows': dashboard_rows,
        'total_customers': total_customers,
        'ordered_count': ordered_count,
        'not_ordered_count': total_customers - ordered_count,
        'other_orders': other_orders,
        'all_orders_count': all_orders_count,
        'all_printed_count': all_printed_count,
        'menu_summary': menu_summary,
        'menu_totals': menu_totals,
        'today_display': today.strftime('%Y年%m月%d日'),
        'payment_method_totals': payment_method_totals,
        'weekday_label': weekday_label,
    }
    return render(request, 'orders/regular_dashboard.html', context)


@login_required
def api_check_duplicate(request):
    customer_id = request.GET.get('customer_id')
    delivery_date = request.GET.get('delivery_date')
    exclude_pk = request.GET.get('exclude_pk')
    if not customer_id or not delivery_date:
        return JsonResponse({'duplicate': False})
    qs = Order.objects.filter(customer_id=customer_id, delivery_date=delivery_date)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return JsonResponse({'duplicate': qs.exists()})


@login_required
def api_order_totals(request, date):
    """納品日ごとのメニュー別注文数合計を返すAPI"""
    try:
        target_date = datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'totals': []})

    qs = OrderItem.objects.filter(order__delivery_date=target_date, product__isnull=False)

    exclude_order_pk = request.GET.get('exclude_order_pk')
    if exclude_order_pk:
        qs = qs.exclude(order__pk=exclude_order_pk)

    order_totals = {
        row['product__id']: row
        for row in qs.values('product__id').annotate(
            total_large=Sum('quantity_large'),
            total_regular=Sum('quantity_regular'),
            total_small=Sum('quantity_small'),
            total_quantity=Sum('quantity'),
        )
    }

    totals = [
        {
            'product_id': pid,
            'total_large': vals.get('total_large', 0) or 0,
            'total_regular': vals.get('total_regular', 0) or 0,
            'total_small': vals.get('total_small', 0) or 0,
            'total_quantity': vals.get('total_quantity', 0) or 0,
        }
        for pid, vals in order_totals.items()
    ]

    return JsonResponse({'totals': totals})


# --- PaymentMethod views ---

@login_required
def payment_method_list(request):
    payment_methods = PaymentMethod.objects.all()
    return render(request, 'orders/payment_method_list.html', {'payment_methods': payment_methods})


@login_required
def payment_method_create(request):
    if request.method == 'POST':
        form = PaymentMethodForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f'支払い方法「{form.instance.name}」を登録しました。')
            return redirect('orders:order_settings')
    else:
        form = PaymentMethodForm()
    return render(request, 'orders/payment_method_form.html', {'form': form, 'is_edit': False})


@login_required
def payment_method_edit(request, pk):
    payment_method = get_object_or_404(PaymentMethod, pk=pk)
    if request.method == 'POST':
        form = PaymentMethodForm(request.POST, instance=payment_method)
        if form.is_valid():
            form.save()
            messages.success(request, f'支払い方法「{payment_method.name}」を更新しました。')
            return redirect('orders:order_settings')
    else:
        form = PaymentMethodForm(instance=payment_method)
    return render(request, 'orders/payment_method_form.html', {'form': form, 'payment_method': payment_method, 'is_edit': True})


@login_required
def payment_method_delete(request, pk):
    payment_method = get_object_or_404(PaymentMethod, pk=pk)
    if request.method == 'POST':
        payment_method.is_active = False
        payment_method.save()
        messages.success(request, f'支払い方法「{payment_method.name}」を無効にしました。')
        return redirect('orders:order_settings')
    return render(request, 'orders/payment_method_confirm_delete.html', {'payment_method': payment_method})


# --- Settings view ---

@login_required
def order_settings(request):
    settings_obj = OrderSettings.load()
    payment_methods = PaymentMethod.objects.all()
    extra_products = ExtraProduct.objects.all()
    delivery_bins = DeliveryBin.objects.all()

    if request.method == 'POST':
        form = OrderSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, '設定を保存しました。')
            return redirect('orders:order_settings')
    else:
        form = OrderSettingsForm(instance=settings_obj)

    context = {
        'form': form,
        'payment_methods': payment_methods,
        'extra_products': extra_products,
        'delivery_bins': delivery_bins,
    }
    return render(request, 'orders/order_settings.html', context)


# --- ExtraProduct views ---

@login_required
def extra_product_create(request):
    if request.method == 'POST':
        form = ExtraProductForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f'追加商品「{form.instance.name}」を登録しました。')
            return redirect('orders:order_settings')
    else:
        form = ExtraProductForm()
    return render(request, 'orders/extra_product_form.html', {'form': form, 'is_edit': False})


@login_required
def extra_product_edit(request, pk):
    extra_product = get_object_or_404(ExtraProduct, pk=pk)
    if request.method == 'POST':
        form = ExtraProductForm(request.POST, instance=extra_product)
        if form.is_valid():
            form.save()
            messages.success(request, f'追加商品「{extra_product.name}」を更新しました。')
            return redirect('orders:order_settings')
    else:
        form = ExtraProductForm(instance=extra_product)
    return render(request, 'orders/extra_product_form.html', {
        'form': form, 'extra_product': extra_product, 'is_edit': True
    })


@login_required
def extra_product_delete(request, pk):
    extra_product = get_object_or_404(ExtraProduct, pk=pk)
    if request.method == 'POST':
        extra_product.is_active = False
        extra_product.save()
        messages.success(request, f'追加商品「{extra_product.name}」を無効にしました。')
        return redirect('orders:order_settings')
    return render(request, 'orders/extra_product_confirm_delete.html', {'extra_product': extra_product})


@login_required
def api_extra_products(request):
    products = ExtraProduct.objects.filter(is_active=True)
    data = [{'id': p.pk, 'name': p.name, 'unit_price': int(p.unit_price)} for p in products]
    return JsonResponse({'extra_products': data})


# --- DeliveryBin views ---

@login_required
def delivery_bin_create(request):
    if request.method == 'POST':
        form = DeliveryBinForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f'配達便「{form.instance.name}」を登録しました。')
            return redirect('orders:order_settings')
    else:
        form = DeliveryBinForm()
    return render(request, 'orders/delivery_bin_form.html', {'form': form, 'is_edit': False})


@login_required
def delivery_bin_edit(request, pk):
    delivery_bin = get_object_or_404(DeliveryBin, pk=pk)
    if request.method == 'POST':
        form = DeliveryBinForm(request.POST, instance=delivery_bin)
        if form.is_valid():
            form.save()
            messages.success(request, f'配達便「{delivery_bin.name}」を更新しました。')
            return redirect('orders:order_settings')
    else:
        form = DeliveryBinForm(instance=delivery_bin)
    return render(request, 'orders/delivery_bin_form.html', {
        'form': form, 'delivery_bin': delivery_bin, 'is_edit': True
    })


@login_required
def delivery_bin_delete(request, pk):
    delivery_bin = get_object_or_404(DeliveryBin, pk=pk)
    if request.method == 'POST':
        delivery_bin.is_active = False
        delivery_bin.save()
        messages.success(request, f'配達便「{delivery_bin.name}」を無効にしました。')
        return redirect('orders:order_settings')
    return render(request, 'orders/delivery_bin_confirm_delete.html', {'delivery_bin': delivery_bin})


# --- Delivery List views ---

def _get_scheduled_customer_ids(target_date):
    """指定日にスケジュールされているレギュラー顧客のIDセットを返す"""
    weekday = target_date.weekday()
    DAY_FIELDS = ['schedule_mon', 'schedule_tue', 'schedule_wed',
                  'schedule_thu', 'schedule_fri', 'schedule_sat', 'schedule_sun']
    if weekday < 5:
        weekday_qs = Customer.objects.filter(
            is_active=True, is_regular=True, regular_type='WEEKDAY'
        )
    else:
        weekday_qs = Customer.objects.none()
    custom_qs = Customer.objects.filter(
        is_active=True, is_regular=True, regular_type='CUSTOM',
        **{DAY_FIELDS[weekday]: True}
    )
    return set((weekday_qs | custom_qs).values_list('pk', flat=True))


def _parse_target_date(request):
    today = timezone.now().date()
    date_str = request.GET.get('date')
    if date_str:
        try:
            return datetime.strptime(date_str, '%Y-%m-%d').date(), today
        except ValueError:
            pass
    return today, today


@login_required
def delivery_list(request):
    """配達便一覧インデックスページ（管理者向け）"""
    target_date, today = _parse_target_date(request)
    weekday = target_date.weekday()
    WEEKDAY_LABELS = ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日']

    scheduled_customer_ids = _get_scheduled_customer_ids(target_date)
    orders_for_date = Order.objects.filter(
        delivery_date=target_date
    ).select_related('customer__delivery_bin').prefetch_related('items')
    order_map = {o.customer_id: o for o in orders_for_date}

    all_customer_ids = scheduled_customer_ids | set(order_map.keys())
    all_customers = Customer.objects.filter(
        pk__in=all_customer_ids, is_active=True
    ).select_related('delivery_bin')

    bins = DeliveryBin.objects.filter(is_active=True)
    assigned_bin_ids = {db.pk for db in bins}

    bin_sections = []
    for db in bins:
        bin_customers = [c for c in all_customers if c.delivery_bin_id == db.pk]
        ordered = sum(1 for c in bin_customers if c.pk in order_map)
        total_qty = sum(
            order_map[c.pk].total_quantity for c in bin_customers if c.pk in order_map
        )
        bin_sections.append({
            'bin': db,
            'total_customers': len(bin_customers),
            'ordered_count': ordered,
            'total_quantity': total_qty,
        })

    unassigned_customers = [
        c for c in all_customers
        if c.delivery_bin_id is None or c.delivery_bin_id not in assigned_bin_ids
    ]
    unassigned_section = {
        'total_customers': len(unassigned_customers),
        'ordered_count': sum(1 for c in unassigned_customers if c.pk in order_map),
        'total_quantity': sum(
            order_map[c.pk].total_quantity for c in unassigned_customers if c.pk in order_map
        ),
    }

    context = {
        'target_date': target_date,
        'target_date_str': target_date.strftime('%Y-%m-%d'),
        'weekday_label': WEEKDAY_LABELS[weekday],
        'bin_sections': bin_sections,
        'unassigned_section': unassigned_section,
        'today': today,
    }
    return render(request, 'orders/delivery_list.html', context)


@login_required
def delivery_list_by_bin(request, pk):
    """特定便の配達リスト（配達員向け・モバイルファースト）"""
    delivery_bin = get_object_or_404(DeliveryBin, pk=pk, is_active=True)
    target_date, today = _parse_target_date(request)
    weekday = target_date.weekday()
    WEEKDAY_LABELS = ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日']

    scheduled_customer_ids = _get_scheduled_customer_ids(target_date)
    orders_for_date = Order.objects.filter(
        delivery_date=target_date,
        customer__delivery_bin=delivery_bin
    ).select_related('customer', 'customer__payment_method').prefetch_related('items')
    order_map = {o.customer_id: o for o in orders_for_date}

    # この便に属する顧客（スケジュール済み + 当日受注済み）
    all_customer_ids = (
        scheduled_customer_ids | set(order_map.keys())
    )
    bin_customers = Customer.objects.filter(
        pk__in=all_customer_ids,
        is_active=True,
        delivery_bin=delivery_bin
    ).select_related('payment_method').order_by('company_name', 'name')

    rows = []
    for customer in bin_customers:
        order = order_map.get(customer.pk)
        rows.append({
            'customer': customer,
            'order': order,
            'has_order': order is not None,
            'total_quantity': order.total_quantity if order else 0,
            'notes': order.notes if order else customer.notes,
        })

    ordered_count = sum(1 for r in rows if r['has_order'])

    # 支払い方法別合計
    pm_map = {}
    for row in rows:
        if not row['has_order']:
            continue
        pm = row['customer'].payment_method
        key = pm.pk if pm else 0
        if key not in pm_map:
            pm_map[key] = {
                'name': pm.name if pm else 'その他',
                'total': 0,
                'sort_order': pm.sort_order if pm else 999,
            }
        pm_map[key]['total'] += row['order'].total
    payment_method_totals = sorted(pm_map.values(), key=lambda x: x['sort_order'])

    context = {
        'delivery_bin': delivery_bin,
        'target_date': target_date,
        'target_date_str': target_date.strftime('%Y-%m-%d'),
        'weekday_label': WEEKDAY_LABELS[weekday],
        'rows': rows,
        'ordered_count': ordered_count,
        'not_ordered_count': len(rows) - ordered_count,
        'payment_method_totals': payment_method_totals,
        'today': today,
    }
    return render(request, 'orders/delivery_list_by_bin.html', context)
