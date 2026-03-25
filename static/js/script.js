function confirmLogout(event) {
    if (!confirm('ログアウトしてもよろしいですか？')) {
        event.preventDefault();
    }
}


document.addEventListener('keydown', function(event) {
    if (event.key === "Enter" && event.target.tagName !== 'TEXTAREA') {
        event.preventDefault();  // Enterキーでのフォーム送信を無効化（textareaは除外）
        // 次の入力フィールドにフォーカス移動
        var form = event.target.closest('form');
        if (form) {
            var focusable = Array.from(form.querySelectorAll('input:not([type="hidden"]):not([readonly]):not(:disabled), select:not(:disabled), textarea:not(:disabled)'));
            var index = focusable.indexOf(event.target);
            if (index >= 0 && index < focusable.length - 1) {
                focusable[index + 1].focus();
            }
        }
    }
});


// 書式付き値から数値を抽出する関数
function stripFormatting(value) {
    if (!value) return '0';
    // ▲, ＋, ±, カンマを除去し、▲の場合は負数にする
    var isNegative = value.indexOf('▲') !== -1;
    var cleaned = value.replace(/[▲＋±,+]/g, '');
    var num = parseFloat(cleaned) || 0;
    if (isNegative) num = -num;
    return String(Math.round(num));
}

document.addEventListener('DOMContentLoaded', function () {
    const forms = document.querySelectorAll('form');

    forms.forEach((form) => {
        if (form.id !== 'logout-form') {  // ログアウト用フォームを除外
            form.addEventListener('submit', function (event) {
                const clickedButton = document.activeElement;

                if (clickedButton.name === 'action' && clickedButton.value === 'send') {
                    if (!confirm('販売場所は間違いありませんか？')) {
                        event.preventDefault();
                        return;
                    }

                    // 時間順序チェック
                    if (typeof validateTimeOrder === 'function') {
                        var timeWarning = validateTimeOrder();
                        if (timeWarning && !confirm(timeWarning)) {
                            event.preventDefault();
                            return;
                        }
                    }

                    // 差額チェック
                    var diffEl = document.getElementById('sales_difference');
                    if (diffEl) {
                        var diffVal = stripFormatting(diffEl.value);
                        if (parseInt(diffVal, 10) !== 0) {
                            if (!confirm('差額が0ではありません（' + diffEl.value + '円）。送信してよろしいですか？')) {
                                event.preventDefault();
                                return;
                            }
                        }
                    }
                }

                // 書式付きreadonly フィールドの値を数値に戻す
                var fieldsToClean = ['total_revenue', 'total_discount', 'sales_difference', 'total_others_sales'];
                fieldsToClean.forEach(function(fieldId) {
                    var el = document.getElementById(fieldId);
                    if (el) {
                        el.value = stripFormatting(el.value);
                    }
                });
            });
        }
    });
});


// 数値入力フォーカス時に値を全選択（0を消す手間を省略）
document.addEventListener('focusin', function(e) {
    if (e.target.type === 'number') {
        var target = e.target;
        setTimeout(function() { target.select(); }, 0);
    }
});


// 残数入力から販売数・売上を自動計算する関数
function updateFromRemaining(inputElement) {
    var menuNo = inputElement.id.replace('remaining_input_', '');  // 商品番号の取得
    var quantityInput = document.getElementById('quantity_' + menuNo);
    var quantity = parseInt(quantityInput.value, 10) || 0;  // 持参数の取得
    var priceElement = document.getElementById('price_' + menuNo);
    var price = Math.round(parseFloat(priceElement.textContent)) || 0;  // 単価の取得
    var remaining = parseInt(inputElement.value, 10) || 0;  // 入力された残数の取得

    // 残数が持参数を超えた場合は持参数に制限
    if (remaining > quantity && quantity > 0) {
        remaining = quantity;
        inputElement.value = quantity;
    }

    // 販売数の計算
    var salesQuantity = Math.max(0, quantity - remaining);

    // 売上の計算
    var totalSales = salesQuantity * price;

    // 販売数の表示を更新
    var salesQuantityElement = document.getElementById('sales_quantity_' + menuNo);
    var salesQuantityDisplayElement = document.getElementById('sales_quantity_display_' + menuNo);

    if (salesQuantityElement) {
        salesQuantityElement.value = salesQuantity;
    }

    if (salesQuantityDisplayElement) {
        salesQuantityDisplayElement.innerText = numberWithCommas(salesQuantity);
    }

    // 残数のhidden inputを更新
    var remainingHidden = document.getElementById('remaining_' + menuNo);
    if (remainingHidden) {
        remainingHidden.value = remaining;
    }

    // 売上の表示を更新
    var totalSalesElement = document.getElementById('total_sales_' + menuNo);
    var totalSalesDisplayElement = document.getElementById('total_sales_display_' + menuNo);

    if (totalSalesElement) {
        totalSalesElement.value = totalSales;
    }

    if (totalSalesDisplayElement) {
        totalSalesDisplayElement.innerText = numberWithCommas(totalSales);
    }

    // 合計を再計算
    calculateTotals();
}

