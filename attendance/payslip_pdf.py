"""給与明細PDFの生成。

既存のExcel明細フォーマット（横テーブル6列×2行のセクション構成）に近づけて
1人1ページのA4PDFを生成する。法令上の給与明細交付義務に対応する。

reportlab の同梱CIDフォント（HeiseiKakuGo-W5）を使うので、外部フォントファイル
は不要。日本語の正常表示には CIDFont の登録が必要なので、初回呼び出し時に
1度だけ実施する。

レイアウト：
  ── ヘッダー（左：会社名/期間/氏名、右：差引支給額の独立ボックス）
  ── 支給（6列×2行のテーブル）
  ── 控除（6列×2行のテーブル）
  ── 勤怠（6列×2行のテーブル）
  ── 記事（6列×1行：累計・時給単価など）
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate, Frame

from . import aggregation
from .models import PayslipAdjustment

_JP_FONT = "HeiseiKakuGo-W5"
_FONT_REGISTERED = False

# 既存Excel明細のピンク色（ヘッダー帯の淡い色）
_PINK_HEADER = colors.HexColor("#F4D7DA")
_PINK_NETPAY = colors.HexColor("#E8B5BC")  # 差引支給額のラベル帯（少し濃いめ）
_BORDER = colors.HexColor("#888888")
_BG_LABEL = colors.HexColor("#F0F0F0")  # 左側「支給/控除」縦ラベルの灰


def _ensure_font():
    global _FONT_REGISTERED
    if not _FONT_REGISTERED:
        pdfmetrics.registerFont(UnicodeCIDFont(_JP_FONT))
        _FONT_REGISTERED = True


def _yen(value):
    """整数 → カンマ区切り。None/不正は 0。"""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _hours(minutes):
    """分 → 「8.5」のような小数1桁文字列。0なら 0.00。"""
    if not minutes:
        return "0.00"
    return f"{minutes / 60:.2f}"


def _para(text, size=10, bold=False, align="LEFT"):
    """Platypus 用のスタイル付き段落を1つ作る。"""
    style = ParagraphStyle(
        f"p{size}{bold}{align}",
        fontName=_JP_FONT,
        fontSize=size,
        leading=size * 1.2,
        alignment={"LEFT": 0, "CENTER": 1, "RIGHT": 2}.get(align, 0),
        textColor=colors.black,
    )
    return Paragraph(text, style)


def _section(label, header_row, value_rows, col_widths):
    """セクションテーブルを1つ作る（左に縦ラベル、右にヘッダー＋値）。

    label        … 「支給」「控除」「勤怠」「記事」など
    header_row   … 各列のラベル文字列のリスト（例: ["基本給", "早朝手当", ...]）
    value_rows   … 値行のリスト（複数行可、各行は列数分のリスト）
    col_widths   … ラベル列を除いた各列の幅（mm単位の数値リスト）
    """
    # 左の縦ラベル + 各列ヘッダー / 各値行
    label_w = 11 * mm
    rows = []
    rows.append([_para(f"<b>{label}</b>", size=9, align="CENTER")] + [
        _para(f"<b>{h}</b>", size=7, align="CENTER") for h in header_row
    ])
    for values in value_rows:
        rows.append([_para("", size=7)] + [
            _para(v, size=8, align="CENTER") for v in values
        ])

    table = Table(
        rows,
        colWidths=[label_w] + col_widths,
        rowHeights=[5.5 * mm] + [6 * mm] * len(value_rows),
    )
    style = TableStyle([
        # 全体
        ("GRID", (0, 0), (-1, -1), 0.6, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), _JP_FONT),
        # 縦ラベル列（左端）
        ("BACKGROUND", (0, 0), (0, -1), _BG_LABEL),
        ("SPAN", (0, 0), (0, -1)),  # 全行ぶち抜きで1セル化
        # ヘッダー行はピンク背景
        ("BACKGROUND", (1, 0), (-1, 0), _PINK_HEADER),
    ])
    table.setStyle(style)
    return table


def _net_pay_box(payslip):
    """差引支給額の右上ボックス。"""
    inner = Table(
        [
            [_para("<b>差引支給額</b>", size=10, align="CENTER")],
            [_para(f"<b>{_yen(payslip.net_pay_yen)}</b>", size=15, align="CENTER")],
        ],
        colWidths=[50 * mm],
        rowHeights=[7 * mm, 10 * mm],
    )
    inner.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, _BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), _PINK_HEADER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), _JP_FONT),
    ]))
    return inner


def _header_box(payslip):
    """左上の「会社名・期間・氏名」ボックス。"""
    staff = payslip.staff
    company = staff.get_company_display()
    # 月度ラベル（PayslipCalc には payroll_period が無いことがある）
    period_label = ""
    period = getattr(payslip, "payroll_period", None)
    if period is not None and getattr(period, "period_end", None):
        period_label = aggregation.period_label(period.period_end)

    header = Table(
        [
            [_para(f"<b>{company}</b>", size=10, align="CENTER")],
            [_para(f"{period_label}　給与支給明細書", size=9, align="CENTER")],
            [
                Table(
                    [[_para("<b>氏 名</b>", size=8, align="CENTER"),
                      _para(staff.display_name, size=10, align="CENTER")]],
                    colWidths=[14 * mm, 50 * mm],
                    rowHeights=[6 * mm],
                    style=TableStyle([
                        ("GRID", (0, 0), (-1, -1), 0.6, _BORDER),
                        ("BACKGROUND", (0, 0), (0, 0), _BG_LABEL),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("FONTNAME", (0, 0), (-1, -1), _JP_FONT),
                    ]),
                ),
            ],
        ],
        colWidths=[64 * mm],
        rowHeights=[7 * mm, 6 * mm, 6 * mm],
    )
    header.setStyle(TableStyle([
        ("GRID", (0, 0), (0, 1), 0.6, _BORDER),
        ("BACKGROUND", (0, 0), (0, 1), _PINK_HEADER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), _JP_FONT),
    ]))
    return header


def _build_story(payslip, payment_adjustments=None, deduction_adjustments=None):
    """1人分の明細を flowable のリストにする。"""
    _ensure_font()
    payment_adjustments = payment_adjustments or []
    deduction_adjustments = deduction_adjustments or []

    # 年末調整の支給/控除合計を別枠に。それ以外（慶弔金・出張手当など）は
    # 「その他手当」「その他控除」へ合算する（既存Excelフォーマットに準拠）。
    def _sum_adj(adjs, keyword=None, exclude_keyword=False):
        total = 0
        for a in adjs:
            in_kw = (keyword and keyword in a.name)
            if exclude_keyword:
                if not in_kw:
                    total += a.amount_yen
            else:
                if in_kw:
                    total += a.amount_yen
        return total

    nencho_pay = _sum_adj(payment_adjustments, "年末調整")
    nencho_deduct = _sum_adj(deduction_adjustments, "年末調整")
    other_adj_pay = _sum_adj(payment_adjustments, "年末調整", exclude_keyword=True)
    other_adj_deduct = _sum_adj(deduction_adjustments, "年末調整", exclude_keyword=True)

    # 派生集計
    syaho = (
        payslip.health_insurance_yen + payslip.nursing_insurance_yen
        + payslip.pension_yen + payslip.employment_insurance_yen
    )
    zeigaku = payslip.income_tax_yen + payslip.resident_tax_yen

    # 時間外割増の分離（残業手当=60h以内分／時間外労働手当=60h超分）。
    # 過去確定済みのレート変動を考慮しないため PayrollSetting.current() を使う。
    # v1 の許容範囲（年間でレートが変わるケースは稀、変わってもズレは小さい）。
    from .models import PayrollSetting
    from decimal import Decimal, ROUND_HALF_UP
    setting = PayrollSetting.current()
    over60_min = payslip.over60h_minutes or 0
    under60_min = max(0, (payslip.overtime_minutes or 0) - over60_min)
    wage = Decimal(payslip.hourly_wage or 0)

    def _round_yen(amount):
        return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))

    # v2方式：割増は「四捨五入した時給単価 × 時間」（基発150号）。エンジンと同じ手順で再構成する。
    ot_unit_under60 = _round_yen(wage * (Decimal(1) + setting.overtime_rate))
    ot_unit_over60 = _round_yen(wage * (Decimal(1) + setting.over60h_rate))
    under60_yen = _round_yen(Decimal(ot_unit_under60) * Decimal(under60_min) / 60)
    over60_yen = _round_yen(Decimal(ot_unit_over60) * Decimal(over60_min) / 60)
    # 合計が overtime_premium_yen と一致するように調整（丸め誤差対策）：差分を残業側へ寄せる
    diff = payslip.overtime_premium_yen - (under60_yen + over60_yen)
    under60_yen += diff

    story = []

    # --- 上段：会社/期間/氏名 と 差引支給額を横並びに ----------------------
    top = Table(
        [[_header_box(payslip), "", _net_pay_box(payslip)]],
        colWidths=[64 * mm, 60 * mm, 50 * mm],
        rowHeights=[20 * mm],
    )
    top.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(top)
    story.append(Spacer(1, 2 * mm))

    # --- 支給セクション（6列×2行） ----------------------------------------
    # A5横 = 210mm幅、両側マージン10mmずつなので使える幅は ~190mm
    # ラベル列 11mm + 6列で 179mm → 1列 ~ 29.5mm
    pay_col_widths = [29.5 * mm] * 6
    pay_h1 = ["基本給", "早朝手当", "残業手当", "時間外労働手当", "有給休暇手当", "年末調整超過額"]
    pay_v1 = [
        _yen(payslip.base_wage_yen),
        "—",                     # 早朝手当（モデル未対応・「—」で未対応を示す）
        _yen(under60_yen),       # 残業手当：60h以内の時間外（overtime_rate）
        _yen(over60_yen),        # 時間外労働手当：60h超の追加割増（over60h_rate）
        _yen(getattr(payslip, "paid_leave_yen", 0)),
        _yen(nencho_pay),
    ]
    # 通勤手当セルでは「定期 + ガソリン」の内訳を small で表示する（合算消去しない）。
    commute_base = payslip.commute_allowance_yen
    gasoline_yen = getattr(payslip, "gasoline_yen", 0)
    commute_total = commute_base + gasoline_yen
    if gasoline_yen > 0:
        commute_cell = (
            f"{commute_total:,}<br/>"
            f"<font size=6>(定期{commute_base:,}+ガ{gasoline_yen:,})</font>"
        )
    else:
        commute_cell = f"{commute_total:,}"

    # その他手当 = プロフィールその他手当 + 「年末調整」以外の支給アジャストメント。
    # 既存Excelに「その他臨時」枠が無いので、慶弔金等もここに合算する。
    other_pay_total = payslip.other_allowance_yen + other_adj_pay

    # ドライバー手当は固定列として常に表示（0でも0と表示。is_driverでない人は常に0）。
    driver_yen = getattr(payslip, "driver_allowance_yen", 0)

    pay_h2 = ["通勤手当", "その他手当", "行商手当", "箱洗い手当", "ドライバー手当", "総支給額"]
    pay_v2 = [
        commute_cell,
        _yen(other_pay_total),
        _yen(getattr(payslip, "peddling_allowance_yen", 0)),
        _yen(getattr(payslip, "box_wash_allowance_yen", 0)),
        _yen(driver_yen),
        _yen(payslip.gross_yen),
    ]
    sec = _section("支\n給", pay_h1, [pay_v1], pay_col_widths)
    story.append(sec)
    sec2 = _section("", pay_h2, [pay_v2], pay_col_widths)
    sec2.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), _JP_FONT),
        ("BACKGROUND", (0, 0), (0, -1), _BG_LABEL),
        ("SPAN", (0, 0), (0, -1)),
        ("BACKGROUND", (1, 0), (-1, 0), _PINK_HEADER),
        # 総支給額のセルだけ強調
        ("BACKGROUND", (-1, 0), (-1, 0), _PINK_NETPAY),
        ("FONTSIZE", (-1, 1), (-1, 1), 11),
    ]))
    story.append(sec2)
    story.append(Spacer(1, 1.5 * mm))

    # --- 控除セクション（6列×2行） ----------------------------------------
    ded_col_widths = [29.5 * mm] * 6
    ded_h1 = ["健康保険", "介護保険", "厚生年金", "雇用保険", "", "社会保険合計"]
    ded_v1 = [
        _yen(payslip.health_insurance_yen),
        _yen(payslip.nursing_insurance_yen),
        _yen(payslip.pension_yen),
        _yen(payslip.employment_insurance_yen),
        "",
        _yen(syaho),
    ]
    ded_h2 = ["所得税", "住民税", "税額合計", "年末調整不足分", "その他控除", "総控除額"]
    ded_v2 = [
        _yen(payslip.income_tax_yen),
        _yen(payslip.resident_tax_yen),
        _yen(zeigaku),
        _yen(nencho_deduct),
        _yen(payslip.other_deduction_yen + other_adj_deduct),
        _yen(payslip.total_deduction_yen),
    ]
    sec3 = _section("控\n除", ded_h1, [ded_v1], ded_col_widths)
    story.append(sec3)
    sec4 = _section("", ded_h2, [ded_v2], ded_col_widths)
    sec4.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), _JP_FONT),
        ("BACKGROUND", (0, 0), (0, -1), _BG_LABEL),
        ("SPAN", (0, 0), (0, -1)),
        ("BACKGROUND", (1, 0), (-1, 0), _PINK_HEADER),
        ("BACKGROUND", (-1, 0), (-1, 0), _PINK_NETPAY),
        ("FONTSIZE", (-1, 1), (-1, 1), 11),
    ]))
    story.append(sec4)
    story.append(Spacer(1, 1.5 * mm))

    # --- 勤怠セクション（6列×2行） ----------------------------------------
    # 確定済み Payslip は件数フィールドを持たない（PayslipCalc は持っている）
    # ので、無い場合は ManualWorkHours から引いて補完する。
    paid_leave_days = getattr(payslip, "paid_leave_days", None)
    peddling_count = getattr(payslip, "peddling_count", None)
    box_wash_count = getattr(payslip, "box_wash_count", None)
    driver_count = getattr(payslip, "driver_count", None)
    if paid_leave_days is None or peddling_count is None or box_wash_count is None or driver_count is None:
        from .models import ManualWorkHours as _MWH
        period = getattr(payslip, "payroll_period", None)
        mwh = None
        if period is not None and getattr(period, "pk", None):
            mwh = _MWH.objects.filter(
                staff=payslip.staff, payroll_period=period
            ).first()
        paid_leave_days = paid_leave_days if paid_leave_days is not None else (mwh.paid_leave_days if mwh else 0)
        peddling_count = peddling_count if peddling_count is not None else (mwh.peddling_count if mwh else 0)
        box_wash_count = box_wash_count if box_wash_count is not None else (mwh.box_wash_count if mwh else 0)
        driver_count = driver_count if driver_count is not None else (mwh.driver_count if mwh else 0)

    att_col_widths = [29.5 * mm] * 6
    att_h1 = ["総労働日数", "出勤日数", "有給休暇日数", "行商回数", "箱洗い数", "ドライバー回数"]
    att_v1 = [
        f"{payslip.work_days}",
        f"{payslip.work_days}",  # 同値（モデル未分離）
        f"{paid_leave_days}",
        f"{peddling_count}",
        f"{box_wash_count}",
        f"{driver_count}",
    ]
    att_h2 = ["基本出勤時間", "早朝出勤時間", "残業時間", "時間外労働時間", "遅刻時間", "早退時間"]
    att_v2 = [
        # 基本給に対応する通常時間。v1 の旧明細（normal未保存）は総実働で代替する。
        _hours(getattr(payslip, "normal_minutes", 0) or payslip.work_minutes),
        "—",                                # 早朝出勤時間（モデル未対応）
        _hours(payslip.overtime_minutes),
        _hours(payslip.over60h_minutes),
        "—",                                # 遅刻時間（モデル未対応）
        "—",                                # 早退時間（モデル未対応）
    ]
    sec5 = _section("勤\n怠", att_h1, [att_v1], att_col_widths)
    story.append(sec5)
    sec6 = _section("", att_h2, [att_v2], att_col_widths)
    sec6.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), _JP_FONT),
        ("BACKGROUND", (0, 0), (0, -1), _BG_LABEL),
        ("SPAN", (0, 0), (0, -1)),
        ("BACKGROUND", (1, 0), (-1, 0), _PINK_HEADER),
    ]))
    story.append(sec6)
    story.append(Spacer(1, 1.5 * mm))

    # --- 記事セクション（時給単価など） ------------------------------------
    # 時間外時給単価は四捨五入（基発150号・50銭以上切上げ）。実支給の計算単価と一致させる。
    overtime_unit = (
        _round_yen(wage * (Decimal(1) + setting.overtime_rate))
        if payslip.hourly_wage else 0
    )
    paid_leave_unit = (
        int(getattr(payslip, "paid_leave_yen", 0) / paid_leave_days)
        if paid_leave_days else 0
    )
    # 有給年度（10月〜9月）の累計取得日数を集計する。
    # 給与明細が出る期間に対応する有給年度を取得し、年度内の合計を表示。
    from . import services as _services
    ref_date = None
    period = getattr(payslip, "payroll_period", None)
    if period is not None and getattr(period, "period_end", None):
        ref_date = period.period_end
    if ref_date is not None:
        try:
            leave_summary = _services.paid_leave_summary(payslip.staff, ref_date)
            cum_paid_leave = leave_summary["used"]
        except Exception:
            cum_paid_leave = paid_leave_days  # フォールバック：当月のみ
    else:
        cum_paid_leave = paid_leave_days

    art_h = ["累計", "基本時給単価", "時間外時給単価", "", "有給休暇単価", "累計有給取得日数"]
    art_v = [
        "—",                       # 年間累計支給額は未対応
        _yen(payslip.hourly_wage),
        _yen(overtime_unit),
        "",
        _yen(paid_leave_unit) if paid_leave_unit else "—",
        f"{cum_paid_leave}日",     # 有給年度（10月〜9月）内の累計取得日数
    ]
    sec7 = _section("記\n事", art_h, [art_v], [29.5 * mm] * 6)
    story.append(sec7)

    # フッター
    story.append(Spacer(1, 2 * mm))
    story.append(_para(
        "※ この明細は給与計算アプリ（w002）が自動生成しました。",
        size=6, align="LEFT",
    ))
    return story


class _PayslipDoc(BaseDocTemplate):
    """A5横のドキュメントテンプレート（210mm × 148mm）。"""

    def __init__(self, buf):
        super().__init__(
            buf, pagesize=landscape(A5),
            leftMargin=10 * mm, rightMargin=10 * mm,
            topMargin=8 * mm, bottomMargin=8 * mm,
            title="給与明細",
        )
        frame = Frame(
            self.leftMargin, self.bottomMargin,
            self.width, self.height,
            id="main", showBoundary=0,
        )
        self.addPageTemplates([PageTemplate(id="payslip", frames=[frame])])


def render_payslip_pdf(payslip, payment_adjustments=None, deduction_adjustments=None):
    """1人分の給与明細PDFをバイト列で返す。"""
    _ensure_font()
    buf = BytesIO()
    doc = _PayslipDoc(buf)
    story = _build_story(
        payslip,
        payment_adjustments=payment_adjustments,
        deduction_adjustments=deduction_adjustments,
    )
    doc.build(story)
    return buf.getvalue()


def render_payslips_bulk_pdf(payslips):
    """複数 Payslip を1つのPDFに結合（1人1ページ）。"""
    from reportlab.platypus import PageBreak
    _ensure_font()
    buf = BytesIO()
    doc = _PayslipDoc(buf)
    story = []
    for i, payslip in enumerate(payslips):
        adjustments = list(
            PayslipAdjustment.objects.filter(
                staff=payslip.staff,
                payroll_period=payslip.payroll_period,
            )
        )
        pay_adjs = [a for a in adjustments if a.kind == PayslipAdjustment.KIND_PAYMENT]
        ded_adjs = [a for a in adjustments if a.kind == PayslipAdjustment.KIND_DEDUCTION]
        if i > 0:
            story.append(PageBreak())
        story.extend(_build_story(
            payslip,
            payment_adjustments=pay_adjs,
            deduction_adjustments=ded_adjs,
        ))
    doc.build(story)
    return buf.getvalue()
