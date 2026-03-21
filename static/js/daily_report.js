// daily_report.js - 日計表フォーム専用のJavaScript

// デバウンスユーティリティ
function debounce(func, wait) {
    var timeout;
    return function() {
        var context = this, args = arguments;
        clearTimeout(timeout);
        timeout = setTimeout(function() {
            func.apply(context, args);
        }, wait);
    };
}

// その他の売上用: 項目選択時に単価を更新
function updatePrice(element, type) {
    var price = element.getAttribute('data-price' + type);
    document.getElementById('others_price' + type).value = price;
}

// 新横浜担当者の切替処理
function switchPersonField(locationValue, preselectedPerson) {
    var container = document.querySelector('.container[data-shin-yokohama-url]');
    var shinYokohamaUrl = container ? container.getAttribute('data-shin-yokohama-url') : '';
    var personDefault = document.getElementById('person-default');
    var personSelect = document.getElementById('person-select');
    var selectEl = document.getElementById('担当者_select');

    if (locationValue === '新横浜') {
        // ローディング表示
        selectEl.innerHTML = '<option>読み込み中...</option>';
        personDefault.style.display = 'none';
        personSelect.style.display = '';

        fetch(shinYokohamaUrl)
            .then(function(response) { return response.json(); })
            .then(function(data) {
                if (data.length === 0) {
                    showDefaultPerson();
                    return;
                }
                // 担当者セレクトを表示
                personDefault.style.display = 'none';
                personSelect.style.display = '';
                selectEl.name = '担当者';
                // デフォルト側のname属性を無効化
                var hiddenField = document.getElementById('担当者_hidden');
                if (hiddenField) hiddenField.removeAttribute('name');
                var inputField = document.getElementById('担当者_input');
                if (inputField) inputField.removeAttribute('name');

                if (data.length === 1) {
                    selectEl.innerHTML = '';
                    var option = document.createElement('option');
                    option.value = data[0].name;
                    option.textContent = data[0].name;
                    selectEl.appendChild(option);
                } else {
                    selectEl.innerHTML = '<option value="">-- 担当者を選択 --</option>';
                    data.forEach(function(user) {
                        var option = document.createElement('option');
                        option.value = user.name;
                        option.textContent = user.name;
                        selectEl.appendChild(option);
                    });
                }
                // POST後の再表示時に以前選択した担当者を復元
                if (preselectedPerson) {
                    selectEl.value = preselectedPerson;
                }
            });
    } else {
        showDefaultPerson();
    }
}

function showDefaultPerson() {
    var personDefault = document.getElementById('person-default');
    var personSelect = document.getElementById('person-select');
    var selectEl = document.getElementById('担当者_select');
    personDefault.style.display = '';
    personSelect.style.display = 'none';
    selectEl.removeAttribute('name');
    var hiddenField = document.getElementById('担当者_hidden');
    if (hiddenField) hiddenField.name = '担当者';
    var inputField = document.getElementById('担当者_input');
    if (inputField) inputField.name = '担当者';
}

// 閉店時間メッセージ更新
function updateClosingTimeMessage() {
    var closingTimeEl = document.getElementById('closing_time');
    var messageElement = document.getElementById('closing-time-message');
    if (!closingTimeEl || !messageElement) return;
    messageElement.textContent = closingTimeEl.value ? 'は入力されています。' : 'を入力してください。';
}

// 送信ボタンの表示切替
function toggleSubmitButton() {
    var closingTimeEl = document.getElementById('closing_time');
    var locationCheckEl = document.getElementById('location_check');
    var submitButton = document.getElementById('submit-button');
    if (!closingTimeEl || !locationCheckEl || !submitButton) return;

    if (closingTimeEl.value && locationCheckEl.checked) {
        submitButton.classList.remove('btn-submit-hidden');
    } else {
        submitButton.classList.add('btn-submit-hidden');
    }
}

// 自動保存（localStorage）
var autoSaveTimer = null;

function getAutoSaveKey() {
    var dateEl = document.getElementById('date');
    var locationEl = document.getElementById('location');
    if (!dateEl || !locationEl) return null;
    var dateVal = dateEl.value || 'nodate';
    var locationVal = locationEl.value || 'nolocation';
    return 'daily_report_draft_' + dateVal + '_' + locationVal;
}

function hasMeaningfulData(form) {
    // 販売数、PayPay、電子決済、現金のいずれかが0以外なら有意なデータあり
    var fields = ['paypay', 'digital_payment', 'cash'];
    for (var i = 0; i < fields.length; i++) {
        var el = document.getElementById(fields[i]);
        if (el && parseInt(el.value, 10) > 0) return true;
    }
    // 残数が持参数と異なれば入力済みとみなす
    var remainingInputs = form.querySelectorAll('[id^="remaining_input_"]');
    for (var j = 0; j < remainingInputs.length; j++) {
        var menuNo = remainingInputs[j].id.replace('remaining_input_', '');
        var quantityEl = document.getElementById('quantity_' + menuNo);
        var quantity = quantityEl ? parseInt(quantityEl.value, 10) || 0 : 0;
        var remaining = parseInt(remainingInputs[j].value, 10) || 0;
        if (remaining !== quantity) return true;
    }
    return false;
}

