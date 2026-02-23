from django.db import migrations


def seed_templates(apps, schema_editor):
    NotificationTemplate = apps.get_model('shifts', 'NotificationTemplate')
    defaults = [
        {
            'notification_type': 'OPEN',
            'is_enabled': True,
            'title_template': '【シフト募集開始】',
            'body_template': (
                '新しいシフト希望募集が始まりました。\n'
                '対象期間: {period_range}\n'
                '提出締切: {deadline}\n\n'
                'アプリからシフト希望を提出してください。'
            ),
        },
        {
            'notification_type': 'REMINDER',
            'is_enabled': True,
            'title_template': '【シフト提出リマインド】締切が迫っています',
            'body_template': (
                'シフト希望の締切が近づいています。\n'
                '対象期間: {period_range}\n'
                '提出締切: {deadline}\n\n'
                'まだ未提出の方はお早めに提出してください。'
            ),
        },
        {
            'notification_type': 'MANUAL',
            'is_enabled': True,
            'title_template': '【シフト提出のお願い】',
            'body_template': (
                'シフト希望をまだご提出いただいていません。\n'
                '対象期間: {period_range}\n'
                '提出締切: {deadline}\n\n'
                '早急にアプリからご提出ください。'
            ),
        },
        {
            'notification_type': 'PUBLISHED',
            'is_enabled': True,
            'title_template': '【シフト公開】',
            'body_template': (
                'シフトが公開されました。\n'
                '対象期間: {period_range}\n\n'
                'アプリから確定シフトをご確認ください。'
            ),
        },
        {
            'notification_type': 'ASSIGNMENT_CHANGED',
            'is_enabled': True,
            'title_template': '【シフト変更のお知らせ】',
            'body_template': (
                '公開済みシフトに変更がありました。\n'
                '対象期間: {period_range}\n\n'
                'アプリから最新のシフトをご確認ください。'
            ),
        },
    ]
    for data in defaults:
        NotificationTemplate.objects.get_or_create(
            notification_type=data['notification_type'],
            defaults=data,
        )


def reverse(apps, schema_editor):
    NotificationTemplate = apps.get_model('shifts', 'NotificationTemplate')
    NotificationTemplate.objects.filter(
        notification_type__in=['OPEN', 'REMINDER', 'MANUAL', 'PUBLISHED', 'ASSIGNMENT_CHANGED'],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('shifts', '0010_notificationtemplate'),
    ]

    operations = [
        migrations.RunPython(seed_templates, reverse),
    ]
