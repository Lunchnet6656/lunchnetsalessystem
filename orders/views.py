from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db.models import Count, Sum, Q
from django.utils import timezone
from .models import Customer, Order, OrderItem, PaymentMethod
from .forms import CustomerForm, OrderForm, OrderItemFormSet, PaymentMethodForm
from sales.models import Product
from datetime import datetime, timedelta


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
        formset = OrderItemFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
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
            messages.success(request, f'受注 {order.order_number} を登録しました。')
            return redirect('orders:order_detail', pk=order.pk)
    else:
        form = OrderForm(initial=initial)
        formset = OrderItemFormSet()

    customers = Customer.objects.filter(is_active=True)
    context = {
        'form': form,
        'formset': formset,
        'customers': customers,
        'is_edit': False,
    }
    return render(request, 'orders/order_form.html', context)


@login_required
def order_edit(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        form = OrderForm(request.POST, instance=order)
        formset = OrderItemFormSet(request.POST, instance=order)
        if form.is_valid() and formset.is_valid():
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
            messages.success(request, f'受注 {order.order_number} を更新しました。')
            return redirect('orders:order_detail', pk=order.pk)
    else:
        form = OrderForm(instance=order)
        formset = OrderItemFormSet(instance=order)

    customers = Customer.objects.filter(is_active=True)
    context = {
        'form': form,
        'formset': formset,
        'order': order,
        'customers': customers,
        'is_edit': True,
    }
    return render(request, 'orders/order_form.html', context)


@login_required
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related('customer').prefetch_related('items'), pk=pk)
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
    order = get_object_or_404(Order.objects.select_related('customer').prefetch_related('items'), pk=pk)
    from .pdf import generate_delivery_slip
    buffer = generate_delivery_slip(order)
    if not order.pdf_printed_at:
        order.pdf_printed_at = timezone.now()
        order.save(update_fields=['pdf_printed_at'])
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="delivery_slip_{order.order_number}.pdf"'
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
    return render(request, 'orders/customer_form.html', {'form': form, 'is_edit': False})


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
    return render(request, 'orders/customer_form.html', {'form': form, 'customer': customer, 'is_edit': True})


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

    regular_customers = Customer.objects.filter(is_active=True, is_regular=True)

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
    ).select_related('customer').prefetch_related('items')

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

    qs = OrderItem.objects.filter(order__delivery_date=target_date)

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
            return redirect('orders:payment_method_list')
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
            return redirect('orders:payment_method_list')
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
        return redirect('orders:payment_method_list')
    return render(request, 'orders/payment_method_confirm_delete.html', {'payment_method': payment_method})
