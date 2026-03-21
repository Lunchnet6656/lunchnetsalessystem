function confirmLogout(event) {
    if (!confirm('ログアウトしてもよろしいですか？')) {
        event.preventDefault();
    }
}


document.addEventListener('keydown', function(event) {
    if (event.key === "Enter" && event.target.tagName !== 'TEXTAREA') {
        event.preventDefault();  // Enterキーでのフォーム送信を無効化（textareaは除外）
    }
});


document.addEventListener('DOMContentLoaded', function () {
    const forms = document.querySelectorAll('form');

    forms.forEach((form) => {
        if (form.id !== 'logout-form') {  // ログアウト用フォームを除外
            form.addEventListener('submit', function (event) {
                const clickedButton = document.activeElement;

                if (clickedButton.name === 'action' && clickedButton.value === 'send') {
                    if (!confirm('販売場所は間違いありませんか？')) {
                        event.preventDefault();
                    }
                }
            });
        }
    });
});


// 数値入力フォーカス時に値を全選択（0を消す手間を省略）
document.addEventListener('focusin', function(e) {
    if (e.target.type === 'number') {
        e.target.select();
    }
});


function updateRemainingAndSales(inputElement) {
    var menuNo = inputElement.name.split('_')[2];  // 商品番号の取得
    var quantityInput = document.getElementById('quantity_' + menuNo);
    var quantity = parseInt(quantityInput.value, 10);  // 持参数の取得
    var price = Math.round(parseFloat(inputElement.dataset.price));  // 単価の取得（小数点を丸める）
    var salesQuantity = parseInt(inputElement.value, 10);  // 入力された販売数の取得

    // 完売チェックボックスの状態を取得
    var soldOutCheckbox = document.getElementById('sold_out_' + menuNo);
    var isSoldOut = soldOutCheckbox ? soldOutCheckbox.checked : false;

    // 完売状態の場合、販売数と残数を持参数に設定
    if (isSoldOut) {
        salesQuantity = quantity;  // 完売時は販売数を持参数と同じに設定
        inputElement.value = salesQuantity;  // 入力ボックスも更新
    } else {
        salesQuantity = parseInt(inputElement.value, 10); // チェックが外れた場合はユーザー入力値を使用
    }

    // 残数の計算
    var remaining = quantity - salesQuantity;

    // 売上の計算
    var totalSales = salesQuantity * price;

    // 残数と売上の表示を更新
    var remainingElement = document.getElementById('remaining_' + menuNo);
    var remainingDisplayElement = document.getElementById('remaining_display_' + menuNo);
    var totalSalesElement = document.getElementById('total_sales_' + menuNo);
    var totalSalesDisplayElement = document.getElementById('total_sales_display_' + menuNo);

    if (remainingElement) {
        remainingElement.value = remaining;
    }

    if (remainingDisplayElement) {
        remainingDisplayElement.innerText = numberWithCommas(remaining);
    }

    if (totalSalesElement) {
        totalSalesElement.value = totalSales;
    }

    if (totalSalesDisplayElement) {
        totalSalesDisplayElement.innerText = numberWithCommas(totalSales);
    }

    // 合計を再計算
    calculateTotals();
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

    document.querySelectorAll('[name^="sales_quantity_"]').forEach(function(input) {
        var menuNo = parseInt(input.name.split('_')[2], 10);  // メニュー番号を取得
        var salesQuantity = parseInt(input.value, 10);
        var quantity = parseInt(document.getElementById('quantity_' + menuNo).value, 10);
        var price = Math.round(parseFloat(input.dataset.price));
        var remaining = quantity - salesQuantity;
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
    var salesQuantityInput = document.querySelector('[name="sales_quantity_' + menuNo + '"]');
    var quantity = parseInt(document.getElementById('quantity_' + menuNo).value, 10);

    if (checkboxElement.checked) {
        // 完売にチェックが入った場合、販売数を持参数と同じに設定
        salesQuantityInput.value = quantity;
    } else {
        // 完売のチェックが外れた場合、販売数をユーザーが再設定できるようにする
        salesQuantityInput.value = 0;
    }

    // 残数と売上を再計算
    updateRemainingAndSales(salesQuantityInput);
}

// チェックボックスにイベントリスナーを追加する
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('[id^="sold_out_"]').forEach(function(checkbox) {
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
        var selectedOption = serviceNameElement.options[serviceNameElement.selectedIndex];
        servicePrice = parseInt(selectedOption.value, 10) || 0;
        var serviceStyleElement = document.getElementById('service_style').value;

        if (serviceStyleElement === 'なし') {
            skipProcessing_a = true;
            skipProcessing_b = true;
        }
        else if (serviceStyleElement === '割引') {
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
        checkbox.checked = isTotalSoldOut;
        var menuNo = checkbox.id.split('_')[2];
        var salesQuantityInput = document.querySelector('[name="sales_quantity_' + menuNo + '"]');
        var quantity = parseInt(document.getElementById('quantity_' + menuNo).value, 10);

        if (isTotalSoldOut) {
            salesQuantityInput.value = quantity;
        } else {
            salesQuantityInput.value = 0;
        }

        // 残数と売上を再計算
        updateRemainingAndSales(salesQuantityInput);
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
    checkbox.addEventListener('change', function() {
        var menuNo = this.id.split('_')[2];
        var salesQuantityInput = document.querySelector('[name="sales_quantity_' + menuNo + '"]');
        updateRemainingAndSales(salesQuantityInput);
    });
});

// ページロード時に合計を計算
document.addEventListener('DOMContentLoaded', function() {
    calculateTotals();
    updateOthers();
});
