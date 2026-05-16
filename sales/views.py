# sales/views.py
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from .forms import UploadFileForm
import pandas as pd
from sales.models import SalesLocation
import openpyxl
import logging

logger = logging.getLogger(__name__)


def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'ユーザー名またはパスワードが正しくありません')
    return render(request, 'login.html')

def dashboard_view(request):
    return render(request, 'dashboard.html')

def upload_view(request):
    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            file = request.FILES['file']
            wb = openpyxl.load_workbook(file)
            sheet = wb.active

            logger.debug("ファイルの読み込みに成功しました")
            for row in sheet.iter_rows(min_row=2, values_only=True):
                name, type = row
                logger.debug("行データ: %s, %s", name, type)
                SalesLocation.objects.create(name=name, type=type)

            logger.info("販売場所データの保存に成功しました")
            return redirect('dashboard')  # ダッシュボードにリダイレクト
        else:
            logger.warning("アップロードフォームが無効です")
    else:
        form = UploadFileForm()
    return render(request, 'upload.html', {'form': form})
