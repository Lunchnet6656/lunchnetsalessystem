/* 給与明細の勤怠インライン編集で、入力ごとにサーバへ計算を投げて
   支給・控除・差引支給額の表示を動的に更新する。

   fetch には必ず .catch() を付ける（lunchnetsale 既存システムの
   レビュー指摘＝fetch 失敗時のフォールバック無し、を踏襲しない）。 */
(function () {
  var form = document.getElementById('hours-save-form');
  if (!form) return;
  var previewUrl = form.dataset.previewUrl;
  if (!previewUrl) return;

  var inputs = form.querySelectorAll(
    'input[name="work_days"], input[name="work_hours"], ' +
    'input[name="break_hours"], ' +
    'input[name="overtime_hours"], input[name="night_hours"], ' +
    'input[name="holiday_hours"], ' +
    // 販売事業向けの手当入力（食堂ではDOMに存在しないので空セレクタになる）
    'input[name="peddling_count"], input[name="box_wash_count"], ' +
    'input[name="paid_leave_days"], input[name="driver_count"]'
  );
  if (!inputs.length) return;

  var csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
  var periodInput = form.querySelector('input[name="period"]');

  function formatYen(value) {
    if (value === null || value === undefined) return '0';
    return Number(value).toLocaleString('ja-JP');
  }

  var breakRow = document.querySelector('[data-break-row]');

  function updateDisplay(data) {
    var fields = document.querySelectorAll('[data-field]');
    fields.forEach(function (el) {
      var key = el.dataset.field;
      if (data[key] === undefined) return;
      el.textContent = formatYen(data[key]);
    });
    // 休憩控除の行は、控除額があるときだけ支給欄に出す。
    if (breakRow && data.break_deduction_yen !== undefined) {
      breakRow.hidden = !Number(data.break_deduction_yen);
    }
  }

  var debounceTimer = null;
  function schedulePreview() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function () {
      var body = new FormData();
      body.append('csrfmiddlewaretoken', csrfInput ? csrfInput.value : '');
      body.append('period', periodInput ? periodInput.value : '');
      inputs.forEach(function (inp) {
        body.append(inp.name, inp.value);
      });
      fetch(previewUrl, {
        method: 'POST',
        body: body,
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin'
      })
        .then(function (res) {
          if (!res.ok) throw new Error('preview HTTP ' + res.status);
          return res.json();
        })
        .then(updateDisplay)
        .catch(function (err) {
          // プレビュー失敗はページを壊さない。保存時のサーバ計算で必ず整合する。
          console.error('payslip preview failed:', err);
        });
    }, 200);
  }

  // --- 未保存変更の検知 → デカ保存ボタンの強調＋スティッキーバー＋離脱警告
  //
  // 入力初期値をスナップショットし、現在値と比較して dirty を判定する。
  // dirty なら：
  //   1. メイン保存ボタンに .save-cta--dirty を付けて脈打たせる
  //   2. スティッキー保存バーを画面下に出す
  //   3. beforeunload で離脱を警告する
  // フォーム送信時に dirty を解除して、保存後の離脱警告を出さないようにする。
  var initialValues = {};
  inputs.forEach(function (inp) { initialValues[inp.name] = inp.value; });

  var cta = document.getElementById('js-save-cta');
  var ctaSubClean = cta ? cta.querySelector('.save-cta__sub--clean') : null;
  var ctaSubDirty = cta ? cta.querySelector('.save-cta__sub--dirty') : null;
  var savebar = document.getElementById('js-sticky-savebar');
  var isDirty = false;

  function setDirty(next) {
    if (next === isDirty) return;
    isDirty = next;
    if (cta) cta.classList.toggle('save-cta--dirty', next);
    if (ctaSubClean) ctaSubClean.hidden = next;
    if (ctaSubDirty) ctaSubDirty.hidden = !next;
    if (savebar) savebar.hidden = !next;
    document.body.classList.toggle('has-sticky-savebar', next);
  }

  function recomputeDirty() {
    var dirty = false;
    inputs.forEach(function (inp) {
      if (inp.value !== initialValues[inp.name]) dirty = true;
    });
    setDirty(dirty);
  }

  inputs.forEach(function (input) {
    input.addEventListener('input', function () {
      schedulePreview();
      recomputeDirty();
    });
  });

  window.addEventListener('beforeunload', function (e) {
    if (!isDirty) return;
    // 旧仕様の互換のため returnValue にもセット。テキストはブラウザ側で固定文言が出る。
    e.preventDefault();
    e.returnValue = '保存していない変更があります。このまま離れますか？';
    return e.returnValue;
  });

  form.addEventListener('submit', function () {
    // 保存リクエストが飛ぶ → 離脱警告を出さないように dirty を解除する
    setDirty(false);
  });
})();
