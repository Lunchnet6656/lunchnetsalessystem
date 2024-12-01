#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys

def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lunchnetsale.settings')  # プロジェクト名に合わせて変更
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        if 'django' in str(exc):
            raise ImportError(
                "Couldn't import Django. Are you sure it's installed and "
                "available on your PYTHONPATH environment variable? Did you "
                "forget to activate a virtual environment?"
            )
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
