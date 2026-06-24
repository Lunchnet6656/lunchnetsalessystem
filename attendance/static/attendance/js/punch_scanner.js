// 共有端末用のQRスキャナー。
// カメラ映像から jsQR で QR を読み取り、サーバの kiosk API に打刻リクエストを投げる。
// 連続スキャン時の二重送信は cooldown フラグで抑える。
(function () {
  "use strict";

  const scanner = document.querySelector(".kiosk__scanner");
  if (!scanner) {
    return;
  }
  const apiUrl = scanner.dataset.kioskApi;
  const mode = scanner.dataset.mode;
  // 打刻成功後、この秒数だけ結果を見せてからモード選択画面へ自動で戻す。
  // 共有端末がスキャン画面のまま放置されるのを防ぐ。
  const HOME_REDIRECT_SEC = 5;
  const homeUrl = scanner.dataset.homeUrl;
  const video = document.getElementById("kiosk-video");
  const canvas = document.getElementById("kiosk-canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const prompt = document.getElementById("kiosk-prompt");
  const toast = document.getElementById("kiosk-toast");

  // Web Speech API の音声リストは非同期に読み込まれる端末があるので、
  // 早めに getVoices() を叩いて初回発話で無音になる事故を避ける。
  if (window.speechSynthesis) {
    try {
      window.speechSynthesis.getVoices();
      window.speechSynthesis.addEventListener("voiceschanged", function () {
        window.speechSynthesis.getVoices();
      });
    } catch (e) { /* noop */ }
  }

  // 連続スキャンの抑制（直近2.5秒間は再送信しない）
  let cooldownUntil = 0;
  // 同じQRをほぼ同時に多重検出するのを避けるため、直近のスキャン値を覚える
  let lastScanned = "";
  let lastScannedAt = 0;

  function showToast(state, message) {
    toast.hidden = false;
    toast.dataset.state = state; // "ok" | "error"
    toast.textContent = message;
  }

  function hideToast() {
    toast.hidden = true;
    toast.textContent = "";
    toast.removeAttribute("data-state");
  }

  function setPrompt(text) {
    prompt.textContent = text;
  }

  // 打刻成功後にモード選択画面へ戻すタイマー。次の人がすぐスキャンしたら
  // 新しい sendPunch 側でキャンセルし、最新の打刻結果を優先する。
  let homeTimer = null;
  function cancelHomeRedirect() {
    if (homeTimer) {
      clearInterval(homeTimer);
      homeTimer = null;
    }
  }
  function scheduleHomeRedirect() {
    if (!homeUrl) {
      return;
    }
    cancelHomeRedirect();
    let remaining = HOME_REDIRECT_SEC;
    homeTimer = setInterval(function () {
      remaining -= 1;
      if (remaining <= 0) {
        cancelHomeRedirect();
        window.location.href = homeUrl;
        return;
      }
      setPrompt("あと" + remaining + "秒でモード選択へ戻ります…");
    }, 1000);
  }

  function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setPrompt("カメラが使えない端末です。スキャナーをお使いください。");
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "user" }, audio: false })
      .then((stream) => {
        video.srcObject = stream;
        video.setAttribute("playsinline", "true");
        video.play();
        requestAnimationFrame(tick);
      })
      .catch((err) => {
        console.error(err);
        setPrompt("カメラの利用許可が必要です（端末のブラウザ設定を確認）。");
      });
  }

  // カメラを停止して黒枠（カメラ表示）を消す。スキャン成功後に呼び、画面を
  // クリーンな待機状態へ戻す。予備カメラは次回ボタンで再度起動できる。
  function stopCamera() {
    if (video && video.srcObject) {
      video.srcObject.getTracks().forEach((t) => t.stop());
      video.srcObject = null;
    }
    const box = document.getElementById("kiosk-camera");
    const btn = document.getElementById("kiosk-camera-btn");
    if (box) {
      box.hidden = true;
    }
    if (btn) {
      btn.disabled = false;
      btn.textContent = "スキャナーが使えないときはカメラで読み取る";
    }
  }

  function tick() {
    if (video.readyState !== video.HAVE_ENOUGH_DATA) {
      requestAnimationFrame(tick);
      return;
    }
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const code = window.jsQR(image.data, image.width, image.height, {
      inversionAttempts: "dontInvert",
    });

    const now = Date.now();
    if (code && code.data && now > cooldownUntil) {
      // 同一QRの即時多重を抑える
      if (code.data === lastScanned && now - lastScannedAt < 1200) {
        // 何もしない
      } else {
        lastScanned = code.data;
        lastScannedAt = now;
        cooldownUntil = now + 2500;
        sendPunch(code.data);
      }
    }
    requestAnimationFrame(tick);
  }

  // 打刻成功時の音声メッセージ。Web Speech API（ブラウザ内蔵の音声合成）を
  // 使うので、MP3 ファイルや外部 API は不要。日本語音声がない端末では無音で
  // 失敗するが、トースト表示があるので運用は止まらない。
  function speak(text) {
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
      return;
    }
    try {
      // 直前の発話が残っていれば打ち切る（連続打刻で重ならないように）
      window.speechSynthesis.cancel();
      var u = new SpeechSynthesisUtterance(text);
      u.lang = "ja-JP";
      u.rate = 1.0;
      u.pitch = 1.0;
      u.volume = 1.0;
      window.speechSynthesis.speak(u);
    } catch (e) {
      // 音声合成は補助なので、失敗してもサイレントに進む
      console.warn("speech failed:", e);
    }
  }

  function sendPunch(rawValue) {
    // 前回の自動遷移待ちがあれば止める（次の人の打刻を優先）
    cancelHomeRedirect();
    setPrompt("送信中…");
    hideToast();
    fetch(apiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: rawValue, kind: mode }),
    })
      .then((res) => res.json().then((body) => ({ status: res.status, body })))
      .then(({ status, body }) => {
        if (body.ok) {
          showToast("ok", "✓ " + body.message);
          // カメラで読み取った場合は、成功したら黒枠を消してクリーンに戻す。
          // （ハードスキャナー読み取り時はカメラ未起動なので no-op）
          stopCamera();
          // 成功時のボイスメッセージ。kind はサーバーが返した確定値を使う
          // （クライアントの mode と一致するが、サーバー側を信頼源にする）。
          if (body.kind === "clock_in") {
            speak("おはようございます！");
          } else if (body.kind === "clock_out") {
            speak("お疲れさまでした！");
          }
          // 成功したら結果を数秒見せたあとモード選択画面へ自動で戻す。
          // （成功時のみ。エラー時は同じモードで再スキャンできるよう留まる）
          scheduleHomeRedirect();
          return;
        } else {
          const msg = body.message || "打刻に失敗しました。";
          showToast("error", "× " + msg);
          // エラー時もボイスで「もう一度」を知らせる。手で押せていなくても気付ける。
          speak("打刻できませんでした");
        }
        setPrompt("次の方どうぞ — QRをかざしてください");
        // toast は次のスキャンまで表示し続ける（記録の手がかり）
      })
      .catch((err) => {
        console.error(err);
        showToast("error", "× 通信エラー。もう一度お試しください。");
        setPrompt("QRコードをかざしてください");
      });
  }

  // --- ハードQRスキャナー（キーボードウェッジ）対応 ---
  // USB/Bluetooth接続のバーコード／QRスキャナーは、読み取った文字列を高速に
  // キー入力し、末尾に Enter を送る（HID＝キーボードとして振る舞う）。
  // それを拾って、カメラと同じ sendPunch に流す。カメラと併用＝スキャナー主・
  // カメラ予備。二重送信は既存の cooldown / lastScanned 抑制で防ぐ。
  let wedgeBuf = "";
  let wedgeLastAt = 0;
  document.addEventListener("keydown", function (e) {
    const now = Date.now();
    // 直前のキーから間隔が空いていれば新しい読み取りの先頭とみなす
    // （人の手打ちは遅く、スキャナーは連続して速い）
    if (now - wedgeLastAt > 120) {
      wedgeBuf = "";
    }
    wedgeLastAt = now;

    if (e.key === "Enter") {
      const val = wedgeBuf.trim();
      wedgeBuf = "";
      if (val.length < 8) {
        return;
      }
      e.preventDefault();
      // 同一QRのほぼ同時の多重を抑える
      if (val === lastScanned && now - lastScannedAt < 1200) {
        return;
      }
      lastScanned = val;
      lastScannedAt = now;
      cooldownUntil = now + 2500;
      sendPunch(val);
      return;
    }

    // 印字可能な1文字だけバッファに足す（Shift等の修飾キーは無視）
    if (e.key.length === 1) {
      wedgeBuf += e.key;
    }
  });

  // --- カメラ（予備）---
  // 普段はハードスキャナーで打刻するためカメラは起動しない（映像も出さない）。
  // スキャナーが使えないときだけ、ボタンでカメラを起動して読み取りに使う。
  const cameraBtn = document.getElementById("kiosk-camera-btn");
  const cameraBox = document.getElementById("kiosk-camera");
  if (cameraBtn) {
    cameraBtn.addEventListener("click", function () {
      if (cameraBox) {
        cameraBox.hidden = false;
      }
      cameraBtn.disabled = true;
      cameraBtn.textContent = "カメラ起動中…";
      setPrompt("QRコードをカメラにかざしてください");
      startCamera();
    });
  }
})();