// 持参数変更時に残数から再計算する関数
function onQuantityChange(inputElement) {
    var menuNo = inputElement.id.replace('quantity_', '');
    var remainingInput = document.getElementById('remaining_input_' + menuNo);
    if (remainingInput) {
        updateFromRemaining(remainingInput);
    }
}

function numberWithCommas(x) {
    var number = Math.round(x);  // 小数点以下を丸める
    return number.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function calculateTotals() {
    var totalQuantity = 0;
    var totalSalesQuantity = 0;
    var totalRemaining = 0;
    var totalSales = 0;

    // 単価別の集計用変数
    var salesByPrice = {};

    // 残数入力フィールドを基準に集計
    document.querySelectorAll('[id^="remaining_input_"]').forEach(function(remainingInput) {
        var menuNo = parseInt(remainingInput.id.replace('remaining_input_', ''), 10);
        var quantity = parseInt(document.getElementById('quantity_' + menuNo).value, 10) || 0;
        var remaining = parseInt(remainingInput.value, 10) || 0;
        var salesQuantity = Math.max(0, quantity - remaining);
        var priceElement = document.getElementById('price_' + menuNo);
        var price = priceElement ? Math.round(parseFloat(priceElement.textContent)) || 0 : 0;
        var totalSalesForItem = salesQuantity * price;

        // 単価別の販売数を集計
        if (price > 0) {
            if (!salesByPrice[price]) {
                salesByPrice[price] = {
                    quantity: 0,
                    sales: 0
                };
            }
            salesByPrice[price].quantity += salesQuantity;
            salesByPrice[price].sales += totalSalesForItem;
        }

        // totalSales は menu_no == 11 も含める
        totalSales += totalSalesForItem;

        // menu_no が 11 の場合、totalQuantity と totalSalesQuantity の集計からは除外
        if (menuNo === 11) {
            return;  // この商品の quantity と salesQuantity はスキップ
        }

        totalQuantity += quantity;
        totalSalesQuantity += salesQuantity;
        totalRemaining += remaining;
    });

    document.getElementById('total_quantity').innerText = numberWithCommas(totalQuantity);
    document.getElementById('total_sales_quantity').innerText = numberWithCommas(totalSalesQuantity);
    document.getElementById('total_remaining').innerText = numberWithCommas(totalRemaining);
    document.getElementById('total_total_sales').innerText = numberWithCommas(totalSales);

    document.querySelector('input[name="total_quantity"]').value = totalQuantity;
    document.querySelector('input[name="total_sales_quantity"]').value = totalSalesQuantity;
    document.querySelector('input[name="total_remaining"]').value = totalRemaining;

    // 単価別の集計結果を表示（単価の降順で処理）
    Object.entries(salesByPrice)
        .sort(([priceA], [priceB]) => parseInt(priceB) - parseInt(priceA))
        .forEach(([price, data], index) => {
            const priceDisplay = document.getElementById(`price_${price}_sales_quantity`);
            const priceDisplay2 = document.getElementById(`price_${price}_sales`);
            if (priceDisplay) {
                priceDisplay.textContent = `${numberWithCommas(data.quantity)}`;
                priceDisplay2.textContent = `${numberWithCommas(data.sales)}`;
                // valueも書き換え
                document.querySelector(`input[name="sales_price_quantity_${index + 1}"]`).value = data.quantity;
            }
        });

    // 割引計算の更新
    updateDiscount();
}

// チェックボックスの状態が変更された時に呼ばれる関数
function onSoldOutCheckboxChange(checkboxElement) {
    var menuNo = checkboxElement.id.split('_')[2];  // 商品番号の取得
    var remainingInput = document.getElementById('remaining_input_' + menuNo);
    var quantity = parseInt(document.getElementById('quantity_' + menuNo).value, 10) || 0;
    var row = checkboxElement.closest('tr');

    if (checkboxElement.checked) {
        // 完売にチェックが入った場合、残数を0に設定
        remainingInput.value = 0;
        if (row) row.classList.add('sold-out-row');
    } else {
        // 完売のチェックが外れた場合、残数を持参数に戻す
        remainingInput.value = quantity;
        if (row) row.classList.remove('sold-out-row');
    }

    // 販売数と売上を再計算
    updateFromRemaining(remainingInput);
}

// チェックボックスにイベントリスナーを追加する
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('[id^="sold_out_"]').forEach(function(checkbox) {
        if (checkbox.id === 'sold_out_total') return;  // 全完売チェックは別処理
        checkbox.addEventListener('change', function() {
            onSoldOutCheckboxChange(this);
        });
    });

    // 初期合計計算
    calculateTotals();
});

