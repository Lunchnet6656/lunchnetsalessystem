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

            print("ファイルの読み込みに成功しました")  # デバッグ用
            for row in sheet.iter_rows(min_row=2, values_only=True):
                name, type = row
                print(f"行データ: {name}, {type}")  # デバッグ用
                SalesLocation.objects.create(name=name, type=type)

            print("データベースへの保存に成功しました")  # デバッグ用
            return redirect('dashboard')  # ダッシュボードにリダイレクト
        else:
            print("フォームが無効です")  # デバッグ用
    else:
        form = UploadFileForm()
    return render(request, 'upload.html', {'form': form})
