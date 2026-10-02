"""振分表マクロ（事務所PC）から持参数を受け取るAPI。

振分表の[確定して送る]ボタンで送られてくる（仮の数字は送らない）。同じ日付は最新の数字で上書きする。
ログイン画面を経由できない（VBAから直接叩く）ため、セッションではなく Bearer トークンで認証する。
"""
import io
import logging
import secrets

from django.conf import settings
from django.core.management import call_command
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from sales.item_quantity_import import parse_item_quantity_workbook, save_item_quantities
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
