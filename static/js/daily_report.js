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

// 通信失敗を画面上部に数秒表示する（fetch失敗時にUIが無反応で固まるのを防ぐ）
function notifyFetchError(message) {
    var banner = document.createElement('div');
    banner.textContent = message || '通信に失敗しました。電波の良い場所でもう一度お試しください。';
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;' +
        'background:#c0392b;color:#fff;padding:12px;text-align:center;' +
        'font-size:14px;box-shadow:0 2px 6px rgba(0,0,0,0.3);';
    document.body.appendChild(banner);
    setTimeout(function() { if (banner.parentNode) banner.parentNode.removeChild(banner); }, 6000);
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
            })
            .catch(function() {
                // 通信失敗時は「読み込み中...」のまま固まらせない
                selectEl.innerHTML = '<option>読み込みに失敗しました</option>';
                notifyFetchError();
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

// 時間フィールドの順序チェック
function validateTimeOrder() {
    var fields = [
        {id: 'departure_time', label: '出発時間'},
        {id: 'arrival_time', label: '到着時間'},
        {id: 'opening_time', label: '開店時間'},
        {id: 'closing_time', label: '閉店時間'}
    ];
    var prev = null;
    var prevLabel = '';
    for (var i = 0; i < fields.length; i++) {
        var el = document.getElementById(fields[i].id);
        if (!el || !el.value || el.value === '00:00') continue;
        var current = el.value;
        if (prev && current < prev) {
            return fields[i].label + 'が' + prevLabel + 'より前の時間になっています。よろしいですか？';
        }
        prev = current;
        prevLabel = fields[i].label;
    }
    return null;
}

// 「データを表示」のあとで日付・販売所を変えたか。変えたまま送るとクーポン欄の値段が食い違うので止める
function isStaleSelection() {
    var form = document.querySelector('form[data-shown-date]');
    if (!form) return false;
    var dateEl = document.getElementById('date');
    var locationEl = document.getElementById('location');
    return (dateEl && dateEl.value !== form.getAttribute('data-shown-date')) ||
           (locationEl && locationEl.value !== form.getAttribute('data-shown-location'));
}

function updateStaleWarning() {
    var stale = isStaleSelection();
    ['stale-date-warning', 'stale-location-warning'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.classList.toggle('d-none', !stale);
    });
}

// 全部売り切れた（残数0）のに完売時間が空。完売の早さが食数の判断に要るので、完売した日は必須
function needsSoldOutTime() {
    if (!document.getElementById('sold_out_time') || !document.querySelector('[id^="remaining_input_"]')) return false;
    var t = bentoTotals();
    return t.remaining === 0 && t.sold > 0 && !soldOutTime();
}

function updateSoldOutTimeMessage() {
    var msg = document.getElementById('sold-out-time-required');
    if (msg) msg.classList.toggle('d-none', !needsSoldOutTime());
}

// 未入力項目を優先順（日付・販売所の変更 → 現金 → 完売時間（完売した日） → 閉店時間 → 気づいたこと → 確認チェック）で1件返す。全部OKならnull
function getFirstIncomplete() {
    if (isStaleSelection()) {
        return { el: document.getElementById('display-data-btn'), msg: '日付か販売所が変わっています。「データを表示」を押し直してください' };
    }
    var cashEl = document.getElementById('cash');
    // 現金：欄が存在し、対象外(disabled)でなく、空欄のときだけ未入力扱い（0の入力はOK＝違算許容）
    if (cashEl && !cashEl.disabled && cashEl.value.trim() === '') {
        return { el: cashEl, msg: '現金が未入力です' };
    }
    if (needsSoldOutTime()) {
        return { el: document.getElementById('sold_out_time'), msg: '完売時間が未入力です' };
    }
    var closingEl = document.getElementById('closing_time');
    if (closingEl && !closingEl.value) {
        return { el: closingEl, msg: '閉店時間が未入力です' };
    }
    if (currentUnusual && !unusualAnswered()) {
        return { el: document.getElementById('unusual-block'), msg: '「気づいたこと」を1つ選んでください', unusual: true };
    }
    var checkEl = document.getElementById('location_check');
    if (checkEl && !checkEl.checked) {
        return { el: checkEl, msg: '確認チェックが未完了です' };
    }
    return null;
}

// 送信ボタンの見た目（グレー=未完了 / 緑=送信可）を更新
function updateSubmitButtonState() {
    var submitButton = document.getElementById('submit-button');
    if (!submitButton) return;
    if (getFirstIncomplete()) {
        submitButton.classList.add('btn-incomplete');
    } else {
        submitButton.classList.remove('btn-incomplete');
    }
}

// 現金欄の赤枠・「未入力」表示を更新
function updateCashFieldState() {
    var cashEl = document.getElementById('cash');
    if (!cashEl || cashEl.disabled) return;
    var msgEl = document.getElementById('cash-required-msg');
    if (cashEl.value.trim() === '') {
        cashEl.classList.add('cash-empty');
        if (msgEl) msgEl.classList.remove('hidden');
    } else {
        cashEl.classList.remove('cash-empty');
        if (msgEl) msgEl.classList.add('hidden');
    }
}

// 未入力項目へスクロール＋数秒で消えるポップを表示（非モーダル・消すタップ不要）
function scrollToAndPop(target) {
    if (!target || !target.el) return;
    var el = target.el;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' });
    el.classList.add('field-flash');
    setTimeout(function() { el.classList.remove('field-flash'); }, 2500);
    try { el.focus({ preventScroll: true }); } catch (e) { if (el.focus) el.focus(); }

    var old = document.querySelector('.field-pop');
    if (old && old.parentNode) old.parentNode.removeChild(old);

    // ポップは「画面（ビューポート）基準」で表示する。
    // scrollIntoView は smooth で非同期のため、要素の座標基準で置くと
    // スクロール完了前の（画面外の）座標になり見えなくなる。
    var pop = document.createElement('div');
    pop.className = 'field-pop';
    pop.textContent = target.msg;
    // block:'center' で対象が画面中央に来るので、その少し上に固定表示
    pop.style.top = '34%';
    pop.style.left = '50%';
    pop.style.transform = 'translate(-50%, -50%)';
    document.body.appendChild(pop);
    setTimeout(function() { if (pop.parentNode) pop.parentNode.removeChild(pop); }, 2800);
}

// フォーム送信ガード（グレーボタンのタップ・Enter 送信の両方をここで止める）
// テンプレの onsubmit="return confirmSubmission(event)" から呼ばれる
function confirmSubmission(event) {
    // 「データを表示」は未入力チェックの対象外（日付・販売所を変えたあとに押し直せるように）
    if (event && event.submitter && event.submitter.value === 'display') {
        return true;
    }
    updateUnusualBlock();
    var incomplete = getFirstIncomplete();
    if (incomplete) {
        if (incomplete.unusual) {
            unusualAttempted = true;
            updateUnusualMarks();
        }
        if (event) {
            event.preventDefault();
            // 二重送信防止のスピナー等（他のsubmitリスナー）も止める
            if (event.stopImmediatePropagation) event.stopImmediatePropagation();
        }
        scrollToAndPop(incomplete);
        return false;
    }
    return true;
}

// 日計表のフォーム。入力欄の多くは閉じタグの都合でフォームの「中」に入っていない（持ち主としてだけ属する）ので、
// 探すときは form.elements を使い、イベントは document で受ける。document.querySelector('form') はログアウトのフォーム
function reportForm() {
    var btn = document.getElementById('submit-button') || document.getElementById('display-data-btn');
    return btn ? btn.form : null;
}

function namedFields(form, name) {
    return Array.prototype.filter.call(form.elements, function(el) { return el.name === name; });
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
    var remainingInputs = document.querySelectorAll('[id^="remaining_input_"]');
    for (var j = 0; j < remainingInputs.length; j++) {
        var menuNo = remainingInputs[j].id.replace('remaining_input_', '');
        var quantityEl = document.getElementById('quantity_' + menuNo);
        var quantity = quantityEl ? parseInt(quantityEl.value, 10) || 0 : 0;
        var remaining = parseInt(remainingInputs[j].value, 10) || 0;
        if (remaining !== quantity) return true;
    }
    return false;
}

// manual=true は「一時保存」ボタン。持参数の訂正だけでも残せるよう、入力の有無を問わず保存する
function autoSaveForm(manual) {
    var form = reportForm();
    if (!form) return false;
    var key = getAutoSaveKey();
    if (!key) return false;

    var wasManual = false;
    try { wasManual = !!(JSON.parse(localStorage.getItem(key) || '{}').__manual); } catch (e) {}
    // 有意なデータがない場合は保存しない（一時保存したものは、その後の自動保存でも残す）
    if (manual !== true && !wasManual && !hasMeaningfulData(form)) return false;

    var formData = new FormData(form);
    // 同じ名前の欄（選択肢ボタン・足した行）があるので、値は名前ごとの一覧で持つ
    var data = { __v: 2 };
    formData.forEach(function(value, fieldName) {
        // CSRFトークンは保存しない
        if (fieldName === 'csrfmiddlewaretoken') return;
        (data[fieldName] = data[fieldName] || []).push(value);
    });

    // 1つだけのチェックボックスは、外した状態も保存
    Array.prototype.filter.call(form.elements, function(el) { return el.type === 'checkbox'; }).forEach(function(cb) {
        if (!cb.name || cb.name.indexOf('reasons_') === 0) return;
        data['__cb_' + cb.name] = cb.checked ? '1' : '0';
    });
    // 理由の選択肢は、隠れている間（送らない間）の分も残す
    document.querySelectorAll('input[type="checkbox"][name^="reasons_"]:checked').forEach(function(cb) {
        (data['__reasons'] = data['__reasons'] || []).push(cb.name + '\t' + cb.value);
    });
    data.__manual = manual === true || wasManual;
    data.__savedAt = new Date().toISOString();
    data.__planRows = document.querySelectorAll('#plan-rows .dr-plan-row').length;
    data.__eventRows = document.querySelectorAll('#event-rows .dr-event-row').length;

    try {
        localStorage.setItem(key, JSON.stringify(data));
    } catch(e) {
        // localStorageがいっぱいの場合は無視
        return false;
    }
    return true;
}

function savedAtLabel(iso) {
    var t = iso ? new Date(iso) : null;
    if (!t || isNaN(t)) return '';
    return (t.getMonth() + 1) + '/' + t.getDate() + ' ' + t.getHours() + ':' + ('0' + t.getMinutes()).slice(-2);
}

function tempSave() {
    var ok = autoSaveForm(true);
    var t = new Date();
    var msg = ok ? t.getHours() + ':' + ('0' + t.getMinutes()).slice(-2) + ' にこの端末へ一時保存しました'
                 : '一時保存できませんでした';
    document.querySelectorAll('.js-temp-save-msg').forEach(function(el) { el.textContent = msg; });
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
    var first = function(v) { return Array.isArray(v) ? v[0] : v; };
    for (var i = 0; i < checkFields.length; i++) {
        if (data[checkFields[i]] && parseInt(first(data[checkFields[i]]), 10) > 0) {
            hasMeaningful = true;
            break;
        }
    }
    if (!hasMeaningful) {
        Object.keys(data).forEach(function(k) {
            if (k.match(/^remaining_/) && parseInt(first(data[k]), 10) >= 0) {
                // 残数が保存されていれば有意なデータあり
                hasMeaningful = true;
            }
        });
    }
    if (!hasMeaningful && !data.__manual) {
        localStorage.removeItem(key);
        return;
    }

    var when = savedAtLabel(data.__savedAt);
    var question = data.__manual
        ? (when ? when + ' に一時保存した入力があります。続きから入力しますか？' : '一時保存した入力があります。続きから入力しますか？')
        : '前回の入力データが保存されています。復元しますか？';
    if (!confirm(question)) {
        localStorage.removeItem(key);
        return;
    }

    var form = reportForm();
    if (!form) return;

    // 足した行を先に作ってから値を戻す
    ensureRows('plan', data.__planRows || 0);
    ensureRows('event', data.__eventRows || 0);

    Object.keys(data).forEach(function(fieldName) {
        if (fieldName.startsWith('__cb_')) {
            // チェックボックスの復元
            var cbName = fieldName.substring(5);
            var cb = namedFields(form, cbName).filter(function(el) { return el.type === 'checkbox'; })[0];
            if (cb) cb.checked = data[fieldName] === '1';
            return;
        }
        if (fieldName.startsWith('__')) return;

        var values = Array.isArray(data[fieldName]) ? data[fieldName] : [data[fieldName]];
        var els = namedFields(form, fieldName);
        if (!els.length) return;
        if (els[0].type === 'radio' || els[0].type === 'checkbox') {
            // value を書き換えず、同じ値のボタンに印を付ける（書き換えると違う答えが送られる）
            els.forEach(function(el) { if (values.indexOf(el.value) !== -1) el.checked = true; });
            return;
        }
        els.forEach(function(el, i) {
            if (el.type !== 'hidden' && i < values.length) el.value = values[i];
        });
    });
    (data.__reasons || []).forEach(function(pair) {
        var parts = pair.split('\t');
        document.querySelectorAll('input[name="' + parts[0] + '"]').forEach(function(cb) {
            if (cb.value === parts[1]) cb.checked = true;
        });
    });
    // 残数の欄には name が無いので、送る用の remaining_N（hidden）の値から戻す
    Object.keys(data).forEach(function(fieldName) {
        var m = fieldName.match(/^remaining_(\d+)$/);
        if (!m) return;
        var input = document.getElementById('remaining_input_' + m[1]);
        var v = Array.isArray(data[fieldName]) ? data[fieldName][0] : data[fieldName];
        if (input) input.value = v;
    });
    refreshPlanRows();
    syncRadioMarks();

    // 残数入力フィールドの復元後に再計算
    document.querySelectorAll('[id^="remaining_input_"]').forEach(function(input) {
        if (typeof updateFromRemaining === 'function') {
            updateFromRemaining(input);
        }
    });

    // 復元後に現金欄の赤枠・判定・送信ボタン状態も反映
    updateCashFieldState();
    updateUnusualBlock();
}

function clearDraft() {
    var key = getAutoSaveKey();
    if (key) {
        try { localStorage.removeItem(key); } catch(e) {}
    }
}

function setupAutoSave() {
    var form = reportForm();
    if (!form) return;

    var debouncedSave = debounce(autoSaveForm, 2000);

    document.addEventListener('input', debouncedSave);
    document.addEventListener('change', debouncedSave);

    // 送信したときだけ消す（「データを表示」を押し直しても一時保存は残す）
    form.addEventListener('submit', function(e) {
        if (e.submitter && e.submitter.value === 'send') clearDraft();
    });
    document.querySelectorAll('.js-temp-save').forEach(function(btn) {
        btn.addEventListener('click', tempSave);
    });

    // ページ離脱時の警告
    window.addEventListener('beforeunload', function(e) {
        var key = getAutoSaveKey();
        if (!key) return;
        // 最終保存
        autoSaveForm();
    });
}


// 二重送信防止
function preventDoubleSubmit() {
    var form = document.querySelector('form');
    if (!form) return;
    form.addEventListener('submit', function() {
        var buttons = form.querySelectorAll('button[type="submit"]');
        buttons.forEach(function(btn) {
            // 送信中はボタンを無効化してスピナー表示
            if (btn.value === 'send' || btn.value === 'display') {
                btn.disabled = true;
                var originalText = btn.textContent;
                btn.setAttribute('data-original-text', originalText);
                btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>処理中...';
                // 5秒後にタイムアウトで復元（ネットワークエラー等に対応）
                setTimeout(function() {
                    btn.disabled = false;
                    btn.textContent = btn.getAttribute('data-original-text') || originalText;
                }, 5000);
            }
        });
    });
}

// DOMContentLoadedで初期化
document.addEventListener('DOMContentLoaded', function() {
    // 二重送信防止
    preventDoubleSubmit();
    // 販売所変更時の担当者切替
    var locationEl = document.getElementById('location');
    if (locationEl) {
        locationEl.addEventListener('change', function() {
            switchPersonField(this.value, null);
            updateStaleWarning();
            updateSubmitButtonState();
        });
        var dateSelectEl = document.getElementById('date');
        if (dateSelectEl) {
            dateSelectEl.addEventListener('change', function() {
                updateStaleWarning();
                updateSubmitButtonState();
            });
        }

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

    // 送信ボタンの状態（グレー/緑）と現金欄の赤枠を初期化＋各入力で更新
    updateCashFieldState();
    updateSubmitButtonState();
    var cashEl = document.getElementById('cash');
    if (cashEl) {
        cashEl.addEventListener('input', function() {
            updateCashFieldState();
            updateSubmitButtonState();
        });
    }
    if (closingTimeEl) {
        closingTimeEl.addEventListener('input', updateSubmitButtonState);
    }
    var locationCheckEl = document.getElementById('location_check');
    if (locationCheckEl) {
        locationCheckEl.addEventListener('change', updateSubmitButtonState);
    }

    // calculateTotalsとupdateRevenueをデバウンス化（低スペック端末対策）
    if (typeof calculateTotals === 'function') {
        var originalCalculateTotals = calculateTotals;
        calculateTotals = debounce(originalCalculateTotals, 150);
        // 初期計算は即時実行
        originalCalculateTotals();
    }

    setupSituation();

    // 自動保存のセットアップ
    setupAutoSave();

    // データ表示後にドラフト復元を試みる
    var bentoSection = document.getElementById('bento-section');
    if (bentoSection) {
        restoreDraft();
    }
});

// ===== 売場の様子（いつもと違った日の質問・明日以降の食数・予定） =====
// 判定の式はサーバー（sales/daily_questions.py の judge）と同じ。割合はサーバーが埋め込んだ値を使う
var currentUnusual = '';
var unusualAttempted = false;
var drqCache;

function drqConfig() {
    if (drqCache === undefined) {
        var el = document.getElementById('dr-questions');
        try { drqCache = el ? JSON.parse(el.textContent) : null; } catch (e) { drqCache = null; }
    }
    return drqCache;
}

// 合計は大盛り（No.11）を除く（サーバーの合計と同じ）
function bentoTotals() {
    var sold = 0, remaining = 0;
    document.querySelectorAll('[id^="remaining_input_"]').forEach(function(input) {
        var no = input.id.replace('remaining_input_', '');
        if (no === '11') return;
        var qEl = document.getElementById('quantity_' + no);
        var q = qEl ? parseInt(qEl.value, 10) || 0 : 0;
        var r = parseInt(input.value, 10) || 0;
        remaining += r;
        sold += Math.max(0, q - r);
    });
    return { sold: sold, remaining: remaining };
}

// 時間の欄の初期値 00:00 は「入っていない」
function soldOutTime() {
    var el = document.getElementById('sold_out_time');
    var v = el && el.value ? el.value.slice(0, 5) : '';
    return v === '00:00' ? '' : v;
}

function judgeUnusual() {
    var c = drqConfig();
    var closing = document.getElementById('closing_time');
    // 閉店時間が入るまでは判定しない（残数を入れる前に「売れなかった」と出ないように）
    if (!c || !c.group || !closing || !closing.value) return '';
    var t = bentoTotals();
    var so = soldOutTime();
    if (so && c.earlyBefore && so < c.earlyBefore) return 'early';
    if (c.usual && t.remaining > 0 && t.sold <= c.usual * c.threshold) return 'low';
    return '';
}

function unusualAnswered() {
    var fs = document.querySelector('#unusual-block fieldset[data-kind="' + currentUnusual + '"]');
    return !!(fs && fs.querySelector('input:checked'));
}

function updateUnusualBlock() {
    var block = document.getElementById('unusual-block');
    currentUnusual = block ? judgeUnusual() : '';
    if (block) {
        block.classList.toggle('d-none', !currentUnusual);
        // 隠れている間は送らない（選んだ内容は残しておき、また出たら戻る）
        block.querySelectorAll('fieldset[data-kind]').forEach(function(fs) {
            var on = fs.getAttribute('data-kind') === currentUnusual;
            fs.classList.toggle('d-none', !on);
            fs.disabled = !on;
        });
        var note = document.getElementById('unusual_note');
        if (note) note.disabled = !currentUnusual;
        var c = drqConfig() || {};
        var heading = document.getElementById('unusual-heading');
        var detail = document.getElementById('unusual-detail');
        if (currentUnusual === 'low') {
            // 「売れなかった」と言い切らず聞く形にする（販売員を評価している感じを出さない）
            heading.textContent = 'いつもと違うことはありましたか？';
            detail.textContent = '今日' + bentoTotals().sold + '食（平均' + (c.avg || c.usual) + '食）';
        } else if (currentUnusual === 'early') {
            // 基準の時刻は出さない（質問を避けようと完売時間をずらして入れないように）
            heading.textContent = 'いつもと違うことはありましたか？';
            detail.textContent = '今日は早めに売り切れました';
        }
    }
    updateLostCustomers();
    updateUnusualMarks();
    updateSoldOutTimeMessage();
    updateSubmitButtonState();
}

// 赤い文字・チェック事項③・下のナビの目印
function updateUnusualMarks() {
    var waiting = !!currentUnusual && !unusualAnswered();
    var req = document.getElementById('unusual-required');
    if (req) req.classList.toggle('d-none', !(waiting && unusualAttempted));
    var check = document.getElementById('check-unusual');
    if (check) {
        check.classList.toggle('d-none', !currentUnusual);
        var msg = document.getElementById('check-unusual-msg');
        if (msg) msg.textContent = waiting ? 'を選んでください。' : 'は選ばれています。';
    }
    var nav = document.getElementById('nav-situation');
    if (nav) {
        var dot = nav.querySelector('.dr-nav-dot');
        if (dot) dot.classList.toggle('d-none', !waiting);
        nav.setAttribute('aria-label', waiting ? '様子（答えていない質問があります）' : '様子');
    }
}

function updateLostCustomers() {
    var block = document.getElementById('lost-customers-block');
    if (!block) return;
    var t = bentoTotals();
    var on = (t.remaining === 0 && t.sold > 0) || !!soldOutTime();
    block.classList.toggle('d-none', !on);
    var input = document.getElementById('lost_customers');
    if (input) input.disabled = !on;
}

// 1つ選ぶボタンは、選んでいるものをもう一度押すと外れる（押し間違えを直せるように）
function syncRadioMarks() {
    document.querySelectorAll('input.btn-check[type="radio"]').forEach(function(r) {
        r.setAttribute('data-was', r.checked ? '1' : '0');
    });
}

function planDateLabel(v) {
    var c = drqConfig() || {};
    var hit = (c.planDates || []).filter(function(d) { return d.v === v; })[0];
    return hit ? hit.label.replace(/（.*$/, '') : v;
}

function refreshPlanRows() {
    var c = drqConfig();
    if (!c) return;
    var rows = Array.prototype.slice.call(document.querySelectorAll('#plan-rows .dr-plan-row'));
    var used = rows.map(function(r) { return r.querySelector('select').value; });
    rows.forEach(function(row, i) {
        var sel = row.querySelector('select');
        Array.prototype.forEach.call(sel.options, function(o) {
            o.disabled = o.value !== sel.value && used.indexOf(o.value) !== -1;
        });
        var del = row.querySelector('.dr-row-del');
        del.classList.toggle('d-none', i === 0);
        del.setAttribute('aria-label', planDateLabel(sel.value) + 'の行を消す');
        var written = row.querySelector('.dr-plan-written');
        var text = (c.written || {})[sel.value];
        written.textContent = text || '';
        written.classList.toggle('d-none', !text);
    });
    var full = used.length >= (c.planDates || []).length;
    var add = document.getElementById('add-plan-row');
    if (add) add.disabled = full;
    var msg = document.getElementById('plan-full-msg');
    if (msg) msg.classList.toggle('d-none', !full);
}

function nextPlanDate() {
    var c = drqConfig() || {};
    var dates = (c.planDates || []).map(function(d) { return d.v; });
    var used = Array.prototype.map.call(document.querySelectorAll('#plan-rows select'), function(s) { return s.value; });
    var start = used.length ? dates.indexOf(used[used.length - 1]) + 1 : 0;
    var order = dates.slice(start).concat(dates.slice(0, start));
    return order.filter(function(v) { return used.indexOf(v) === -1; })[0];
}

function addPlanRow(focus) {
    var c = drqConfig();
    var tpl = document.getElementById('plan-row-template');
    var box = document.getElementById('plan-rows');
    if (!c || !tpl || !box) return null;
    var v = nextPlanDate();
    if (!v) return null;
    var row = tpl.content.firstElementChild.cloneNode(true);
    var sel = row.querySelector('select');
    c.planDates.forEach(function(d) {
        var o = document.createElement('option');
        o.value = d.v;
        o.textContent = d.label;
        sel.appendChild(o);
    });
    sel.value = v;
    attachToReportForm(row);
    box.appendChild(row);
    refreshPlanRows();
    if (focus) row.querySelector('[name="plan_qty_min"]').focus();
    return row;
}

function addEventRow(focus) {
    var c = drqConfig();
    var tpl = document.getElementById('event-row-template');
    var box = document.getElementById('event-rows');
    if (!c || !tpl || !box) return null;
    var row = tpl.content.firstElementChild.cloneNode(true);
    var date = row.querySelector('input[type="date"]');
    date.min = c.eventMin;
    date.max = c.eventMax;
    attachToReportForm(row);
    box.appendChild(row);
    if (focus) date.focus();
    return row;
}

// 足した行の欄は、HTMLの上でフォームの外に作られるので、form 属性でフォームに結び付ける（付けないと送られない）
function attachToReportForm(row) {
    var form = reportForm();
    if (!form || !form.id) return;
    row.querySelectorAll('input, select, textarea').forEach(function(el) { el.setAttribute('form', form.id); });
}

// 自動保存の復元用：行の数をそろえる
function ensureRows(kind, count) {
    var selector = kind === 'plan' ? '#plan-rows .dr-plan-row' : '#event-rows .dr-event-row';
    var guard = 0;
    while (document.querySelectorAll(selector).length < count && guard++ < 60) {
        if (!(kind === 'plan' ? addPlanRow(false) : addEventRow(false))) break;
    }
}

function setupSituation() {
    var section = document.getElementById('additional-section');
    if (!section) return;

    addPlanRow(false);

    // 判定は欄から離れたとき（1文字ごとではない）
    document.addEventListener('change', function(e) {
        var t = e.target;
        if (!t || !t.id) return;
        if (/^(remaining_input_|quantity_)/.test(t.id) || t.id === 'sold_out_time' ||
            t.id === 'closing_time' || t.id === 'sold_out_total') {
            setTimeout(updateUnusualBlock, 0);
        }
    });
    var closing = document.getElementById('closing_time');
    if (closing) closing.addEventListener('input', updateUnusualBlock);
    var soldOut = document.getElementById('sold_out_time');
    if (soldOut) soldOut.addEventListener('input', function() { updateSoldOutTimeMessage(); updateSubmitButtonState(); });

    section.addEventListener('change', function(e) {
        var t = e.target;
        if (t.name && t.name.indexOf('reasons_') === 0) {
            // 「特になし」とほかの選択肢は一緒に選べない
            var noQ = t.closest('fieldset').querySelector('.dr-choice-none');
            var noInput = noQ ? document.getElementById(noQ.getAttribute('for')) : null;
            if (t.checked && noInput) {
                t.closest('fieldset').querySelectorAll('input[type="checkbox"]').forEach(function(cb) {
                    if (t === noInput ? cb !== noInput : cb === noInput) cb.checked = false;
                });
            }
            updateUnusualMarks();
            updateSubmitButtonState();
        }
        if (t.classList.contains('dr-plan-date')) refreshPlanRows();
    });

    section.addEventListener('click', function(e) {
        var t = e.target;
        if (t.matches && t.matches('input.btn-check[type="radio"]')) {
            if (t.getAttribute('data-was') === '1') {
                t.checked = false;
                t.dispatchEvent(new Event('change', { bubbles: true }));
            }
            syncRadioMarks();
            return;
        }
        var step = t.closest('[data-lost-step]');
        if (step) {
            var input = document.getElementById('lost_customers');
            var cur = input.value === '' ? null : parseInt(input.value, 10) || 0;
            var d = parseInt(step.getAttribute('data-lost-step'), 10);
            var next = cur === null ? (d > 0 ? 1 : 0) : Math.min(99, Math.max(0, cur + d));
            input.value = next;
            input.dispatchEvent(new Event('change', { bubbles: true }));
            return;
        }
        var del = t.closest('.dr-row-del');
        if (del) {
            var row = del.closest('.dr-plan-row, .dr-event-row');
            var prev = row.previousElementSibling;
            var isPlan = row.classList.contains('dr-plan-row');
            row.parentNode.removeChild(row);
            if (isPlan) refreshPlanRows();
            var target = prev ? prev.querySelector('select, input') : document.getElementById(isPlan ? 'add-plan-row' : 'add-event-row');
            if (target) target.focus();
            document.dispatchEvent(new Event('change'));
        }
    });

    var addPlan = document.getElementById('add-plan-row');
    if (addPlan) addPlan.addEventListener('click', function() { addPlanRow(true); });
    var addEvent = document.getElementById('add-event-row');
    if (addEvent) addEvent.addEventListener('click', function() { addEventRow(true); });

    syncRadioMarks();
    updateUnusualBlock();
}