function autoSaveForm() {
    var form = document.querySelector('form');
    if (!form) return;
    var key = getAutoSaveKey();
    if (!key) return;

    // 有意なデータがない場合は保存しない
    if (!hasMeaningfulData(form)) return;

    var formData = new FormData(form);
    var data = {};
    formData.forEach(function(value, fieldName) {
        // CSRFトークンは保存しない
        if (fieldName === 'csrfmiddlewaretoken') return;
        data[fieldName] = value;
    });

    // チェックボックスの状態も保存
    form.querySelectorAll('input[type="checkbox"]').forEach(function(cb) {
        data['__cb_' + cb.name] = cb.checked ? '1' : '0';
    });

    try {
        localStorage.setItem(key, JSON.stringify(data));
    } catch(e) {
        // localStorageがいっぱいの場合は無視
    }
}

function restoreDraft() {
    var key = getAutoSaveKey();
    if (!key) return;

    var saved;
    try {
        saved = localStorage.getItem(key);
    } catch(e) {
        return;
    }
    if (!saved) return;

    var data;
    try {
        data = JSON.parse(saved);
    } catch(e) {
        return;
    }

    // 保存データに有意な値があるかチェック
    var hasMeaningful = false;
    var checkFields = ['paypay', 'digital_payment', 'cash'];
    for (var i = 0; i < checkFields.length; i++) {
        if (data[checkFields[i]] && parseInt(data[checkFields[i]], 10) > 0) {
            hasMeaningful = true;
            break;
        }
    }
    if (!hasMeaningful) {
        Object.keys(data).forEach(function(k) {
            if (k.match(/^remaining_/) && parseInt(data[k], 10) >= 0) {
                // 残数が保存されていれば有意なデータあり
                hasMeaningful = true;
            }
        });
    }
    if (!hasMeaningful) {
        localStorage.removeItem(key);
        return;
    }

    if (!confirm('前回の入力データが保存されています。復元しますか？')) {
        localStorage.removeItem(key);
        return;
    }

    var form = document.querySelector('form');
    if (!form) return;

    Object.keys(data).forEach(function(fieldName) {
        if (fieldName.startsWith('__cb_')) {
            // チェックボックスの復元
            var cbName = fieldName.substring(5);
            var cb = form.querySelector('input[type="checkbox"][name="' + cbName + '"]');
            if (cb) cb.checked = data[fieldName] === '1';
            return;
        }

        var el = form.querySelector('[name="' + fieldName + '"]');
        if (el && el.type !== 'hidden') {
            el.value = data[fieldName];
        }
    });

    // 残数入力フィールドの復元後に再計算
    document.querySelectorAll('[id^="remaining_input_"]').forEach(function(input) {
        if (typeof updateFromRemaining === 'function') {
            updateFromRemaining(input);
        }
    });
}

function clearDraft() {
    var key = getAutoSaveKey();
    if (key) {
        try { localStorage.removeItem(key); } catch(e) {}
    }
}

function setupAutoSave() {
    var form = document.querySelector('form');
    if (!form) return;

    var debouncedSave = debounce(autoSaveForm, 2000);

    form.addEventListener('input', debouncedSave);
    form.addEventListener('change', debouncedSave);

    // 送信成功時にドラフト削除
    form.addEventListener('submit', function() {
        clearDraft();
    });

    // ページ離脱時の警告
    window.addEventListener('beforeunload', function(e) {
        var key = getAutoSaveKey();
        if (!key) return;
        // 最終保存
        autoSaveForm();
    });
}


// DOMContentLoadedで初期化
document.addEventListener('DOMContentLoaded', function() {
    // 販売所変更時の担当者切替
    var locationEl = document.getElementById('location');
    if (locationEl) {
        locationEl.addEventListener('change', function() {
            switchPersonField(this.value, null);
        });

        // POST後に新横浜が選択された状態なら担当者セレクトを復元
        if (locationEl.value === '新横浜') {
            var container = document.querySelector('.container[data-selected-person]');
            var savedPerson = container ? container.getAttribute('data-selected-person') : '';
            switchPersonField('新横浜', savedPerson);
        }
    }

    // 閉店時間メッセージ
    var closingTimeEl = document.getElementById('closing_time');
    if (closingTimeEl) {
        closingTimeEl.addEventListener('input', updateClosingTimeMessage);
        updateClosingTimeMessage();
    }

    // ドロップダウン選択時の処理
    document.querySelectorAll('.dropdown-menu').forEach(function(dropdown) {
        dropdown.querySelectorAll('.dropdown-item').forEach(function(item) {
            item.addEventListener('click', function(event) {
                event.preventDefault();
                var selectedValue = this.getAttribute('data-value');
                var dropdownButton = this.closest('.dropdown').querySelector('.dropdown-toggle');
                dropdownButton.textContent = selectedValue;

                if (dropdownButton.id === 'dropdownMenuButton1') {
                    document.getElementById('selected_item_1').value = selectedValue;
                } else if (dropdownButton.id === 'dropdownMenuButton2') {
                    document.getElementById('selected_item_2').value = selectedValue;
                }
            });
        });
    });

    // 送信ボタン表示切替
    toggleSubmitButton();
    if (closingTimeEl) {
        closingTimeEl.addEventListener('input', toggleSubmitButton);
    }
    var locationCheckEl = document.getElementById('location_check');
    if (locationCheckEl) {
        locationCheckEl.addEventListener('change', toggleSubmitButton);
    }

    // calculateTotalsとupdateRevenueをデバウンス化（低スペック端末対策）
    if (typeof calculateTotals === 'function') {
        var originalCalculateTotals = calculateTotals;
        calculateTotals = debounce(originalCalculateTotals, 150);
        // 初期計算は即時実行
        originalCalculateTotals();
    }

    // 自動保存のセットアップ
    setupAutoSave();

    // データ表示後にドラフト復元を試みる
    var bentoSection = document.getElementById('bento-section');
    if (bentoSection) {
        restoreDraft();
    }
});
