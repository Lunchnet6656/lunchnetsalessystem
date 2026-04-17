/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./quest/templates/quest/**/*.html'],
  theme: {
    extend: {
      colors: {
        'dq-blue': '#0d1b4a',
        'dq-blue-light': '#1a237e',
        'dq-blue-mid': '#0f2567',
        'dq-gold': '#ffd700',
        'dq-gold-dark': '#c5a600',
        'dq-white': '#f0f0f0',
        /* celebration 専用カラー */
        'cel-bg': '#1a0a00',
        'cel-window': '#2d1a00',
        'cel-border': '#c8a000',
        'cel-text': '#fffde0',
        'cel-accent': '#ffd700',
        'cel-blue': '#4080ff',
        'cel-purple': '#9040ff',
      },
      fontFamily: {
        'pixel': ['"DotGothic16"', 'monospace'],
      },
      keyframes: {
        'golden-glow': {
          '0%, 100%': { boxShadow: '0 0 10px #ffd700, 0 0 20px #ffd700' },
          '50%': { boxShadow: '0 0 30px #ffd700, 0 0 60px #ffd700, 0 0 90px #ffd700' },
        },
        'level-up-scale': {
          '0%': { transform: 'scale(0.5)', opacity: '0' },
          '60%': { transform: 'scale(1.1)', opacity: '1' },
          '100%': { transform: 'scale(1)', opacity: '1' },
        },
        'sparkle': {
          '0%, 100%': { opacity: '1', transform: 'scale(1) rotate(0deg)' },
          '25%': { opacity: '0.5', transform: 'scale(1.3) rotate(90deg)' },
          '50%': { opacity: '1', transform: 'scale(0.8) rotate(180deg)' },
          '75%': { opacity: '0.5', transform: 'scale(1.3) rotate(270deg)' },
        },
        'xp-fill': {
          '0%': { width: '0%' },
        },
        'float': {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-6px)' },
        },
        /* celebration 専用アニメーション */
        'flash-white': {
          '0%': { opacity: '0' },
          '20%': { opacity: '1' },
          '100%': { opacity: '0' },
        },
        'rise-in': {
          '0%': { opacity: '0', transform: 'translateY(40px) scale(0.8)' },
          '60%': { opacity: '1', transform: 'translateY(-8px) scale(1.05)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        'glow-pulse': {
          '0%, 100%': { textShadow: '0 0 8px #ffd700, 0 0 16px #ffd700' },
          '50%': { textShadow: '0 0 24px #ffd700, 0 0 48px #ff8800, 0 0 80px #ffdd00' },
        },
        'count-up': {
          '0%': { opacity: '0', transform: 'scale(0.6)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'title-old-out': {
          '0%': { opacity: '1', transform: 'scale(1) translateY(0)' },
          '100%': { opacity: '0', transform: 'scale(0.7) translateY(-30px)' },
        },
        'title-new-in': {
          '0%': { opacity: '0', transform: 'scale(1.4) translateY(20px)' },
          '60%': { opacity: '1', transform: 'scale(0.95) translateY(-4px)' },
          '100%': { opacity: '1', transform: 'scale(1) translateY(0)' },
        },
        'char-change': {
          '0%': { opacity: '0', transform: 'scale(0.5) rotate(-5deg)' },
          '60%': { opacity: '1', transform: 'scale(1.08) rotate(2deg)' },
          '100%': { opacity: '1', transform: 'scale(1) rotate(0)' },
        },
        'ray-spin': {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
        'scene-fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'xp-bar-grow': {
          '0%': { width: '0%' },
          '100%': { width: 'var(--xp-target)' },
        },
        'mission-pop': {
          '0%': { opacity: '0', transform: 'translateX(-20px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        'star-burst': {
          '0%': { opacity: '1', transform: 'scale(0) translate(0, 0)' },
          '100%': { opacity: '0', transform: 'scale(1) translate(var(--tx), var(--ty))' },
        },
        'border-shimmer': {
          '0%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
          '100%': { backgroundPosition: '0% 50%' },
        },
      },
      animation: {
        'golden-glow': 'golden-glow 1.5s ease-in-out infinite',
        'level-up-scale': 'level-up-scale 0.6s ease-out forwards',
        'sparkle': 'sparkle 1s ease-in-out infinite',
        'xp-fill': 'xp-fill 1s ease-out forwards',
        'float': 'float 3s ease-in-out infinite',
        /* celebration 専用 */
        'flash-white': 'flash-white 0.8s ease-out forwards',
        'rise-in': 'rise-in 0.7s cubic-bezier(0.22,1,0.36,1) forwards',
        'glow-pulse': 'glow-pulse 1.8s ease-in-out infinite',
        'count-up': 'count-up 0.4s ease-out forwards',
        'title-old-out': 'title-old-out 0.5s ease-in forwards',
        'title-new-in': 'title-new-in 0.8s cubic-bezier(0.22,1,0.36,1) forwards',
        'char-change': 'char-change 0.9s cubic-bezier(0.22,1,0.36,1) forwards',
        'ray-spin': 'ray-spin 8s linear infinite',
        'scene-fade-in': 'scene-fade-in 0.4s ease-out forwards',
        'xp-bar-grow': 'xp-bar-grow 1.5s cubic-bezier(0.22,1,0.36,1) forwards',
        'mission-pop': 'mission-pop 0.4s ease-out forwards',
        'star-burst': 'star-burst 1s ease-out forwards',
        'border-shimmer': 'border-shimmer 2s linear infinite',
      },
    },
  },
  plugins: [],
}