function updateOthers() {
    // 各項目の単価と個数を取得
    var price1 = parseFloat(document.getElementById('others_price1').value) || 0;
    var quantity1 = parseInt(document.getElementById('others_sales_quantity1').value, 10) || 0;
    var price2 = parseFloat(document.getElementById('others_price2').value) || 0;
    var quantity2 = parseInt(document.getElementById('others_sales_quantity2').value, 10) || 0;

    // 各項目の合計を計算
    var others_total1 = price1 * quantity1;
    var others_total2 = price2 * quantity2;

    // 全体の合計を計算
    var total_others = others_total1 + others_total2;

    // 合計金額を表示
    document.getElementById('total_others_sales').value = numberWithCommas(total_others);
    // その他が変更されたら再計算する
    updateRevenue();
}

// すべての price と quantity にイベントリスナーを一括で設定
document.querySelectorAll('.others-input').forEach(function(input) {
    input.addEventListener('input', updateOthers);
});


function updateDiscount() {
    var noRiceQuantity = parseInt(document.getElementById('no_rice_quantity').value, 10) || 0;
    var extraRiceQuantity = parseInt(document.getElementById('extra_rice_quantity').value, 10) || 0;
    var couponQuantity600 = parseInt(document.getElementById('coupon_type_600').value, 10) || 0;
    var couponQuantity700 = parseInt(document.getElementById('coupon_type_700').value, 10) || 0;
    var discount50 = parseInt(document.getElementById('discount_50').value, 10) || 0;
    var discount100 = parseInt(document.getElementById('discount_100').value, 10) || 0;

    // ご飯なし・追加の単価を設定
    var noRiceUnitPrice = -100;
    var extraRiceUnitPrice = 100;

    // 割引・返品・返金の単価を設定
    var discountPrice50 = -50;
    var discountPrice100 = -100;

    var noRiceTotal = noRiceQuantity * noRiceUnitPrice;
    var extraRiceTotal = extraRiceQuantity * extraRiceUnitPrice;
    var couponTotal = (-650 * couponQuantity600) + (-700 * couponQuantity700);
    var discount50Total = discount50 * discountPrice50;
    var discount100Total = discount100 * discountPrice100;

    var skipProcessing_a = false;
    var skipProcessing_b = false;
    var serviceNameElement = document.getElementById('service_name');
    var servicePrice = 0;
    if (!serviceNameElement) {
        skipProcessing_a = true;
        skipProcessing_b = true;
    } else {
        // <select>（入力フォーム）と <input>（編集フォーム）の両方に対応
        if (serviceNameElement.tagName === 'SELECT') {
            var selectedOption = serviceNameElement.options[serviceNameElement.selectedIndex];
            servicePrice = parseInt(selectedOption.value, 10) || 0;
        } else {
            servicePrice = parseInt(serviceNameElement.value, 10) || 0;
        }

        var serviceStyleEl = document.getElementById('service_style');
        var serviceStyleValue = serviceStyleEl ? serviceStyleEl.value : '';

        if (serviceStyleValue === 'なし' || !serviceStyleValue) {
            skipProcessing_a = true;
            skipProcessing_b = true;
        }
        else if (serviceStyleValue === '割引') {
            skipProcessing_a = true;
            skipProcessing_b = false;
        }
        else {
            skipProcessing_a = false;
            skipProcessing_b = true;
        }
    }

    var serviceTotal = 0;

    if (!skipProcessing_a) {
      servicePrice = parseInt(serviceNameElement.value, 10) || 0;
      var serviceType600 = parseInt(document.getElementById('service_type_600').value, 10) || 0;
      var serviceType700 = parseInt(document.getElementById('service_type_700').value, 10) || 0;
        if (serviceType600 > 0 || serviceType700 > 0) {
            serviceTotal += ((-650 + servicePrice) * serviceType600) +  ((-700 + servicePrice) * serviceType700);
        }
    }

    if (!skipProcessing_b) {
      var service100 = parseInt(document.getElementById('service_type_100').value, 10) || 0;
      serviceTotal += service100 * servicePrice;
    }

    var totalDiscount = noRiceTotal + extraRiceTotal + couponTotal + discount50Total + discount100Total + serviceTotal;
    var totalDiscountElement = document.getElementById('total_discount');

    function formatDiscount(amount) {
        if (amount < 0) {
            return '▲' + Math.abs(amount).toLocaleString();
        } else {
            return '＋' + amount.toLocaleString();
        }
    }

    if (totalDiscountElement) {
        totalDiscountElement.value = formatDiscount(totalDiscount);
        totalDiscountElement.dispatchEvent(new Event('change'));
    }

    // 割引が変更されたら再計算する
    updateRevenue();
}

