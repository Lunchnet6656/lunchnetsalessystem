from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sales'

    def ready(self):
        from django.db.models.signals import post_save
        from django.dispatch import receiver
        from django.contrib.auth import get_user_model

        User = get_user_model()

        @receiver(post_save, sender=User)
        def create_menu_permission(sender, instance, created, **kwargs):
            if created:
                from sales.models import UserMenuPermission
                perm = UserMenuPermission(user=instance)
                if instance.is_staff:
                    boolean_fields = [
                        'can_view_upload', 'can_view_register', 'can_view_mypage',
                        'can_view_user_list', 'can_view_location_list', 'can_view_product_list',
                        'can_view_item_quantity', 'can_view_others_item',
                        'can_view_daily_report_list', 'can_view_performance_data',
                        'can_view_performance_by_location', 'can_view_menu_history',
                        'can_view_daily_report_rol', 'can_view_performance_by_location_rol',
                        'can_view_daily_report_form', 'can_view_shift_app',
                    ]
                    for field in boolean_fields:
                        setattr(perm, field, True)
                perm.save()
