/* help.js — ⓘ（popover）の位置合わせ・非対応ブラウザ補完と、？（dialog）の開閉。
 * 開閉・Esc・外側タップは popover / dialog の標準機能に任せ、JSは足りない所だけ補う。 */
(function () {
  'use strict';
  var wide = window.matchMedia('(min-width: 681px)');
  var hasPopover = Object.prototype.hasOwnProperty.call(HTMLElement.prototype, 'popover');

  function triggerOf(tip) {
    return document.querySelector('.hp-tip-btn[popovertarget="' + tip.id + '"]');
  }

  // PC幅だけ ⓘ の真下に出す（はみ出すなら上）。スマホ幅はCSSの「画面下カード」のまま。
  function place(tip) {
    var btn = triggerOf(tip);
    tip.style.top = tip.style.left = tip.style.right = tip.style.bottom = '';
    if (!wide.matches || !btn) return;
    var r = btn.getBoundingClientRect();
    tip.style.right = tip.style.bottom = 'auto';
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var left = Math.min(Math.max(8, r.left - 8), window.innerWidth - w - 8);
    var top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }

  function openTips() {
    return Array.prototype.slice.call(document.querySelectorAll(
      hasPopover ? '.hp-tip:popover-open' : '.hp-tip.hp-open'));
  }

  function hide(tip) {
    if (hasPopover) { tip.hidePopover(); return; }
    tip.classList.remove('hp-open');
    var b = triggerOf(tip);
    if (b) b.setAttribute('aria-expanded', 'false');
  }

  Array.prototype.forEach.call(document.querySelectorAll('.hp-tip-btn[popovertarget]'), function (b) {
    b.setAttribute('aria-expanded', 'false');
  });

  if (hasPopover) {
    // 位置を決めるまで見せない（画面下→ⓘ下へ跳ぶちらつき防止）
    document.addEventListener('beforetoggle', function (e) {
      if (e.target.classList && e.target.classList.contains('hp-tip') && e.newState === 'open') {
        e.target.style.visibility = 'hidden';
      }
    }, true);
    document.addEventListener('toggle', function (e) {
      var tip = e.target;
      if (!tip.classList || !tip.classList.contains('hp-tip')) return;
      if (e.newState === 'open') place(tip);
      tip.style.visibility = '';
      // 開閉状態を読み上げ側にも確実に伝える（暗黙の expanded に頼らない）
      var b = triggerOf(tip);
      if (b) b.setAttribute('aria-expanded', e.newState === 'open' ? 'true' : 'false');
    }, true);
  } else {
    // iOS 16 以前など popover 非対応：同じマークアップのままクラスで開閉する
    document.addEventListener('click', function (e) {
      var btn = e.target.closest('[popovertarget]');
      if (btn) {
        var tip = document.getElementById(btn.getAttribute('popovertarget'));
        if (!tip) return;
        var wasOpen = tip.classList.contains('hp-open');
        openTips().forEach(hide);
        if (btn.getAttribute('popovertargetaction') === 'hide' || wasOpen) return;
        tip.classList.add('hp-open');
        triggerOf(tip).setAttribute('aria-expanded', 'true');
        place(tip);
        return;
      }
      if (!e.target.closest('.hp-tip')) openTips().forEach(hide);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      var open = openTips();
      open.forEach(hide);
      if (open.length) { var b = triggerOf(open[0]); if (b) b.focus(); }
    });
  }

  // PC幅：スクロール・リサイズで ⓘ との位置がずれるので閉じる
  function closeOnMove(e) {
    if (!wide.matches) return;
    if (e && e.target && e.target.closest && e.target.closest('.hp-tip')) return;
    openTips().forEach(hide);
  }
  window.addEventListener('scroll', closeOnMove, true);
  window.addEventListener('resize', closeOnMove);

  // ？ヘルプ：開く。閉じるは <form method="dialog"> と Esc（標準）＋背景タップ（下）
  document.addEventListener('click', function (e) {
    var opener = e.target.closest('[data-hp-open]');
    if (opener) {
      var d = document.getElementById(opener.getAttribute('data-hp-open'));
      if (d && d.showModal) d.showModal();
      return;
    }
    if (e.target.classList && e.target.classList.contains('hp-dialog')) e.target.close();
  });
})();
