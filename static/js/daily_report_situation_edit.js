// 日計表の編集画面「売場の様子」：行の追加・削除、1つ選ぶボタンの選び直し、「特になし」の排他
(function() {
    var root = document.getElementById('situation-edit');
    if (!root) return;
    var form = document.getElementById('report-edit-form');
    var dates = (document.getElementById('plan-rows').getAttribute('data-plan-dates') || '')
        .split(',').filter(Boolean).map(function(s) { var p = s.split('|'); return { v: p[0], label: p[1] }; });

    // 足した行は form 属性でフォームに結び付ける（閉じタグの都合でフォームの外に作られても送られるように）
    function attach(row) {
        if (!form) return;
        row.querySelectorAll('input, select').forEach(function(el) { el.setAttribute('form', form.id); });
    }

    function usedDates() {
        return Array.prototype.map.call(root.querySelectorAll('#plan-rows select'), function(s) { return s.value; });
    }

    function refresh() {
        var used = usedDates();
        root.querySelectorAll('#plan-rows select').forEach(function(sel) {
            Array.prototype.forEach.call(sel.options, function(o) {
                o.disabled = o.value !== sel.value && used.indexOf(o.value) !== -1;
            });
        });
        document.getElementById('add-plan-row').disabled = used.length >= dates.length;
    }

    document.getElementById('add-plan-row').addEventListener('click', function() {
        var used = usedDates();
        var v = dates.map(function(d) { return d.v; }).filter(function(d) { return used.indexOf(d) === -1; })[0];
        if (!v) return;
        var row = document.getElementById('plan-row-template').content.firstElementChild.cloneNode(true);
        var sel = row.querySelector('select');
        dates.forEach(function(d) {
            var o = document.createElement('option');
            o.value = d.v;
            o.textContent = d.label;
            sel.appendChild(o);
        });
        sel.value = v;
        attach(row);
        document.getElementById('plan-rows').appendChild(row);
        refresh();
        row.querySelector('[name="plan_qty_min"]').focus();
    });

    var addEvent = document.getElementById('add-event-row');
    if (addEvent) addEvent.addEventListener('click', function() {
        var box = document.getElementById('event-rows');
        var row = document.getElementById('event-row-template').content.firstElementChild.cloneNode(true);
        var date = row.querySelector('input[type="date"]');
        date.min = box.getAttribute('data-min');
        date.max = box.getAttribute('data-max');
        attach(row);
        box.appendChild(row);
        date.focus();
    });

    function syncRadioMarks() {
        root.querySelectorAll('input.btn-check[type="radio"]').forEach(function(r) {
            r.setAttribute('data-was', r.checked ? '1' : '0');
        });
    }

    root.addEventListener('click', function(e) {
        var t = e.target;
        if (t.matches && t.matches('input.btn-check[type="radio"]')) {
            if (t.getAttribute('data-was') === '1') t.checked = false;
            syncRadioMarks();
            return;
        }
        var del = t.closest && t.closest('.dr-row-del');
        if (del) {
            var row = del.closest('.dr-plan-row, .dr-event-row');
            row.parentNode.removeChild(row);
            refresh();
        }
    });

    root.addEventListener('change', function(e) {
        var t = e.target;
        if (t.name === 'reasons_edit' && t.checked) {
            var noLabel = root.querySelector('.dr-choice-none');
            var noInput = noLabel ? document.getElementById(noLabel.getAttribute('for')) : null;
            if (noInput) {
                root.querySelectorAll('input[name="reasons_edit"]').forEach(function(cb) {
                    if (t === noInput ? cb !== noInput : cb === noInput) cb.checked = false;
                });
            }
        }
        if (t.classList.contains('dr-plan-date')) refresh();
    });

    syncRadioMarks();
    refresh();
})();
