# orders アプリ — 納品書レイアウト設計メモ

## 用紙サイズ
| モード | 用紙 | 余白 | 有効印字域 |
|--------|------|------|------------|
| 通常弁当 | A5 (148×210mm) | 10mm | 128×190mm |
| 仕出し弁当 (CATERING) | A4 (210×297mm) | 10mm | 190×277mm |

## フォントサイズ
| 要素 | 通常弁当(A5) | 仕出し弁当(A4) |
|------|-------------|---------------|
| body | 9pt | 10pt |
| 商品名セル (td.name) | 8pt | 10pt |
| 日付 | 10pt | 11pt |
| タイトル | 18pt | 22pt |
| 下段テキスト | 10pt | 11pt |
| 会社情報 (右欄) | 8pt | 10pt |
| 税テーブル | 11-12pt | 13-14pt |

宛名フォントサイズは `pdf.py` の `_customer_name_font_size()` で自動計算:
- A5 納品書: 基本14pt → 12pt → 10pt → 9pt（文字数で段階縮小）
- 領収書 (A5横): 基本15pt → 13pt → 11pt → 10pt

## 通常弁当テーブル列幅（7列）
| No | 品名 | 単価 | 大盛 | 普通盛 | 小盛 | 金額 |
|----|------|------|------|--------|------|------|
| 6% | 36% | 12% | 9% | 9% | 9% | 19% |

- 品名列: 36% × 128mm = 46mm → 約14文字(8pt)収容
- 金額列を広めにして "¥12,500" 等の右寄せ表示を確保

## 仕出し弁当テーブル列幅（5列）
| No | 品名 | 単価 | 数量 | 金額 |
|----|------|------|------|------|
| 8% | 40% | 12% | 13% | 27% |

- 品名列: 40% × 190mm = 76mm → 約25文字(10pt)収容、変更不要

## 折り返し防止方針
- `.customer-name`: `white-space: nowrap`（宛名を1行に固定）
- `td.name`（商品名セル）: `white-space: nowrap` + フォントサイズ縮小
- `.company-info`（右欄の自社情報）: `white-space: nowrap` + `font-size: 8pt`
  - 理由: "有限会社ランチサービスネットワーク"(17文字)が40%列(51mm)で折り返す可能性があるため
- `table.items` は `table-layout: auto`（デフォルト）のまま維持すること
  - `table-layout: fixed` にすると単価・金額列（12-19%）が数値を折り返してしまう

## 宛名2行構成（意図的）
社名と部署名は `<br>` で区切り2行表示。仕様として許容。
`_customer_name_font_size()` で両行の最長文字数から自動縮小して列幅に収める。

## A4バランス調整方針
仕出し弁当はA4用紙だが共通CSSはA5向け値のため、`{% if is_catering %}` ブロック内の
スタイルシートでフォントサイズ・余白・パディングを上書きしてA4の広さに合わせる。

## 領収書 (A5横) レイアウト仕様

| 項目 | 値 |
|------|----|
| 用紙 | A5横 210×148mm |
| マージン | 10mm 全辺 |
| 有効印字域 | 190×128mm |
| body | 9pt |
| タイトル「領　収　証」 | 26pt |
| 金額枠 | 26pt、幅80% |
| 日付 | 10pt |

宛名フォントサイズは `pdf.py` の `_customer_name_font_size()` で自動計算（基本15pt）。

### 下段テーブル列幅
| 収入印紙 | 内訳 | 会社情報 |
|---------|------|---------|
| 16mm | 62mm | 残り ≈ 108mm |

### 折り返し防止対象
- `.customer-name`: `white-space: nowrap`（宛名）
- `.company-name-text`: `white-space: nowrap`（社名）
- `.company-info`: `white-space: nowrap`（住所・TEL等）
- `.breakdown-label` / `.breakdown-amount`: `white-space: nowrap`（内訳欄）

## PDF生成
- WeasyPrint 使用（reportlab ではない）
- フォント: IPAexGothic（本文）/ IPAexMincho（テーブル内商品名）
- 生成ロジック: `orders/pdf.py` の `generate_delivery_slip()` / `generate_receipt()`

## プレビュー印刷時の倍率（zoom）制御

`delivery_slip_preview.html` の `@media print` ブロック内 `body` に適用される。

| 条件 | zoom値 | 理由 |
|------|--------|------|
| `customer_notes` あり **かつ** `has_extra_items` あり | 0.97（97%） | 備考欄＋追加商品テーブルの両方が入るとページが溢れるため縮小 |
| それ以外 | 1（100%） | 縮小不要 |

- `customer_notes`: 発注備考テキスト（空でなければ備考ボックスが出現）
- `has_extra_items`: 追加商品テーブルの有無フラグ
- zoom は印刷メディア (`@media print`) 限定。画面プレビューには影響しない。

## 修正時のルール
納品書・領収書を修正するときは必ず両方のpreviewテンプレートも修正すること。
- 納品書: `delivery_slip.html` ↔ `delivery_slip_preview.html`
- 領収書: `receipt.html` ↔ `receipt_preview.html`
