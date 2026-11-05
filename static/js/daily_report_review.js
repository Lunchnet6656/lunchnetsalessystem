/* 日計表一覧：その日の振り返りの入力モーダル。
 * モーダルはページに1個だけ。押した日付の内容を drv-data（JSON）から差し込む。
 * 出来事カードは追加した順に番号を振り、削除予定は消さずに残す（保存時に送らない）ので番号の振り直しは要らない。 */
(function () {
  'use strict';
  var modal = document.getElementById('drv-modal');
  if (!modal) return;
  var MAX = 10;
  var KIND_TONE = { stop: 'danger', absence: 'danger', production: 'warning', delivery: 'warning', reallocation: 'secondary', other: 'secondary' };
  var data = JSON.parse(document.getElementById('drv-data').textContent || '{}');
  var form = document.getElementById('drv-form');
  var list = document.getElementById('drv-events');
  var tpl = document.getElementById('drv-ev-tpl').innerHTML;
  var comment = document.getElementById('drv-comment');
  var total = document.getElementById('drv-total');
  var addBtn = document.getElementById('drv-add');
  var count = document.getElementById('drv-count');
  var delBtn = document.getElementById('drv-delete');
  var saveBtn = document.getElementById('drv-save');
  var live = document.getElementById('drv-live');
  var next = 0;

  function cards() { return Array.prototype.slice.call(list.querySelectorAll('.drv-ev')); }
  function activeCards() { return cards().filter(function (c) { return !c.classList.contains('is-deleted'); }); }
  function q(card, sel) { return card.querySelector(sel); }
  function badge(text, cls) {
    var b = document.createElement('span');
    b.className = 'badge fw-normal text-nowrap ' + cls;
    b.textContent = text;
    return b;
  }
  function scopeOf(card) { return q(card, '.drv-scope:checked').value; }
  function checkedLocs(card) { return Array.prototype.slice.call(card.querySelectorAll('.drv-loc:checked')); }

  function updateCount() {
    var n = activeCards().length;
    count.textContent = n + '/' + MAX;
    addBtn.disabled = n >= MAX;
  }

  function summarize(card) {
    var sum = q(card, '.drv-ev-sum');
    var kind = q(card, '.drv-ev-kind');
    var open = !q(card, '.drv-ev-body').hidden;
    var n = Number(card.dataset.i) + 1;
    sum.textContent = '';
    if (card.querySelector('.is-invalid, .drv-locs .invalid-feedback.d-block')) {
      var err = document.createElement('i');
      err.className = 'fas fa-exclamation-circle text-danger';
      err.setAttribute('aria-label', '入力エラー');
      sum.appendChild(err);
    }
    if (open || !kind.value) {
      sum.appendChild(document.createTextNode('出来事' + n));
    } else {
      var tone = KIND_TONE[kind.value] || 'secondary';
      sum.appendChild(badge(kind.options[kind.selectedIndex].text,
        'bg-' + tone + '-subtle text-' + tone + '-emphasis border border-' + tone + '-subtle'));
      var locs = checkedLocs(card);
      var where = document.createElement('span');
      where.className = 'text-nowrap';
      where.textContent = scopeOf(card) === 'all' ? '全店'
        : locs.length > 3 ? locs.length + '店'
        : locs.map(function (l) { return l.dataset.label; }).join('・');
      sum.appendChild(where);
      if (q(card, '.drv-ev-exclude').checked) sum.appendChild(badge('予測除外', 'text-bg-light border text-muted'));
    }
    q(card, '.drv-ev-del').setAttribute('aria-label', '出来事' + n + 'を削除');
  }

  function setOpen(card, open) {
    q(card, '.drv-ev-body').hidden = !open;
    var t = q(card, '.drv-ev-toggle');
    t.setAttribute('aria-expanded', open ? 'true' : 'false');
    var chev = q(card, '.drv-chev');
    chev.classList.toggle('fa-chevron-down', open);
    chev.classList.toggle('fa-chevron-right', !open);
    summarize(card);
  }

  // 店の一覧（約30店）が出来事の数だけ開くと長すぎるので、開くのは常に1件だけ
  function openOnly(card) {
    cards().forEach(function (c) { if (c !== card && !q(c, '.drv-ev-body').hidden) setOpen(c, false); });
    setOpen(card, true);
  }

  function updateLocs(card) {
    var select = scopeOf(card) === 'select';
    q(card, '.drv-locs').hidden = !select;
    var n = checkedLocs(card).length;
    q(card, '.drv-loc-count').textContent = n + '店選択';
    q(card, '.drv-loc-clear').disabled = n === 0;
  }

  function clearErrors(card) {
    card.querySelectorAll('.is-invalid').forEach(function (el) { el.classList.remove('is-invalid'); });
    card.querySelectorAll('.drv-locs .invalid-feedback').forEach(function (el) { el.classList.remove('d-block'); });
  }

  function addCard(ev) {
    var i = next++;
    total.value = next;
    var wrap = document.createElement('div');
    wrap.innerHTML = tpl.replace(/__i__/g, i);
    var card = wrap.firstElementChild;
    q(card, '.drv-ev-kind').value = ev ? ev.kind : '';
    q(card, '.drv-scope[value="' + (ev && !ev.all ? 'select' : 'all') + '"]').checked = true;
    (ev ? ev.locations : []).forEach(function (id) {
      var box = q(card, '.drv-loc[value="' + id + '"]');
      if (box) box.checked = true;
    });
    q(card, '.drv-ev-exclude').checked = ev ? ev.exclude : true;
    q(card, '.drv-ev-note').value = ev ? ev.note : '';
    list.appendChild(card);
    updateLocs(card);
    setOpen(card, false);
    return card;
  }

  list.addEventListener('click', function (e) {
    var card = e.target.closest('.drv-ev');
    if (!card) return;
    if (e.target.closest('.drv-ev-toggle')) {
      if (card.classList.contains('is-deleted')) return;
      if (q(card, '.drv-ev-body').hidden) openOnly(card); else setOpen(card, false);
    } else if (e.target.closest('.drv-ev-del')) {
      setOpen(card, false);
      card.classList.add('is-deleted');
      q(card, '.drv-ev-del').hidden = true;
      q(card, '.drv-ev-undo-wrap').hidden = false;
      q(card, '.drv-ev-undo').focus();
      updateCount();
    } else if (e.target.closest('.drv-ev-undo')) {
      if (activeCards().length >= MAX) return;
      card.classList.remove('is-deleted');
      q(card, '.drv-ev-undo-wrap').hidden = true;
      q(card, '.drv-ev-del').hidden = false;
      q(card, '.drv-ev-del').focus();
      updateCount();
    } else if (e.target.closest('.drv-loc-clear')) {
      checkedLocs(card).forEach(function (l) { l.checked = false; });
      updateLocs(card);
    }
  });

  list.addEventListener('change', function (e) {
    var card = e.target.closest('.drv-ev');
    if (!card) return;
    if (e.target.matches('.drv-ev-kind') && e.target.value) e.target.classList.remove('is-invalid');
    if (e.target.matches('.drv-scope, .drv-loc')) {
      updateLocs(card);
      if (scopeOf(card) === 'all' || checkedLocs(card).length) q(card, '.drv-locs .invalid-feedback').classList.remove('d-block');
    }
    summarize(card);
  });

  addBtn.addEventListener('click', function () {
    if (activeCards().length >= MAX) return;
    var card = addCard(null);
    openOnly(card);
    updateCount();
    q(card, '.drv-ev-kind').focus();
  });

  comment.addEventListener('input', function () { comment.classList.remove('is-invalid'); });

  modal.addEventListener('show.bs.modal', function (e) {
    var btn = e.relatedTarget;
    if (!btn) return;
    var day = btn.dataset.drvDate;
    var review = data[day];
    form.action = btn.dataset.drvAction;
    document.getElementById('drv-date').textContent = btn.dataset.drvLabel;
    list.textContent = '';
    next = 0;
    total.value = 0;
    comment.value = review ? review.comment : '';
    comment.classList.remove('is-invalid');
    (review ? review.events : []).forEach(function (ev) { addCard(ev); });
    document.getElementById('drv-updated').textContent = review ? '最終更新 ' + review.updated : '';
    delBtn.hidden = !review;
    updateCount();
  });

  modal.addEventListener('shown.bs.modal', function () { comment.focus(); });

  modal.addEventListener('hidden.bs.modal', function () {
    delBtn.textContent = '削除';
    delBtn.classList.add('btn-outline-danger');
    delBtn.classList.remove('btn-danger');
    document.getElementById('drv-action').value = 'save';
  });

  // 全体の削除は2回押したときだけ送る
  delBtn.addEventListener('click', function () {
    if (!delBtn.classList.contains('btn-danger')) {
      delBtn.textContent = '削除確定';
      delBtn.classList.remove('btn-outline-danger');
      delBtn.classList.add('btn-danger');
      return;
    }
    document.getElementById('drv-action').value = 'delete';
    lock();
    form.submit();
  });

  function lock() {
    [saveBtn, delBtn].concat(Array.prototype.slice.call(form.querySelectorAll('[data-bs-dismiss]'))).forEach(function (b) { b.disabled = true; });
    var sp = document.createElement('span');
    sp.className = 'spinner-border spinner-border-sm me-1';
    sp.setAttribute('aria-hidden', 'true');
    saveBtn.insertBefore(sp, saveBtn.firstChild);
  }

  form.addEventListener('submit', function (e) {
    if (saveBtn.disabled) { e.preventDefault(); return; }
    var errors = 0, firstCard = null, firstField = null;
    activeCards().forEach(function (card) {
      clearErrors(card);
      var kind = q(card, '.drv-ev-kind');
      var bad = null;
      if (!kind.value) { kind.classList.add('is-invalid'); bad = kind; errors++; }
      if (scopeOf(card) === 'select' && !checkedLocs(card).length) {
        q(card, '.drv-locs .invalid-feedback').classList.add('d-block');
        bad = bad || card.querySelector('.drv-loc');
        errors++;
      }
      if (bad && !firstCard) { firstCard = card; firstField = bad; }
      summarize(card);
    });
    var empty = !comment.value.trim() && !activeCards().length;
    comment.classList.toggle('is-invalid', empty);
    if (empty) { errors++; firstField = firstField || comment; }
    if (errors) {
      e.preventDefault();
      if (firstCard) openOnly(firstCard);
      if (firstField) firstField.focus();
      live.textContent = '入力エラー ' + errors + '件';
      return;
    }
    // 削除予定のカードは送らない
    cards().forEach(function (card) {
      if (card.classList.contains('is-deleted')) {
        card.querySelectorAll('input, select').forEach(function (el) { el.disabled = true; });
      }
    });
    lock();
  });
})();
