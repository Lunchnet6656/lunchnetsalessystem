# lunchnetsale/utils.py
"""ビュー横断で使う小さなヘルパー。"""


def is_report_owner(user, report):
    """日計表 report が user 本人のものかを判定する。

    submitted_by が記録されていない旧データ（この欄の追加前に作られた日計表）は、
    担当者名 person_in_charge とユーザーのフルネームの一致で本人とみなす。
    これは views.py の report_message_reply 等で既に使われている運用ルールに合わせたもの。
    """
    full_name = f"{user.last_name} {user.first_name}".strip()
    return (
        (report.submitted_by_id is not None and report.submitted_by_id == user.id)
        or (report.submitted_by_id is None and report.person_in_charge == full_name)
    )
