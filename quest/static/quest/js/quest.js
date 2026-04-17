'use strict';

// ========================================
// XPバー アニメーション
// ========================================
function animateXpBar() {
  const bar = document.getElementById('xp-bar');
  if (!bar) return;
  const target = parseInt(bar.dataset.target || '0', 10);
  // 少し遅延させてからアニメーション開始（ページ読み込み後の視覚効果）
  setTimeout(function () {
    bar.style.width = target + '%';
  }, 300);
}

// ========================================
// レベルアップモーダル
// ========================================
function closeLevelUpModal() {
  const overlay = document.getElementById('level-up-overlay');
  if (overlay) {
    overlay.style.opacity = '0';
    overlay.style.transition = 'opacity 0.4s ease';
    setTimeout(function () {
      overlay.style.display = 'none';
      // 新実績モーダルがあれば表示
      showAchievementModal();
    }, 400);
  }
}

// ========================================
// 新実績モーダル
// ========================================
function closeAchievementModal() {
  const overlay = document.getElementById('achievement-overlay');
  if (overlay) {
    overlay.style.opacity = '0';
    overlay.style.transition = 'opacity 0.4s ease';
    setTimeout(function () {
      overlay.style.display = 'none';
    }, 400);
  }
}

function showAchievementModal() {
  const overlay = document.getElementById('achievement-overlay');
  if (overlay) {
    overlay.style.display = 'flex';
    overlay.style.opacity = '0';
    setTimeout(function () {
      overlay.style.opacity = '1';
      overlay.style.transition = 'opacity 0.4s ease';
    }, 50);
  }
}

// ========================================
// ミッション達成エフェクト（スパークル）
// ========================================
function initMissionEffects() {
  const completedMissions = document.querySelectorAll('.mission-complete-effect');
  if (completedMissions.length === 0) return;

  // @keyframes sparkle をページに注入（未定義のため）
  if (!document.getElementById('sparkle-keyframes')) {
    const style = document.createElement('style');
    style.id = 'sparkle-keyframes';
    style.textContent = [
      '@keyframes sparkle {',
      '  0%   { opacity: 0; transform: scale(0); }',
      '  40%  { opacity: 1; transform: scale(1.3); }',
      '  100% { opacity: 0; transform: scale(0); }',
      '}',
    ].join('\n');
    document.head.appendChild(style);
  }

  completedMissions.forEach(function (el) {
    for (let i = 0; i < 3; i++) {
      const duration = (0.8 + Math.random() * 0.6).toFixed(2);
      const delay    = (i * 0.3).toFixed(2);
      const sparkle  = document.createElement('span');
      sparkle.style.cssText = [
        'position: absolute',
        'top: '    + (Math.random() * 80 + 10) + '%',
        'left: '   + (Math.random() * 80 + 10) + '%',
        'width: 6px',
        'height: 6px',
        'border-radius: 50%',
        'background-color: #ffd700',
        'pointer-events: none',
        'animation: sparkle ' + duration + 's ease-in-out ' + delay + 's 3',
        'animation-fill-mode: forwards',
        'z-index: 1',
      ].join('; ');
      el.appendChild(sparkle);

      // アニメーション終了後にDOM削除
      const totalMs = (parseFloat(duration) * 3 + parseFloat(delay)) * 1000 + 100;
      setTimeout(function () {
        if (sparkle.parentNode) sparkle.parentNode.removeChild(sparkle);
      }, totalMs);
    }
  });
}

// ========================================
// サウンド再生（将来対応）
// ========================================
const SoundManager = {
  enabled: false,

  toggle: function () {
    this.enabled = !this.enabled;
    const btn = document.getElementById('sound-toggle');
    if (btn) {
      btn.textContent = this.enabled ? '&#128266; ON' : '&#128264; OFF';
    }
  },

  play: function (type) {
    if (!this.enabled) return;
    // 将来: AudioContext / Web Audio API で効果音を再生
    // type: 'level_up' | 'mission_complete' | 'achievement'
    console.log('[SoundManager] play:', type);
  },
};

// ========================================
// ページ読み込み時の初期化
// ========================================
document.addEventListener('DOMContentLoaded', function () {
  // XPバーアニメーション
  animateXpBar();

  // ミッション達成エフェクト
  initMissionEffects();

  // レベルアップモーダル: 5秒後に自動クローズ
  const levelUpOverlay = document.getElementById('level-up-overlay');
  if (levelUpOverlay) {
    SoundManager.play('level_up');
    setTimeout(function () {
      closeLevelUpModal();
    }, 5000);
  }

  // 新実績モーダル（レベルアップがない場合は即時表示）
  const achievementOverlay = document.getElementById('achievement-overlay');
  if (achievementOverlay && !levelUpOverlay) {
    showAchievementModal();
  }

  // フォーカスリングの可視化（キーボード操作時）
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Tab') {
      document.body.classList.add('keyboard-nav');
    }
  });
  document.addEventListener('mousedown', function () {
    document.body.classList.remove('keyboard-nav');
  });
});

// グローバルに公開（HTML onclickから呼び出し可能に）
window.closeLevelUpModal = closeLevelUpModal;
window.closeAchievementModal = closeAchievementModal;
window.SoundManager = SoundManager;
