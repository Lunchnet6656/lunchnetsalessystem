document.addEventListener('DOMContentLoaded', function() {
    const customerSelect = document.getElementById('id_customer');
    const priceTypeDisplay = document.getElementById('price-type-display');
    const addRowBtn = document.getElementById('add-row-btn');
    const grandTotalEl = document.getElementById('grand-total');
    const deliveryDateInput = document.getElementById('id_delivery_date');
    const cardsGrid = document.getElementById('product-cards-grid');
    const cardsPlaceholder = document.getElementById('cards-placeholder');
    const totalFormsInput = document.querySelector('[name$="-TOTAL_FORMS"]');
    const initialFormsInput = document.querySelector('[name$="-INITIAL_FORMS"]');

    let currentPriceType = 'A';
    let currentProducts = [];
    let largeExtraPrice = 0; // No.11の単価（大盛り割増）

    // 編集時の既存データを取得
    const existingItems = {};
    document.querySelectorAll('.formset-form').forEach(function(el) {
        const productId = el.dataset.productId;
        if (productId) {
            existingItems[productId] = {
                index: parseInt(el.dataset.index),
                quantityLarge: parseInt(el.dataset.quantityLarge) || 0,
                quantityRegular: parseInt(el.dataset.quantityRegular) || 0,
                quantitySmall: parseInt(el.dataset.quantitySmall) || 0,
                idField: el.querySelector('input[name$="-id"]'),
            };
        }
    });

    // --- 顧客選択モーダル ---
    const customerModal = document.getElementById('customer-modal');
    const customerModalOverlay = document.getElementById('customer-modal-overlay');
    const openModalBtn = document.getElementById('open-customer-modal-btn');
    const closeModalBtn = document.getElementById('close-customer-modal-btn');
    const customerSearchInput = document.getElementById('customer-search-input');
    const customerCardsGrid = document.getElementById('customer-cards-grid');
    const customerNoResults = document.getElementById('customer-no-results');
    const customerSelectedInfo = document.getElementById('customer-selected-info');

    function openCustomerModal() {
        customerModal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        if (customerSearchInput) {
            customerSearchInput.value = '';
            filterCustomerCards('');
            customerSearchInput.focus();
        }
        highlightSelectedCard();
    }

    function closeCustomerModal() {
        customerModal.style.display = 'none';
        document.body.style.overflow = '';
    }

    function highlightSelectedCard() {
        var selectedValue = customerSelect.value;
        document.querySelectorAll('.customer-card').forEach(function(card) {
            if (card.dataset.customerId === selectedValue) {
                card.classList.remove('border-gray-200');
                card.classList.add('border-blue-500', 'ring-2', 'ring-blue-500', 'bg-blue-50');
            } else {
                card.classList.remove('border-blue-500', 'ring-2', 'ring-blue-500', 'bg-blue-50');
                card.classList.add('border-gray-200');
            }
        });
    }

    function selectCustomer(customerId) {
        customerSelect.value = customerId;
        customerSelect.dispatchEvent(new Event('change'));
        closeCustomerModal();
    }

    function updateCustomerDisplay() {
        var selected = customerSelect.options[customerSelect.selectedIndex];
        if (selected && selected.value) {
            customerSelectedInfo.textContent = selected.textContent.trim();
            customerSelectedInfo.classList.remove('text-gray-500');
            customerSelectedInfo.classList.add('text-gray-800', 'font-medium');
            openModalBtn.textContent = '変更';
        } else {
            customerSelectedInfo.textContent = '顧客が選択されていません';
            customerSelectedInfo.classList.add('text-gray-500');
            customerSelectedInfo.classList.remove('text-gray-800', 'font-medium');
            openModalBtn.textContent = '顧客を選択';
        }
    }

    function filterCustomerCards(query) {
        var lowerQuery = query.toLowerCase();
        var visibleCount = 0;
        document.querySelectorAll('.customer-card').forEach(function(card) {
            var searchText = (card.dataset.searchText || '').toLowerCase();
            if (!lowerQuery || searchText.indexOf(lowerQuery) !== -1) {
                card.style.display = '';
                visibleCount++;
            } else {
                card.style.display = 'none';
            }
        });
        if (customerNoResults) {
            customerNoResults.classList.toggle('hidden', visibleCount > 0);
        }
    }

    if (openModalBtn) {
        openModalBtn.addEventListener('click', openCustomerModal);
    }
    if (closeModalBtn) {
        closeModalBtn.addEventListener('click', closeCustomerModal);
    }
    if (customerModalOverlay) {
        customerModalOverlay.addEventListener('click', closeCustomerModal);
    }
    if (customerSearchInput) {
        customerSearchInput.addEventListener('input', function() {
            filterCustomerCards(this.value);
        });
    }
    if (customerCardsGrid) {
        customerCardsGrid.addEventListener('click', function(e) {
            var card = e.target.closest('.customer-card');
            if (card) {
                selectCustomer(card.dataset.customerId);
            }
        });
    }
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && customerModal && customerModal.style.display !== 'none') {
            closeCustomerModal();
        }
    });

    // 顧客選択時に価格タイプを取得 + 表示更新
    if (customerSelect) {
        customerSelect.addEventListener('change', function() {
            const selected = this.options[this.selectedIndex];
            if (selected && selected.value) {
                currentPriceType = selected.dataset.priceType || 'A';
                priceTypeDisplay.textContent = '価格タイプ: ' + currentPriceType;
                priceTypeDisplay.classList.remove('hidden');
                updateAllCardPrices();
                // 顧客の備考を受注備考に引き継ぐ（備考欄が空の場合のみ）
                fetch('/orders/api/customer/' + selected.value + '/info/')
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        var notesField = document.getElementById('id_notes');
                        if (notesField && !notesField.value.trim() && data.notes) {
                            notesField.value = data.notes;
                        }
                    });
            } else {
                priceTypeDisplay.classList.add('hidden');
            }
            updateCustomerDisplay();
        });
        const initSelected = customerSelect.options[customerSelect.selectedIndex];
        if (initSelected && initSelected.value) {
            currentPriceType = initSelected.dataset.priceType || 'A';
            priceTypeDisplay.textContent = '価格タイプ: ' + currentPriceType;
            priceTypeDisplay.classList.remove('hidden');
        }
        updateCustomerDisplay();
    }

    // --- 重複チェック ---
    const duplicateWarning = document.getElementById('duplicate-warning');
    const orderForm = document.getElementById('order-form');
    const excludePk = orderForm ? orderForm.dataset.orderPk || '' : '';

    function checkDuplicate() {
        if (!duplicateWarning) return;
        var customerId = customerSelect ? customerSelect.value : '';
        var deliveryDate = deliveryDateInput ? deliveryDateInput.value : '';
        if (!customerId || !deliveryDate) {
            duplicateWarning.classList.add('hidden');
            return;
        }
        var url = '/orders/api/check-duplicate/?customer_id=' + customerId + '&delivery_date=' + deliveryDate;
        if (excludePk) {
            url += '&exclude_pk=' + excludePk;
        }
        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.duplicate) {
                    duplicateWarning.classList.remove('hidden');
                } else {
                    duplicateWarning.classList.add('hidden');
                }
            });
    }

    if (customerSelect) {
        customerSelect.addEventListener('change', checkDuplicate);
    }
    if (deliveryDateInput) {
        deliveryDateInput.addEventListener('change', checkDuplicate);
    }

    // --- 注文合計カラム ---
    var currentTotalsMap = {};

    function fetchOrderTotals(date) {
        if (!date) return;
        var url = '/orders/api/order-totals/' + date + '/';
        if (excludePk) {
            url += '?exclude_order_pk=' + excludePk;
        }
        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                currentTotalsMap = {};
                (data.totals || []).forEach(function(t) {
                    currentTotalsMap[String(t.product_id)] = t;
                });
                insertTotalsCards();
            });
    }

    function insertTotalsCards() {
        // 既存の合計カードを削除
        document.querySelectorAll('.order-total-card').forEach(function(el) { el.remove(); });

        document.querySelectorAll('.product-card').forEach(function(productCard) {
            var productId = productCard.dataset.productId;
            var t = currentTotalsMap[String(productId)] || {};
            var large = t.total_large || 0;
            var regular = t.total_regular || 0;
            var small = t.total_small || 0;
            var total = t.total_quantity || 0;

            var card = document.createElement('div');
            card.className = 'order-total-card border rounded-xl p-3 flex flex-col justify-center ' +
                (total > 0 ? 'border-blue-300 bg-blue-50' : 'border-gray-200 bg-gray-50');

            card.innerHTML =
                '<div style="display:flex;justify-content:space-between;font-size:0.75rem;color:#6b7280;margin-bottom:4px;">' +
                    '<span style="color:#ea580c;">大 ' + large + '</span>' +
                    '<span style="color:#2563eb;">普 ' + regular + '</span>' +
                    '<span style="color:#16a34a;">小 ' + small + '</span>' +
                '</div>' +
                '<div style="text-align:center;font-size:1.1rem;font-weight:700;color:#1f2937;">' + total + '食</div>';

            // 商品カードの直後に挿入
            productCard.after(card);
        });
    }

    // --- 納品日連動で商品リスト更新 ---
    function fetchProductsForDate(date) {
        if (!date) return;
        fetch('/orders/api/products/' + date + '/')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                currentProducts = data.products || [];
                buildProductCards();
                fetchOrderTotals(date);
            });
    }

    // --- カード生成 ---
    function buildProductCards() {
        // No.11を分離（大盛り割増用）
        var no11 = currentProducts.find(function(p) { return p.no === 11; });
        if (no11) {
            var priceKey = 'price_' + currentPriceType;
            largeExtraPrice = no11[priceKey] || 0;
        } else {
            largeExtraPrice = 0;
        }

        // No.1〜10を取得
        var mainProducts = currentProducts.filter(function(p) { return p.no >= 1 && p.no <= 10; });
        mainProducts.sort(function(a, b) { return a.no - b.no; });

        if (mainProducts.length === 0) {
            cardsGrid.classList.add('hidden');
            cardsPlaceholder.textContent = 'この納品日に該当する商品がありません';
            cardsPlaceholder.classList.remove('hidden');
            return;
        }

        cardsPlaceholder.classList.add('hidden');
        cardsGrid.classList.remove('hidden');
        cardsGrid.innerHTML = '';

        // 全体注文数ヘッダーを表示
        var totalsHeader = document.getElementById('order-totals-header');
        if (totalsHeader) totalsHeader.classList.remove('hidden');

        // フォームセットのTOTAL_FORMSを更新
        totalFormsInput.value = mainProducts.length;

        // 既存アイテム数をカウントし、INITIAL_FORMSを更新
        // Django formsetは先頭INITIAL_FORMS個のフォームに有効なPKを期待するため、
        // 既存アイテムを先頭インデックスに配置する必要がある
        var existingCount = 0;
        mainProducts.forEach(function(p) {
            if (existingItems[String(p.id)]) existingCount++;
        });
        initialFormsInput.value = existingCount;

        var existingIdx = 0;
        var newIdx = existingCount;

        mainProducts.forEach(function(product) {
            // 既存アイテムは先頭インデックス、新規は後続インデックス
            var idx;
            if (existingItems[String(product.id)]) {
                idx = existingIdx++;
            } else {
                idx = newIdx++;
            }
            var priceKey = 'price_' + currentPriceType;
            var basePrice = product[priceKey] || 0;
            var isIntegrated = (product.container_type === '一体型');
            var largePriceTotal = isIntegrated ? basePrice : basePrice + largeExtraPrice;

            // 編集時の既存データを復元
            var initLarge = 0, initRegular = 0, initSmall = 0;
            var existingIdValue = '';
            var existing = existingItems[String(product.id)];
            if (existing) {
                initLarge = existing.quantityLarge;
                initRegular = existing.quantityRegular;
                initSmall = existing.quantitySmall;
                if (existing.idField) {
                    existingIdValue = existing.idField.value;
                }
            }

            var card = document.createElement('div');
            card.className = 'product-card border-2 border-gray-200 rounded-xl p-4 transition-all';
            card.dataset.productId = product.id;
            card.dataset.productNo = product.no;
            card.dataset.basePrice = basePrice;
            card.dataset.containerType = product.container_type || '';
            card.dataset.formIndex = idx;

            var totalQty = initLarge + initRegular + initSmall;
            if (totalQty > 0) {
                card.classList.remove('border-gray-200');
                card.classList.add('border-blue-400', 'bg-blue-50');
            }

            var subtotal = calcCardSubtotal(initLarge, initRegular, initSmall, basePrice, largePriceTotal);

            card.innerHTML =
                '<!-- Hidden formset fields -->' +
                '<input type="hidden" name="items-' + idx + '-id" value="' + existingIdValue + '">' +
                '<input type="hidden" name="items-' + idx + '-product" value="' + product.id + '">' +
                '<input type="hidden" name="items-' + idx + '-product_name" value="' + escapeHtml(product.name) + '">' +
                '<input type="hidden" name="items-' + idx + '-quantity" value="' + totalQty + '">' +
                '<input type="hidden" name="items-' + idx + '-quantity_large" value="' + initLarge + '">' +
                '<input type="hidden" name="items-' + idx + '-quantity_regular" value="' + initRegular + '">' +
                '<input type="hidden" name="items-' + idx + '-quantity_small" value="' + initSmall + '">' +
                '<input type="hidden" name="items-' + idx + '-unit_price" value="' + basePrice + '">' +
                '<input type="hidden" name="items-' + idx + '-subtotal" value="' + subtotal + '">' +
                '<input type="hidden" name="items-' + idx + '-DELETE" value="">' +

                '<!-- Card header -->' +
                '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">' +
                    '<div style="display:flex;align-items:center;gap:8px;">' +
                        '<span style="display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;background:#374151;color:#fff;font-size:0.8rem;font-weight:700;">' + product.no + '</span>' +
                        '<span style="font-weight:700;color:#1f2937;font-size:1rem;">' + escapeHtml(product.name) + '</span>' +
                    '</div>' +
                    '<span style="font-size:0.875rem;color:#6b7280;">単価 &yen;' + basePrice.toLocaleString() + '</span>' +
                '</div>' +

                '<!-- 1行目: ラベル -->' +
                '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:4px;">' +
                    '<div style="text-align:center;font-size:0.875rem;font-weight:600;color:#ea580c;">大盛り</div>' +
                    '<div style="text-align:center;font-size:0.875rem;font-weight:600;color:#2563eb;">普通盛り</div>' +
                    '<div style="text-align:center;font-size:0.875rem;font-weight:600;color:#16a34a;">小盛り</div>' +
                '</div>' +

                '<!-- 2行目: 数量 -->' +
                '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:4px;">' +
                    '<div class="qty-large-display" style="text-align:center;font-size:1.5rem;font-weight:700;color:#ea580c;">' + initLarge + '</div>' +
                    '<div class="qty-regular-display" style="text-align:center;font-size:1.5rem;font-weight:700;color:#2563eb;">' + initRegular + '</div>' +
                    '<div class="qty-small-display" style="text-align:center;font-size:1.5rem;font-weight:700;color:#16a34a;">' + initSmall + '</div>' +
                '</div>' +

                '<!-- 3行目: +-ボタン -->' +
                '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px;">' +
                    '<div style="display:flex;justify-content:center;gap:6px;">' +
                        '<button type="button" class="qty-minus" data-size="large" style="width:36px;height:36px;display:flex;align-items:center;justify-content:center;background:#fed7aa;color:#c2410c;border:none;border-radius:8px;font-size:1.125rem;font-weight:700;cursor:pointer;">&minus;</button>' +
                        '<button type="button" class="qty-plus" data-size="large" style="width:36px;height:36px;display:flex;align-items:center;justify-content:center;background:#ea580c;color:#fff;border:none;border-radius:8px;font-size:1.125rem;font-weight:700;cursor:pointer;">&plus;</button>' +
                    '</div>' +
                    '<div style="display:flex;justify-content:center;gap:6px;">' +
                        '<button type="button" class="qty-minus" data-size="regular" style="width:36px;height:36px;display:flex;align-items:center;justify-content:center;background:#bfdbfe;color:#1d4ed8;border:none;border-radius:8px;font-size:1.125rem;font-weight:700;cursor:pointer;">&minus;</button>' +
                        '<button type="button" class="qty-plus" data-size="regular" style="width:36px;height:36px;display:flex;align-items:center;justify-content:center;background:#2563eb;color:#fff;border:none;border-radius:8px;font-size:1.125rem;font-weight:700;cursor:pointer;">&plus;</button>' +
                    '</div>' +
                    '<div style="display:flex;justify-content:center;gap:6px;">' +
                        '<button type="button" class="qty-minus" data-size="small" style="width:36px;height:36px;display:flex;align-items:center;justify-content:center;background:#bbf7d0;color:#15803d;border:none;border-radius:8px;font-size:1.125rem;font-weight:700;cursor:pointer;">&minus;</button>' +
                        '<button type="button" class="qty-plus" data-size="small" style="width:36px;height:36px;display:flex;align-items:center;justify-content:center;background:#16a34a;color:#fff;border:none;border-radius:8px;font-size:1.125rem;font-weight:700;cursor:pointer;">&plus;</button>' +
                    '</div>' +
                '</div>' +

                '<!-- 小計 + リセット -->' +
                '<div style="display:flex;align-items:center;justify-content:space-between;">' +
                    '<button type="button" class="card-reset" style="font-size:0.75rem;color:#9ca3af;background:none;border:none;cursor:pointer;">リセット</button>' +
                    '<div>' +
                        '<span style="font-size:0.75rem;color:#9ca3af;">小計: </span>' +
                        '<span class="card-subtotal" style="font-size:0.875rem;font-weight:700;color:#374151;">&yen;' + subtotal.toLocaleString() + '</span>' +
                    '</div>' +
                '</div>';

            cardsGrid.appendChild(card);
        });

        calcGrandTotal();
    }

    function calcCardSubtotal(large, regular, small, basePrice, largePriceTotal) {
        return (large * largePriceTotal) + ((regular + small) * basePrice);
    }

    function updateCardDisplay(card) {
        var idx = card.dataset.formIndex;
        var basePrice = parseInt(card.dataset.basePrice) || 0;
        var isIntegrated = (card.dataset.containerType === '一体型');
        var largePriceTotal = isIntegrated ? basePrice : basePrice + largeExtraPrice;

        var qtyLarge = parseInt(card.querySelector('[name="items-' + idx + '-quantity_large"]').value) || 0;
        var qtyRegular = parseInt(card.querySelector('[name="items-' + idx + '-quantity_regular"]').value) || 0;
        var qtySmall = parseInt(card.querySelector('[name="items-' + idx + '-quantity_small"]').value) || 0;
        var totalQty = qtyLarge + qtyRegular + qtySmall;

        // Update displays
        card.querySelector('.qty-large-display').textContent = qtyLarge;
        card.querySelector('.qty-regular-display').textContent = qtyRegular;
        card.querySelector('.qty-small-display').textContent = qtySmall;

        // Update hidden fields
        card.querySelector('[name="items-' + idx + '-quantity"]').value = totalQty;
        var subtotal = calcCardSubtotal(qtyLarge, qtyRegular, qtySmall, basePrice, largePriceTotal);
        card.querySelector('[name="items-' + idx + '-subtotal"]').value = subtotal;

        // Update subtotal display
        card.querySelector('.card-subtotal').textContent = '\u00a5' + subtotal.toLocaleString();

        // Highlight card if has quantities
        if (totalQty > 0) {
            card.classList.remove('border-gray-200');
            card.classList.add('border-blue-400', 'bg-blue-50');
        } else {
            card.classList.remove('border-blue-400', 'bg-blue-50');
            card.classList.add('border-gray-200');
        }

        calcGrandTotal();
    }

    // --- イベント: +/- ボタン ---
    cardsGrid.addEventListener('click', function(e) {
        var btn = e.target.closest('.qty-plus, .qty-minus');
        if (btn) {
            var card = btn.closest('.product-card');
            var idx = card.dataset.formIndex;
            var size = btn.dataset.size;
            var fieldName = 'items-' + idx + '-quantity_' + size;
            var field = card.querySelector('[name="' + fieldName + '"]');
            var current = parseInt(field.value) || 0;

            if (btn.classList.contains('qty-plus')) {
                field.value = current + 1;
            } else if (btn.classList.contains('qty-minus') && current > 0) {
                field.value = current - 1;
            }

            updateCardDisplay(card);
            return;
        }

        // Reset button
        var resetBtn = e.target.closest('.card-reset');
        if (resetBtn) {
            var card = resetBtn.closest('.product-card');
            var idx = card.dataset.formIndex;
            card.querySelector('[name="items-' + idx + '-quantity_large"]').value = 0;
            card.querySelector('[name="items-' + idx + '-quantity_regular"]').value = 0;
            card.querySelector('[name="items-' + idx + '-quantity_small"]').value = 0;
            updateCardDisplay(card);
        }
    });

    // --- 合計計算 ---
    function calcGrandTotal() {
        var total = 0;
        var totalMeals = 0;
        document.querySelectorAll('.product-card').forEach(function(card) {
            var idx = card.dataset.formIndex;
            var subtotalVal = parseInt(card.querySelector('[name="items-' + idx + '-subtotal"]').value) || 0;
            var qtyVal = parseInt(card.querySelector('[name="items-' + idx + '-quantity"]').value) || 0;
            total += subtotalVal;
            totalMeals += qtyVal;
        });
        grandTotalEl.textContent = '\u00a5' + total.toLocaleString();
        document.getElementById('grand-total-meals').textContent = totalMeals + '食';
    }

    // --- 顧客変更時に全カードの価格を更新 ---
    function updateAllCardPrices() {
        // No.11の価格を再計算
        var no11 = currentProducts.find(function(p) { return p.no === 11; });
        if (no11) {
            var priceKey = 'price_' + currentPriceType;
            largeExtraPrice = no11[priceKey] || 0;
        } else {
            largeExtraPrice = 0;
        }

        document.querySelectorAll('.product-card').forEach(function(card) {
            var productId = card.dataset.productId;
            var product = currentProducts.find(function(p) { return String(p.id) === String(productId); });
            if (!product) return;

            var priceKey = 'price_' + currentPriceType;
            var basePrice = product[priceKey] || 0;
            var isIntegrated = (product.container_type === '一体型');
            var largePriceTotal = isIntegrated ? basePrice : basePrice + largeExtraPrice;
            var idx = card.dataset.formIndex;

            card.dataset.basePrice = basePrice;
            card.querySelector('[name="items-' + idx + '-unit_price"]').value = basePrice;

            // Update price displays in card
            var priceDisplays = card.querySelectorAll('.text-xs.text-gray-400');
            if (priceDisplays[0]) priceDisplays[0].innerHTML = '&yen;' + largePriceTotal.toLocaleString();
            if (priceDisplays[1]) priceDisplays[1].innerHTML = '&yen;' + basePrice.toLocaleString();
            if (priceDisplays[2]) priceDisplays[2].innerHTML = '&yen;' + basePrice.toLocaleString();

            // Update header price
            var headerPrice = card.querySelector('.text-sm.text-gray-500');
            if (headerPrice) headerPrice.innerHTML = '単価 &yen;' + basePrice.toLocaleString();

            updateCardDisplay(card);
        });
    }

    // --- 納品日変更 ---
    if (deliveryDateInput) {
        deliveryDateInput.addEventListener('change', function() {
            fetchProductsForDate(this.value);
        });
        if (deliveryDateInput.value) {
            fetchProductsForDate(deliveryDateInput.value);
        }
    }

    // --- HTML escape ---
    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
});
