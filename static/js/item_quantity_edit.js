/* item_quantity_edit.js — 持参数の修正（修正の表・消し込み・編集バー）。
 * 数の変更は［保存］でまとめて送る（同時修正の検出はサーバー）。✓は押した瞬間に画面へ出し、裏でまとめて送る。 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var root = $('iq');
  var D = JSON.parse($('iq-data').textContent);
  var CSRF = root.querySelector('[name=csrfmiddlewaretoken]').value;
  var STORE_KEY = 'iq-edit-restore';

  var products = D.products, locations = D.locations;
  var offIds = {}, activeCount = 0;
  locations.forEach(function (l) { if (l.off) offIds[l.id] = true; else activeCount++; });

  var dirty = {};          // key → 未保存の数
  var sel = null;          // {p, l}（l=null はメニュー行だけ選択）
  var mode = 'edit';
  var saving = false;
  var pend = {};           // ✓の送信待ち key → true/false
  var inflight = null;     // 送信中の✓
  var unsent = {};         // 送信に失敗して再送待ちの key
  var timer = null, retryDelay = 1000;
  var leaving = false;

  function key(p, l) { return p + ':' + l; }
  function cell(p, l) { return D.cells[key(p, l)] || (D.cells[key(p, l)] = { cur: 0, plan: 0, ck: false }); }
  function val(p, l) { var k = key(p, l); return k in dirty ? dirty[k] : cell(p, l).cur; }
  function nDirty() { return Object.keys(dirty).length; }
  function nPending() { return Object.keys(pend).length + (inflight ? Object.keys(inflight).length : 0); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }
  function fmt(n) { return n.toLocaleString('ja-JP'); }
  function diffText(d) { return d === 0 ? '（±0）' : d > 0 ? '（+' + d + '）' : '（−' + (-d) + '）'; }
  function productOf(id) { for (var i = 0; i < products.length; i++) if (products[i].id === id) return products[i]; }
  function locationOf(id) { for (var i = 0; i < locations.length; i++) if (locations[i].id === id) return locations[i]; }
  function rowTotals(p) {
    var now = 0, plan = 0;
    locations.forEach(function (l) { now += val(p, l.id); plan += cell(p, l.id).plan; });
    return { now: now, plan: plan };
  }
  // 持っていく数が0のマスは配るものが無いので、✓しなくても済み
  function isZeroDone(p, l) { return !offIds[l] && val(p, l) === 0; }
  function isDone(p, l) { return !offIds[l] && (val(p, l) === 0 || cell(p, l).ck); }
  function doneCount(p) {
    var n = 0;
    locations.forEach(function (l) { if (isDone(p, l.id)) n++; });
    return n;
  }

  /* ── 修正の表 ── */
  function renderGrid() {
    var h = '<thead><tr><th class="c-name">メニュー</th><th class="c-tot">合計</th>';
    locations.forEach(function (l) {
      var cls = 'iq-loc' + (l.off ? ' iq-off' : '') + (sel && sel.l === l.id ? ' iq-colfocus' : '');
      var badge = l.off ? '<span class="badge text-bg-secondary iq-colbadge">中止</span>'
        : l.submitted ? '<span class="badge text-bg-secondary iq-colbadge iq-submitted">提出済</span>' : '';
      h += '<th class="' + cls + '">' + badge + '<span class="iq-vname">' + esc(l.name) + '</span></th>';
    });
    h += '</tr></thead><tbody>';
    var colNow = {}, colPlan = {}, gNow = 0, gPlan = 0;
    products.forEach(function (p) {
      var t = rowTotals(p.id), dc = doneCount(p.id);
      h += '<tr' + (sel && sel.p === p.id ? ' class="iq-focus"' : '') + '><td class="c-name"><button type="button" class="iq-menu" data-menu="' + p.id + '">' +
        esc(p.name) + (dc ? '<span class="iq-ckcount">✓' + dc + '/' + activeCount + '</span>' : '') + '</button></td>' +
        '<td class="c-tot iq-row-total"><div class="iq-tot-now">' + fmt(t.now) + '</div>' +
        (t.now !== t.plan ? '<div class="iq-tot-plan">予定' + fmt(t.plan) + '</div>' : '') + '</td>';
      locations.forEach(function (l) {
        var c = cell(p.id, l.id), v = val(p.id, l.id), k = key(p.id, l.id);
        colNow[l.id] = (colNow[l.id] || 0) + v; colPlan[l.id] = (colPlan[l.id] || 0) + c.plan;
        var cls = ['iq-cell'];
        if (l.off) cls.push('iq-cell--off');
        if (v === 0) cls.push('iq-cell--zero');
        if (v !== c.plan) cls.push('iq-cell--diff');
        if (k in dirty) cls.push('iq-cell--dirty');
        if (sel && sel.p === p.id && sel.l === l.id) cls.push('iq-cell--selected');
        h += '<td class="' + cls.join(' ') + '"><button type="button" data-product="' + p.id + '" data-location="' + l.id +
          '" data-plan="' + c.plan + '" data-current="' + v + '" aria-label="' + esc(p.name + ' ' + l.name + ' 現在' + v + ' 予定' + c.plan) + '">' +
          (isDone(p.id, l.id) ? '<span class="iq-tick" aria-hidden="true">✓</span>' : '') + '<span class="iq-n">' + v + '</span>' +
          (v !== c.plan ? '<span class="iq-plan">予定' + c.plan + '</span>' : '') + '</button></td>';
      });
      h += '</tr>';
    });
    locations.forEach(function (l) { gNow += colNow[l.id] || 0; gPlan += colPlan[l.id] || 0; });
    h += '</tbody><tfoot><tr><td class="c-name">合計</td><td class="c-tot" id="iq-grand-total"><div class="iq-ft">' + fmt(gNow) + '</div>' +
      (gNow !== gPlan ? '<div class="iq-ftp">予定' + fmt(gPlan) + '</div>' : '') + '</td>';
    locations.forEach(function (l) {
      var n = colNow[l.id] || 0, pl = colPlan[l.id] || 0;
      h += '<td class="iq-col-total"><div class="iq-ft">' + n + '</div>' + (n !== pl ? '<div class="iq-ftp">予定' + pl + '</div>' : '') + '</td>';
    });
    h += '</tr></tfoot>';
    var box = $('iq-gridbox'), sx = box.scrollLeft, sy = box.scrollTop;
    $('iq-grid').innerHTML = h;
    box.scrollLeft = sx; box.scrollTop = sy;
    $('iq-sum-now').textContent = fmt(gNow);
    $('iq-sum-plan').textContent = gNow !== gPlan ? '予定 ' + fmt(gPlan) : '';
  }

  /* ── 消し込み（1メニューに集中） ── */
  function renderCheck() {
    if (!sel) sel = { p: products[0].id, l: null };
    var p = sel.p, idx = products.indexOf(productOf(p));
    $('iq-chips').innerHTML = products.map(function (x) {
      var dc = doneCount(x.id);
      return '<button type="button" class="iq-chip' + (x.id === p ? ' on' : '') + (dc === activeCount ? ' done' : '') + '" data-chip="' + x.id + '">' +
        esc(x.name) + '<span class="iq-pg">' + dc + '/' + activeCount + '</span></button>';
    }).join('');
    var dc = doneCount(p), t = rowTotals(p), done = dc === activeCount;
    var kh = $('iq-khead');
    kh.classList.toggle('done', done);
    kh.innerHTML = '<span class="iq-km">' + esc(productOf(p).name) + '</span>' +
      '<span class="iq-kp">' + (done ? '消し込み完了' : '消し込み <b>' + dc + '</b>／' + activeCount + '店') + '</span>' +
      '<span class="iq-kp">合計 ' + fmt(t.now) + '</span><span class="iq-spacer"></span>' +
      (idx < products.length - 1 ? '<button type="button" class="btn btn-primary iq-h" id="iq-next">次のメニュー</button>' : '');
    $('iq-tiles').innerHTML = locations.map(function (l) {
      var c = cell(p, l.id), v = val(p, l.id), k = key(p, l.id), cls = ['iq-tile'];
      if (l.off) cls.push('iq-tile--off');
      if (v === 0) cls.push('iq-tile--zero');
      if (v !== c.plan && !l.off) cls.push('iq-tile--diff');
      if (k in dirty) cls.push('iq-tile--dirty');
      if (isDone(p, l.id)) cls.push('iq-tile--done');
      if (sel.l === l.id) cls.push('iq-tile--selected');
      return '<div class="' + cls.join(' ') + '" data-l="' + l.id + '" role="button" tabindex="' + (l.off || isZeroDone(p, l.id) ? -1 : 0) +
        '" aria-pressed="' + isDone(p, l.id) + '" aria-label="' + esc(l.name + ' ' + (l.off ? '中止' : v)) + '">' +
        '<span class="iq-tl">' + esc(l.name) + '</span><span class="iq-tn">' + (l.off ? '中止' : v) + '</span>' +
        (v !== c.plan && !l.off ? '<span class="iq-tp">予定' + c.plan + '</span>' : '') +
        (unsent[k] ? '<span class="iq-unsent">未送信</span>' : '') + '</div>';
    }).join('');
    var on = $('iq-chips').querySelector('.on');
    if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  /* ── 編集バー ── */
  function setDisabled(ids, flag) { ids.forEach(function (id) { $(id).disabled = flag; }); }
  function renderBar() {
    var t = $('iq-target'), n = nDirty(), inp = $('iq-input');
    if (!D.editable) {
      t.innerHTML = '<div class="iq-bar-none">閲覧のみ</div><div class="iq-line2">&nbsp;</div>';
      setDisabled(['iq-minus', 'iq-plus', 'iq-input', 'iq-reset', 'iq-cancel', 'iq-save'], true);
      return;
    }
    if (!sel) {
      t.innerHTML = '<div class="iq-bar-none">マス未選択</div><div class="iq-line2">&nbsp;</div>';
      setDisabled(['iq-minus', 'iq-plus', 'iq-input', 'iq-reset'], true); inp.value = '';
    } else {
      var p = productOf(sel.p), r = rowTotals(sel.p), d = r.now - r.plan;
      var rowLine = '行の合計 ' + fmt(r.now) + '／予定 ' + fmt(r.plan) + ' <span id="iq-row-diff" class="' + (d === 0 ? 'ok' : '') + '">' + diffText(d) + '</span>';
      if (sel.l === null) {
        t.innerHTML = '<div><span class="iq-bar-menu">' + esc(p.name) + '</span><span class="iq-bar-x">×</span><span class="iq-bar-none">店未選択</span></div>' +
          '<div class="iq-line2">' + rowLine + '</div>';
        setDisabled(['iq-minus', 'iq-plus', 'iq-input', 'iq-reset'], true); inp.value = '';
      } else {
        var l = locationOf(sel.l), v = val(sel.p, sel.l), c = cell(sel.p, sel.l);
        var line2 = l.submitted ? '<span class="text-danger">日計表提出済み・日計表には反映なし</span>'
          : '予定 ' + c.plan + ' ・ ' + rowLine;
        t.innerHTML = '<div><span class="iq-bar-menu">' + esc(p.name) + '</span><span class="iq-bar-x">×</span><span class="iq-bar-loc">' + esc(l.name) + '</span></div>' +
          '<div class="iq-line2">' + line2 + '</div>';
        $('iq-minus').disabled = saving || v <= 0;
        $('iq-plus').disabled = saving;
        inp.disabled = saving;
        if (document.activeElement !== inp) { inp.value = v; inp.classList.remove('is-invalid'); }
        $('iq-reset').disabled = saving || v === c.plan;
      }
    }
    var dc = $('iq-dirty-count');
    dc.hidden = !n; dc.textContent = '未保存 ' + n;
    $('iq-cancel').disabled = saving || !n;
    var s = $('iq-save');
    s.disabled = saving || !n;
    s.innerHTML = saving ? '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>保存中' : '保存';
  }

  function render() {
    if (mode === 'edit') renderGrid(); else renderCheck();
    renderBar();
  }

  function setVal(v) {
    if (!sel || sel.l === null || saving) return;
    var k = key(sel.p, sel.l);
    v = Math.max(0, v);
    if (v === cell(sel.p, sel.l).cur) delete dirty[k]; else dirty[k] = v;
    render();
  }

  /* ── 通知（バーの上に短く出す） ── */
  var toastTimer = null;
  function toast(msg, warn) {
    var old = document.querySelector('.iq-toast');
    if (old) old.remove();
    var t = document.createElement('div');
    t.className = 'iq-toast' + (warn ? ' warn' : '');
    t.setAttribute('role', 'status');
    t.textContent = msg;
    $('iq-bar').appendChild(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.remove(); }, warn ? 4000 : 2000);
  }
  function showAlert(kind, msg, btnLabel, onBtn) {
    var a = $('iq-alert');
    a.className = 'iq-alert alert alert-' + kind;
    a.innerHTML = '<span>' + esc(msg) + '</span>' + (btnLabel ? '<button type="button" class="btn btn-sm btn-outline-' + kind + '" id="iq-alert-btn">' + esc(btnLabel) + '</button>' : '');
    a.hidden = false;
    if (btnLabel) $('iq-alert-btn').onclick = onBtn;
    fit();
  }
  function hideAlert() { $('iq-alert').hidden = true; fit(); }

  /* ── 保存（数の変更） ── */
  function reload() {
    leaving = true;
    try {
      var box = $('iq-gridbox');
      sessionStorage.setItem(STORE_KEY, JSON.stringify({
        date: D.date, mode: mode, sel: sel, sx: box.scrollLeft, sy: box.scrollTop, ty: $('iq-tiles').scrollTop,
      }));
    } catch (e) { /* 使えなければ左上から */ }
    location.reload();
  }
  function save() {
    if (saving || !nDirty()) return;
    saving = true; hideAlert(); render();
    var sent = Object.keys(dirty).map(function (k) {
      var ids = k.split(':');
      return { product_id: +ids[0], location_id: +ids[1], value: dirty[k], base: D.cells[k] ? D.cells[k].cur : 0 };
    });
    fetch(root.dataset.saveUrl, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ upload_id: D.uploadId, changes: sent }),
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) { return { res: res, data: data }; });
    }).then(function (r) {
      var data = r.data;
      if (r.res.ok && data.ok) {
        // ✓の送信待ちを先に出し切ってから読み直す（読み直しで✓が消えないように）
        waitChecks(reload);
        return;
      }
      saving = false;
      if (data.reupload) {
        showAlert('warning', '振分表から再受信あり・未保存 ' + nDirty() + '件は取りやめ', '再読み込み', function () { dirty = {}; reload(); });
      } else if (data.conflicts) {
        data.conflicts.forEach(function (c) {
          var k = key(c.product_id, c.location_id);
          cell(c.product_id, c.location_id).cur = c.current;
          delete dirty[k];
        });
        var first = data.conflicts[0].label, more = data.conflicts.length - 1;
        showAlert('warning', '他の修正あり：' + first + (more ? ' ほか' + more + '件' : ''));
      } else {
        showAlert('danger', '保存失敗・未保存 ' + nDirty() + '件はそのまま', '再送', save);
      }
      render();
    }).catch(function () {
      saving = false;
      showAlert('danger', '通信エラー・未保存 ' + nDirty() + '件はそのまま', '再送', save);
      render();
    });
  }

  /* ── ✓の保存（約1秒ためてまとめて送る・失敗は自動で再送） ── */
  function setCheck(p, l, on) {
    var k = key(p, l);
    cell(p, l).ck = on;
    pend[k] = on;
    if (!timer) timer = setTimeout(flushChecks, 1000);
  }
  function flushChecks() {
    timer = null;
    if (inflight || !Object.keys(pend).length) return;
    var batch = pend; pend = {}; inflight = batch;
    var body = Object.keys(batch).map(function (k) {
      var ids = k.split(':');
      return { product_id: +ids[0], location_id: +ids[1], checked: batch[k] };
    });
    fetch(root.dataset.checksUrl, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ checks: body }),
    }).then(function (res) {
      if (!res.ok) throw new Error(res.status);
      inflight = null; retryDelay = 1000;
      var had = Object.keys(unsent).length;
      Object.keys(batch).forEach(function (k) { delete unsent[k]; });
      if (had && mode === 'check') renderCheck();
      if (Object.keys(pend).length && !timer) timer = setTimeout(flushChecks, 1000);
    }).catch(function () {
      inflight = null;
      Object.keys(batch).forEach(function (k) {
        if (!(k in pend)) pend[k] = batch[k];
        unsent[k] = true;
      });
      retryDelay = Math.min(retryDelay * 2, 30000);
      if (!timer) timer = setTimeout(flushChecks, retryDelay);
      if (mode === 'check') renderCheck();
    });
  }
  function waitChecks(done) {
    if (!nPending()) { done(); return; }
    if (timer) { clearTimeout(timer); timer = null; }
    flushChecks();
    var tries = 0;
    (function poll() {
      // 通信が切れていて✓が送れない時は待ち続けない（✓は画面を開き直すと未保存として消える）
      if (!nPending() || ++tries > 30) { done(); return; }
      setTimeout(poll, 100);
    })();
  }
  window.addEventListener('online', function () {
    if (timer) { clearTimeout(timer); timer = null; }
    flushChecks();
  });
  function refreshChecks() {
    // 他の端末で付けた✓をモード切替のたびに取り込む（常時の自動更新はしない）
    return fetch(root.dataset.checksUrl, { credentials: 'same-origin' }).then(function (res) {
      if (!res.ok) throw new Error(res.status);
      return res.json();
    }).then(function (data) {
      var on = {};
      data.checked.forEach(function (k) { on[k] = true; });
      Object.keys(D.cells).forEach(function (k) {
        if (k in pend || (inflight && k in inflight)) return;
        D.cells[k].ck = !!on[k];
      });
      data.checked.forEach(function (k) {
        if (!D.cells[k]) { var ids = k.split(':'); cell(+ids[0], +ids[1]).ck = true; }
      });
      render();
    }).catch(function () { /* 取れなければ手元の表示のまま */ });
  }

  /* ── 高さ合わせ：表（消し込みのタイル）の下に編集バーがちょうど来るように ── */
  function fit() {
    var bar = $('iq-bar'), target = mode === 'edit' ? $('iq-gridbox') : $('iq-tiles');
    var top = target.getBoundingClientRect().top + window.scrollY;
    var h = window.innerHeight - top - bar.offsetHeight - 4;
    if (h < 320) {
      // スマホ：見出しの分だけページを送れば、表がナビバーと編集バーの間いっぱいに使える
      var nav = document.querySelector('.navbar');
      h = window.innerHeight - (nav ? nav.offsetHeight : 0) - bar.offsetHeight - 8;
    }
    target.style.height = Math.max(220, h) + 'px';
    if (mode === 'check') {
      var tiles = $('iq-tiles');
      tiles.classList.toggle('iq-scrolls', tiles.scrollHeight > tiles.clientHeight + 1);
    }
  }
  window.addEventListener('resize', fit);

  function setMode(m) {
    if (m === mode) return;
    mode = m;
    $('iq-mode-edit').className = 'btn ' + (m === 'edit' ? 'btn-primary' : 'btn-outline-secondary');
    $('iq-mode-check').className = 'btn ' + (m === 'check' ? 'btn-primary' : 'btn-outline-secondary');
    $('iq-mode-edit').setAttribute('aria-pressed', m === 'edit');
    $('iq-mode-check').setAttribute('aria-pressed', m === 'check');
    $('iq-gridbox').hidden = m !== 'edit';
    $('iq-kview').hidden = m !== 'check';
    render(); fit();
    refreshChecks();
  }

  /* ── 操作 ── */
  $('iq-grid').addEventListener('click', function (e) {
    var mb = e.target.closest('button[data-menu]');
    if (mb) { sel = { p: +mb.dataset.menu, l: null }; render(); return; }
    var b = e.target.closest('button[data-product]');
    if (!b) return;
    if (offIds[b.dataset.location]) { toast('中止の店は修正不可', true); return; }
    sel = { p: +b.dataset.product, l: +b.dataset.location };
    render();
  });
  $('iq-minus').onclick = function () { setVal(val(sel.p, sel.l) - 1); };
  $('iq-plus').onclick = function () { setVal(val(sel.p, sel.l) + 1); };
  $('iq-reset').onclick = function () { setVal(cell(sel.p, sel.l).plan); };
  $('iq-input').addEventListener('change', function (e) {
    var s = e.target.value.trim();
    if (!/^\d+$/.test(s)) {
      e.target.classList.add('is-invalid');
      $('iq-target').querySelector('.iq-line2').innerHTML = '<span class="text-danger">0以上の整数</span>';
      return;
    }
    e.target.classList.remove('is-invalid');
    setVal(parseInt(s, 10));
  });
  $('iq-input').addEventListener('keydown', function (e) { if (e.key === 'Enter') e.target.blur(); });
  $('iq-save').onclick = save;
  $('iq-cancel').onclick = function () {
    $('iq-confirm-text').textContent = '未保存の修正 ' + nDirty() + '件を取消';
    $('iq-act').hidden = true; $('iq-confirm').hidden = false;
  };
  $('iq-confirm-no').onclick = function () { $('iq-act').hidden = false; $('iq-confirm').hidden = true; };
  $('iq-confirm-yes').onclick = function () {
    dirty = {}; $('iq-act').hidden = false; $('iq-confirm').hidden = true; render();
  };

  if ($('iq-mode-edit')) {
    $('iq-mode-edit').onclick = function () { setMode('edit'); };
    $('iq-mode-check').onclick = function () { setMode('check'); };
  }
  $('iq-chips').addEventListener('click', function (e) {
    var c = e.target.closest('[data-chip]');
    if (c) { sel = { p: +c.dataset.chip, l: null }; render(); $('iq-tiles').scrollTop = 0; }
  });
  $('iq-khead').addEventListener('click', function (e) {
    if (!e.target.closest('#iq-next')) return;
    var idx = products.indexOf(productOf(sel.p));
    sel = { p: products[idx + 1].id, l: null };
    render(); $('iq-tiles').scrollTop = 0;
  });

  // 押す＝✓の付け外し。押したままなぞると、最初の店の状態に合わせて通過した店をまとめて付ける／外す
  var drag = null;
  function applyTile(el) {
    if (!el || !drag) return;
    var l = +el.dataset.l;
    if (offIds[l] || isZeroDone(sel.p, l) || drag.seen[l]) return;
    drag.seen[l] = true;
    if (cell(sel.p, l).ck !== drag.to) setCheck(sel.p, l, drag.to);
    sel = { p: sel.p, l: l };
    render();
  }
  $('iq-tiles').addEventListener('pointerdown', function (e) {
    var el = e.target.closest('.iq-tile');
    if (!el) return;
    if (el.classList.contains('iq-tile--off')) { toast('中止の店', true); return; }
    if (isZeroDone(sel.p, +el.dataset.l)) return;
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    drag = { to: !cell(sel.p, +el.dataset.l).ck, seen: {} };
    applyTile(el);
  });
  window.addEventListener('pointermove', function (e) {
    if (!drag) return;
    var el = document.elementFromPoint(e.clientX, e.clientY);
    applyTile(el && el.closest('#iq-tiles .iq-tile'));
  });
  function endDrag() {
    if (!drag) return;
    drag = null;
    if (doneCount(sel.p) === activeCount) toast(productOf(sel.p).name + ' 消し込み完了');
  }
  window.addEventListener('pointerup', endDrag);
  window.addEventListener('pointercancel', function () { drag = null; });
  $('iq-tiles').addEventListener('keydown', function (e) {
    var el = e.target.closest('.iq-tile');
    if (!el || (e.key !== 'Enter' && e.key !== ' ') || isZeroDone(sel.p, +el.dataset.l)) return;
    e.preventDefault();
    drag = { to: !cell(sel.p, +el.dataset.l).ck, seen: {} };
    applyTile(el); endDrag();
    var again = $('iq-tiles').querySelector('[data-l="' + el.dataset.l + '"]');
    if (again) again.focus();
  });

  // 未保存のまま日付を変えない（保存か取消を先に）
  root.querySelectorAll('.iq-dates a').forEach(function (a) {
    a.addEventListener('click', function (e) {
      if (nDirty()) { e.preventDefault(); toast('未保存の修正あり・保存か取消', true); }
    });
  });
  window.addEventListener('beforeunload', function (e) {
    if (leaving || (!nDirty() && !nPending())) return;
    e.preventDefault(); e.returnValue = '';
  });

  // 保存後の「保存 3件」は base.html のお知らせ欄ではなく、バーの上に短く出す（表の高さを奪わないため）
  var msgs = document.querySelector('.alert-messages');
  if (msgs) {
    var a = msgs.querySelector('.alert');
    if (a) toast(a.textContent.trim(), !a.classList.contains('alert-success'));
    msgs.remove();
  }

  // 読み直し前のモード・選択・スクロール位置へ戻す
  var restore = null;
  try {
    restore = JSON.parse(sessionStorage.getItem(STORE_KEY) || 'null');
    sessionStorage.removeItem(STORE_KEY);
  } catch (e) { restore = null; }
  if (restore && restore.date === D.date) {
    if (restore.sel && productOf(restore.sel.p)) sel = restore.sel;
    if (restore.mode === 'check' && $('iq-mode-check')) {
      mode = 'check';
      $('iq-mode-edit').className = 'btn btn-outline-secondary';
      $('iq-mode-check').className = 'btn btn-primary';
      $('iq-mode-edit').setAttribute('aria-pressed', 'false');
      $('iq-mode-check').setAttribute('aria-pressed', 'true');
      $('iq-gridbox').hidden = true; $('iq-kview').hidden = false;
    }
  }
  render(); fit();
  if (restore && restore.date === D.date) {
    $('iq-gridbox').scrollLeft = restore.sx || 0;
    $('iq-gridbox').scrollTop = restore.sy || 0;
    $('iq-tiles').scrollTop = restore.ty || 0;
  }
})();
