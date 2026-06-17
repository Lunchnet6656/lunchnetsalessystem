# shifts アプリ モバイル(レスポンシブ)対応 改善案

作成日: 2026-06-17 / 対象: `shifts/templates/shifts/` 全20テンプレート（Tailwind CSS）

## 結論サマリ
- base.html に viewport は設定済み。崩れの主因は **テーブルの横スクロール未対応** と **gridテーブルの固定 min-width**。
- 影響の8割はこの2パターン。共通修正で一気に直せる。

---

## 問題パターン別 一覧

### パターンA：テーブルが横スクロールできず画面外にはみ出す（深刻度：高）
親 div に `overflow-x-auto` が無く、列の多いテーブルがスマホ幅(375px)を超えてレイアウトを破壊する。

**要修正（ラップなし・9ファイル）**
| ファイル | 列数の目安 |
|---|---|
| admin_dashboard.html | 7列（日別ヒートマップ） |
| admin_review_submissions.html | 8列＋ネスト詳細テーブル |
| admin_user_profiles.html | 8列（padding `px-6 py-4`で特に広い） |
| admin_periods.html | 8列（中にselect/form多数） |
| admin_carpool_routes.html | 5列×2テーブル（行ごとにform） |
| admin_external_staff.html | 5列 |
| admin_daily_assignment.html | 4列 |
| admin_company_holidays.html | 3列 |
| my_submissions.html | 3列 |

**修正方法（全ファイル共通）**
```html
<!-- Before -->
<div class="bg-white rounded shadow">
  <table class="w-full ...">

<!-- After -->
<div class="bg-white rounded shadow overflow-x-auto">
  <table class="w-full ...">
```
※ `admin_location_settings.html` は対応済み（参考実装）。

---

### パターンB：gridテーブルの固定 min-width（深刻度：高）
`<style>`内で `min-width:60px/50px/40px` 等を固定指定。`overflow-auto` でラップ済みでも、日数×人数分の列で常に横スクロールが発生し片手操作しづらい。

**対象（3ファイル）**
- admin_period_assignment.html（行9,12,13 / 最大ファイル497行）
- view_schedule.html（行12）
- admin_external_availability.html（行9,11,12）

**改善方針（いずれか）**
1. 1列目（日付/氏名）を `position: sticky; left:0` で固定し、横スクロールしても基準列が見える化（推奨・低コスト）。
2. メディアクエリでスマホ時 `font-size` と `padding` を縮小し情報密度を上げる。
3. （大改修案）スマホ時はテーブルをやめてカード型1日1ブロック表示に切替。view_schedule と submit は閲覧頻度が高いので将来的に検討価値あり。

---

### パターンC：flex が折り返せず潰れる（深刻度：中）
`flex items-center justify-between` に `flex-wrap` が無く、タイトルとセレクタ等が同じ行で潰れる。

**対象**
- base.html 行16-29（ナビバー：ユーザー名が右端で潰れる）
- admin_dashboard.html 行6（タイトル＋期間セレクタ）
- admin_period_assignment.html 行53（操作ボタン群）

**修正方法**
```html
<!-- Before -->
<div class="flex items-center justify-between mb-4">
<!-- After -->
<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
```

---

### パターンD：タップ範囲が小さい（深刻度：中）
ラジオ/チェック/select/小ボタンが推奨44pxより小さく、指で押しにくい。

**対象**
- submit_availability.html 行88-122（出勤/休みラジオ・欠勤理由select）※提出画面で最重要
- admin_review_submissions.html 行88-101（却下/編集ボタン `text-xs`）
- admin_edit_submission.html 行29-53（ラジオ）
- admin_daily_assignment.html 行50（select `max-w-xs`が親を超える→ `w-full`へ）

**修正方法**
```html
<label class="inline-flex items-center min-h-[44px] px-2">
  <input type="radio" ... class="w-4 h-4">
  <span class="ml-2 text-sm">出勤</span>
</label>
```

---

## 推奨：実施順序（小さく刻んで安全に）
1. **第1弾（即効・低リスク）**：パターンA 9ファイルに `overflow-x-auto` 追加。表示崩れが即解消、ロジック変更なし。
2. **第2弾**：パターンC（base.html含むflex折り返し3箇所）。全画面の見た目が安定。
3. **第3弾**：パターンD（submit_availability優先＝スタッフが毎回触る画面）。
4. **第4弾**：パターンB の sticky 1列目固定。view_schedule / period_assignment の実用性向上。

## 動作確認方法
Chrome DevTools のデバイスエミュレーション（iPhone SE=375px, iPhone 14=390px）で各画面を確認。横スクロールバーが「テーブル内のみ」に収まり、ページ全体が横スクロールしない状態がゴール。

## スコープ外（今回は崩れ軽微）
select_period / rules / user_settings / admin_edit_profile / admin_shift_settings / admin_notification_settings は `max-w-*` や `sm:grid-cols-*` で既に概ね対応済み。
