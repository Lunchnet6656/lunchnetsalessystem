"""振分表マクロ（事務所PC）から持参数を受け取るAPI。

振分表の[確定して送る]ボタンで送られてくる（仮の数字は送らない）。同じ日付は最新の数字で上書きする。
ログイン画面を経由できない（VBAから直接叩く）ため、セッションではなく Bearer トークンで認証する。
"""
import csv
import datetime
import io
import json
import logging
import secrets

from django.conf import settings
from django.core.management import call_command
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from django.urls import reverse

from sales.item_quantity_import import parse_item_quantity_workbook, save_item_quantities
from sales.menu_registry import MENU_SLOTS, WEEK_START_WEEKDAY, register_week, week_end
from sales.menu_history import COLUMNS, build_menu_history
from sales.models import ItemQuantityUpload

logger = logging.getLogger(__name__)


def _authorized(request) -> bool:
    expected = settings.ITEM_QUANTITY_API_TOKEN
    header = request.headers.get("Authorization", "")
    if not expected or not header.startswith("Bearer "):
        return False
    return secrets.compare_digest(header[len("Bearer "):].strip(), expected)


@csrf_exempt
@require_POST
def api_item_quantity(request):
    if not _authorized(request):
        return _json({"ok": False, "errors": ["認証に失敗しました（合言葉ファイルを確認してください）"]}, status=401)

    # VBA からは xlsx をそのまま本文で送る。multipart(file) でも受ける
    upload = request.FILES.get("file")
    fileobj = upload if upload else io.BytesIO(request.body)
    parsed = parse_item_quantity_workbook(fileobj)

    # 古いファイルを開いて保存しただけで過去の実績（日計表の持参数）が書き換わらないように
    if parsed.target_date and parsed.target_date < timezone.localdate():
        parsed.errors.append(
            f"{parsed.target_date:%Y/%m/%d} は過去の日付のため振分表からは受け付けません（画面からアップロードしてください）"
        )

    if parsed.errors:
        ItemQuantityUpload.objects.create(
            target_date=parsed.target_date, source="api", ok=False, errors=parsed.errors,
        )
        return _json(
            {"ok": False, "target_date": _iso(parsed.target_date), "errors": parsed.errors},
            status=422,
        )

    saved = save_item_quantities(parsed)
    ItemQuantityUpload.objects.create(
        target_date=parsed.target_date, source="api", ok=True, saved_count=saved,
        errors=parsed.warnings,
    )

    published = False
    if parsed.target_date == timezone.localdate():
        try:
            call_command("publish_status_json")
            published = True
        except Exception:
            # 持参数は保存済み。出店状況ページは次回の publish で同期される
            logger.exception("持参数自動送信後の publish_status_json に失敗")

    return _json({
        "ok": True,
        "target_date": _iso(parsed.target_date),
        "saved_count": saved,
        "warnings": parsed.warnings,
        "published": published,
    })


@require_GET
def api_menu_history(request):
    """メニュー表（Excelの「Webから」）向けのメニュー実績CSV。読むだけなので、合言葉は登録用とは別。"""
    expected = settings.MENU_HISTORY_API_KEY
    key = request.GET.get("key", "")
    if not expected or not secrets.compare_digest(key, expected):
        return HttpResponse("認証に失敗しました", status=401, content_type="text/plain; charset=utf-8")

    today = timezone.localdate()
    cache_key = f"menu_history_csv_{today.isoformat()}"
    body = cache.get(cache_key)
    if body is None:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(COLUMNS)
        for row in build_menu_history(today):
            writer.writerow(["" if row[c] is None else row[c] for c in COLUMNS])
        body = buf.getvalue()
        # 日計表は夕方に入るので、1日1回の計算で十分（Excelで何度更新しても重くしない）
        cache.set(cache_key, body, 3600)
    # BOM 付きにしないと Excel が文字化けする
    return HttpResponse("\ufeff" + body, content_type="text/csv; charset=utf-8")


