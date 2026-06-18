// 打刻画面のクライアント処理。
// 打刻自体は通常のフォームPOSTで行う（fetch を使わない＝通信失敗の死角を作らない）。
// ここでは通信を伴わない補助だけを担う。
(function () {
  "use strict";

  // 現在時刻の表示を更新する。打刻自体はサーバ時刻で記録されるため、これは表示用。
  var timeEl = document.getElementById("js-time");
  if (timeEl) {
    var renderTime = function () {
      var now = new Date();
      var hh = String(now.getHours()).padStart(2, "0");
      var mm = String(now.getMinutes()).padStart(2, "0");
      timeEl.textContent = hh + ":" + mm;
    };
    renderTime();
    setInterval(renderTime, 10000);
  }

  // 出勤・退勤ボタンの二度押しを防ぐ。送信開始と同時にボタンを無効化する。
  document.querySelectorAll("form[data-confirm-punch]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("button[type=submit]");
      if (button) {
        button.disabled = true;
        button.textContent = "送信中…";
      }
    });
  });

  // 打刻の取り消しは誤操作を防ぐため確認を挟む。
  document.querySelectorAll("form[data-confirm-undo]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm("直近の打刻を取り消しますか？")) {
        event.preventDefault();
      }
    });
  });
})();