function toggleSoldOutTotal() {
    var isTotalSoldOut = document.getElementById('sold_out_total').checked;
    document.querySelectorAll('[id^="sold_out_"]').forEach(function(checkbox) {
        if (checkbox.id === 'sold_out_total') return;  // 自分自身はスキップ
        var menuNo = checkbox.id.split('_')[2];
        if (parseInt(menuNo, 10) === 11) return;  // 単品11(大盛りご飯)は対象外
        checkbox.checked = isTotalSoldOut;
        var remainingInput = document.getElementById('remaining_input_' + menuNo);
        var quantity = parseInt(document.getElementById('quantity_' + menuNo).value, 10) || 0;

        if (isTotalSoldOut) {
            remainingInput.value = 0;
        } else {
            remainingInput.value = quantity;
        }

        // 販売数と売上を再計算
        updateFromRemaining(remainingInput);
    });
}


function updateRevenue() {
    var totalSales = parseFloat(document.getElementById('total_total_sales').innerText.replace(/[^0-9.-]/g, '')) || 0;
    var discountValue = document.getElementById('total_discount').value;
    var totalOthersSales = parseFloat(document.getElementById('total_others_sales').value.replace(/[^0-9.-]/g, '')) || 0;

    var isNegative = discountValue.startsWith('▲');
    var cleanedDiscountValue = discountValue.replace(/^[▲+]/, '');
    var discountTotal = parseFloat(cleanedDiscountValue.replace(/[^0-9.-]/g, '')) || 0;

    if (isNegative) {
        discountTotal = -discountTotal;
    }

    var paypay = parseFloat(document.getElementById('paypay').value) || 0;
    var digitalPayment = parseFloat(document.getElementById('digital_payment').value) || 0;
    var cash = parseFloat(document.getElementById('cash').value) || 0;

    var totalRevenue = totalSales + totalOthersSales + discountTotal;
    var totalPayment = paypay + digitalPayment + cash;
    var salesDifference = totalPayment - totalRevenue;


    var salesDifferenceFormatted;
    if (salesDifference === 0) {
        salesDifferenceFormatted = '±0';
    } else if (salesDifference < 0) {
        salesDifferenceFormatted = '▲' + numberWithCommas(Math.abs(salesDifference));
    } else {
        salesDifferenceFormatted = '+' + numberWithCommas(salesDifference);
    }

    document.getElementById('total_revenue').value = numberWithCommas(Math.round(totalRevenue));
    var diffEl = document.getElementById('sales_difference');
    diffEl.value = salesDifferenceFormatted;

    // 差額ハイライト表示
    diffEl.classList.remove('bg-warning', 'bg-danger', 'text-dark', 'text-white');
    if (salesDifference !== 0) {
        if (Math.abs(salesDifference) > 500) {
            diffEl.classList.add('bg-danger', 'text-white');
        } else {
            diffEl.classList.add('bg-warning', 'text-dark');
        }
    }
}

// 個別チェックボックスにイベントリスナーを追加
document.querySelectorAll('[id^="sold_out_"]').forEach(function(checkbox) {
    if (checkbox.id === 'sold_out_total') return;  // 全完売チェックは別処理
    checkbox.addEventListener('change', function() {
        onSoldOutCheckboxChange(this);
    });
});

// 人気/不人気の排他制御
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('[id^="popular_"]').forEach(function(cb) {
        cb.addEventListener('change', function() {
            if (this.checked) {
                var menuNo = this.id.replace('popular_', '');
                var unpopular = document.getElementById('unpopular_' + menuNo);
                if (unpopular) unpopular.checked = false;
            }
        });
    });
    document.querySelectorAll('[id^="unpopular_"]').forEach(function(cb) {
        cb.addEventListener('change', function() {
            if (this.checked) {
                var menuNo = this.id.replace('unpopular_', '');
                var popular = document.getElementById('popular_' + menuNo);
                if (popular) popular.checked = false;
            }
        });
    });
});

// ページロード時に合計を計算
document.addEventListener('DOMContentLoaded', function() {
    calculateTotals();
    updateOthers();
});