def _iso(d):
    return d.isoformat() if d else None


def _json(data, status=200):
    # VBA はJSONを解析せず message をそのまま表示する。日本語は \uXXXX にしない
    if data["ok"]:
        lines = [f"{data['target_date']} 分の持参数を {data['saved_count']} 件登録しました"]
        lines += data.get("warnings", [])
    else:
        lines = ["持参数を登録しませんでした（1件も保存していません）"]
        lines += [f"・{e}" for e in data["errors"]]
    data["message"] = "\n".join(lines)
    return JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})


# ===== メニュー送信（元のメニュー表の「メニュー送信.xlsm」から） =====
# 仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md §8.3
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"


def _menu_reply(ok, message, status=200, **extra):
    # VBA は message をそのまま表示する（文言をアプリ側で直せるように）
    return JsonResponse({"ok": ok, "message": message, **extra}, status=status,
                        json_dumps_params={"ensure_ascii": False})


def _refuse(message, status=422):
    return _menu_reply(False, "送りませんでした。\n" + message, status=status)


@csrf_exempt
@require_POST
def api_menu(request):
    """本文はJSON：{"week": "2026-10-14", "menus": ["①の名前", …, "⑩の名前"], "extra": ["⑪", "⑫"]}"""
    if not _authorized(request):
        return _menu_reply(False, "送れませんでした。\n認証に失敗しました（合言葉ファイルを確認してください）", status=401)
    try:
        body = json.loads(request.body.decode("utf-8"))
        week = datetime.date.fromisoformat(str(body.get("week", "")))
    except (ValueError, UnicodeDecodeError):
        return _refuse("送られてきた内容を読めませんでした。開発部に連絡してください。", status=400)

    label = f"{week.month}/{week.day}週"
    if week.weekday() != WEEK_START_WEEKDAY:
        return _refuse(f"『{week:%Y%m%d}』は水曜日ではありません。週のシート名は水曜日の日付です。")
    today = timezone.localdate()
    if week_end(week) < today:
        return _refuse(f"{label}はもう終わっています。終わった週のメニューは変えられません。")

    menus = [str(m or "").strip() for m in (body.get("menus") or [])]
    menus += [""] * (MENU_SLOTS - len(menus))
    blanks = [CIRCLED[i] for i, m in enumerate(menus[:MENU_SLOTS]) if not m]
    if blanks or len(menus) > MENU_SLOTS:
        if blanks:
            return _refuse(f"メニューの欄が空いています：{'・'.join(blanks)}\n"
                           "空いたまま送ると、メニューの番号がズレて登録されてしまうためです。")
        return _refuse("メニューが10品より多く送られてきました。開発部に連絡してください。")
    extra = [(CIRCLED[MENU_SLOTS + i], str(m).strip()) for i, m in enumerate(body.get("extra") or []) if str(m or "").strip()]
    if extra:
        return _refuse(f"⑪⑫の欄にメニューが入っています（{'、'.join(f'{c}：{n}' for c, n in extra)}）。\n"
                       "⑪⑫はまだアプリが対応していません。開発部に連絡してください。")

    result = register_week(week, menus)
    lines = [f"{label} {len(result.products)}品を登録しました（要確認 {len(result.needs_check)}品）。"]
    if result.state == "selling":
        lines.append("販売中の週を上書きしました。これから入力する日計表のメニューが変わります。")
    lines.append("確認画面を開きます。種類・容器・値段を確かめて［確認完了］を押してください。")
    logger.info("メニュー受信 %s：%d品（要確認 %d品・%s）", week, len(result.products), len(result.needs_check),
                result.state)
    return _menu_reply(True, "\n".join(lines), week=week.isoformat(), saved_count=len(result.products),
                       needs_check=result.needs_check, state=result.state,
                       url=request.build_absolute_uri(reverse("menu_week_detail", args=[week.isoformat()])))
