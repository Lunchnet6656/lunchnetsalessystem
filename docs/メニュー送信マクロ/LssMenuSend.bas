Attribute VB_Name = "LssMenuSend"
'元のメニュー表（2024～メニュー表.xlsx）の週のシートから①～⑩のメニュー名を読み、LSS（salesアプリ）へ送る。
'元のメニュー表は読み取り専用で開き、保存も変更もしない。値段と容器はアプリがメニュー名から決める。
'送ったあとは必ずブラウザで「週のメニュー確認」画面を開く（種類・容器・値段を毎週人が確かめるため）。
Option Explicit

Private Const API_URL As String = "https://www.lunchnetsalessystem.com/api/menu/"
Private Const LSS_FOLDER As String = "\\192.168.111.250\Data\共有\◇LSS関係\"
Private Const TOKEN_FILE As String = "lss_token.txt"
Private Const MENU_BOOK As String = "\\192.168.111.250\Data\共有\○メニュー表\★メニュー表\2024～メニュー表.xlsx"
Private Const MSG_TITLE As String = "メニューの送信"
Private Const MENU_SLOTS As Long = 10
Private Const FIRST_MENU_ROW As Long = 4   '①はB4、②はB6…⑩はB22（奇数行はトッピングのメモ）。⑪はB24、⑫はB26

'エラーが出たときに「どこで止まったか」を表示するため
Private stepName As String

'[メニュー送信]ボタンから呼ぶ
Public Sub メニュー送信()
    Dim book As Workbook
    Dim openedHere As Boolean
    Dim weekDate As Date
    Dim answer As VbMsgBoxResult
    Dim names(1 To 10) As String
    Dim extra(1 To 2) As String
    Dim token As String
    Dim message As String
    Dim url As String
    Dim ok As Boolean

    On Error GoTo ErrHandler
    weekDate = 次の水曜(Date)

    stepName = "元のメニュー表を開く"
    Set book = メニュー表を開く(openedHere)
    If book Is Nothing Then
        MsgBox "送りませんでした。" & vbCrLf & "元のメニュー表が開けませんでした：" & vbCrLf & MENU_BOOK & vbCrLf & _
               "ファイルの場所が変わっていないか確かめてください。", vbExclamation, MSG_TITLE
        Exit Sub
    End If

    Do
        stepName = "週のシートを読む"
        If Not シートがある(book, Format(weekDate, "yyyymmdd")) Then
            MsgBox "送りませんでした。" & vbCrLf & "『" & Format(weekDate, "yyyymmdd") & "』のシートが元のメニュー表にありません。" & vbCrLf & _
                   "シート名（週の初日・水曜の日付）を確かめてください。", vbExclamation, MSG_TITLE
            GoTo Finish
        End If
        If Not メニューを読む(book.Sheets(Format(weekDate, "yyyymmdd")), names, extra) Then GoTo Finish

        answer = MsgBox(Format(weekDate, "m/d(aaa)") & "週のメニュー" & MENU_SLOTS & "品をアプリに送ります。" & vbCrLf & _
                        メニューの一覧(names) & vbCrLf & "（大盛りごはんはアプリが自動で足します）" & vbCrLf & vbCrLf & _
                        "［はい］送る　［いいえ］別の週を選ぶ　［キャンセル］やめる", vbYesNoCancel + vbQuestion, MSG_TITLE)
        If answer = vbCancel Then GoTo Finish
        If answer = vbYes Then Exit Do
        If Not 別の週を選ぶ(weekDate) Then GoTo Finish
    Loop

    stepName = "合言葉の読み込み"
    token = 合言葉を読む()
    If token = "" Then
        MsgBox "送れませんでした。" & vbCrLf & "合言葉ファイルが見つかりません：" & vbCrLf & LSS_FOLDER & TOKEN_FILE, vbExclamation, MSG_TITLE
        GoTo Finish
    End If

    Application.StatusBar = "メニューをアプリに送っています…"
    stepName = "アプリへの送信"
    ok = 送信(送る内容(weekDate, names, extra), token, message, url)
    Application.StatusBar = False
    If Not ok Then
        MsgBox message, vbExclamation, MSG_TITLE
        GoTo Finish
    End If

    If InStr(message, "販売中") > 0 Then
        MsgBox message, vbExclamation, MSG_TITLE
    Else
        MsgBox message, vbInformation, MSG_TITLE
    End If
    stepName = "確認画面を開く"
    On Error Resume Next
    ThisWorkbook.FollowHyperlink url
    If Err.Number <> 0 Or url = "" Then
        MsgBox "メニューは登録できています。確認画面だけ開けませんでした。" & vbCrLf & _
               "LSSの『週のメニュー確認』から" & Format(weekDate, "m/d") & "週を開いてください。", vbExclamation, MSG_TITLE
    End If
    On Error GoTo ErrHandler

Finish:
    'このマクロが開いたときだけ閉じる（保存しない）
    If openedHere Then book.Close SaveChanges:=False
    Exit Sub

ErrHandler:
    Application.StatusBar = False
    Application.ScreenUpdating = True
    MsgBox "送れませんでした。" & vbCrLf & "止まった所: " & stepName & vbCrLf & "内容: " & Err.Description & "（" & Err.Number & "）" & vbCrLf & vbCrLf & _
           "急ぐときは、LSSの『データアップロード』から今までのExcelでアップロードできます。", vbExclamation, MSG_TITLE
    On Error Resume Next
    If openedHere Then book.Close SaveChanges:=False
End Sub

'今日より後で一番近い水曜（木曜に翌週水曜からのメニューが決まるため）
Private Function 次の水曜(ByVal d As Date) As Date
    d = d + 1
    Do While Weekday(d) <> vbWednesday
        d = d + 1
    Loop
    次の水曜 = d
End Function

'すでに開いていればそれを使う（閉じない）。開いていなければ読み取り専用で開く
Private Function メニュー表を開く(ByRef openedHere As Boolean) As Workbook
    Dim wb As Workbook
    Dim fileName As String

    fileName = Mid(MENU_BOOK, InStrRev(MENU_BOOK, "\") + 1)
    For Each wb In Application.Workbooks
        If wb.Name = fileName Then
            Set メニュー表を開く = wb
            openedHere = False
            Exit Function
        End If
    Next wb
    If Dir(MENU_BOOK) = "" Then Exit Function
    Application.ScreenUpdating = False
    Set メニュー表を開く = Workbooks.Open(Filename:=MENU_BOOK, ReadOnly:=True, UpdateLinks:=0)
    ThisWorkbook.Activate
    Application.ScreenUpdating = True
    openedHere = True
End Function

Private Function シートがある(wb As Workbook, ByVal sheetName As String) As Boolean
    Dim sh As Object
    For Each sh In wb.Sheets
        If sh.Name = sheetName Then
            シートがある = True
            Exit Function
        End If
    Next sh
End Function

'①～⑩と⑪⑫を読む。空欄や⑪⑫に名前があれば理由を出して False
Private Function メニューを読む(ws As Worksheet, names() As String, extra() As String) As Boolean
    Dim i As Long
    Dim blanks As String
    Dim used As String

    For i = 1 To MENU_SLOTS
        names(i) = Trim(CStr(ws.Range("B" & (FIRST_MENU_ROW + (i - 1) * 2)).Value))
        If names(i) = "" Then blanks = blanks & 丸数字(i) & "（B" & (FIRST_MENU_ROW + (i - 1) * 2) & "）　"
    Next i
    For i = 1 To 2
        extra(i) = Trim(CStr(ws.Range("B" & (FIRST_MENU_ROW + (MENU_SLOTS + i - 1) * 2)).Value))
        If extra(i) <> "" Then used = used & 丸数字(MENU_SLOTS + i) & "：" & extra(i) & "　"
    Next i
    If blanks <> "" Then
        MsgBox "送りませんでした。" & vbCrLf & "メニューの欄が空いています：" & blanks & vbCrLf & _
               "空いたまま送ると、メニューの番号がズレて登録されてしまうためです。" & vbCrLf & _
               "元のメニュー表に入れてから、もう一度[メニュー送信]を押してください。", vbExclamation, MSG_TITLE
        Exit Function
    End If
    If used <> "" Then
        MsgBox "送りませんでした。" & vbCrLf & "⑪⑫の欄にメニューが入っています（" & used & "）。" & vbCrLf & _
               "⑪⑫はまだアプリが対応していません。開発部に連絡してください。", vbExclamation, MSG_TITLE
        Exit Function
    End If
    メニューを読む = True
End Function

Private Function 丸数字(ByVal n As Long) As String
    丸数字 = Mid("①②③④⑤⑥⑦⑧⑨⑩⑪⑫", n, 1)
End Function

Private Function メニューの一覧(names() As String) As String
    Dim i As Long
    Dim result As String
    For i = 1 To MENU_SLOTS
        result = result & 丸数字(i) & names(i)
        If i Mod 2 = 0 Then result = result & vbCrLf Else result = result & "　"
    Next i
    メニューの一覧 = result
End Function

'「いいえ」で別の週を選ぶ。キャンセルや読めない日付は False
Private Function 別の週を選ぶ(ByRef weekDate As Date) As Boolean
    Dim text As String
    Dim d As Date

    text = InputBox("送る週のシート名を入れてください（例：" & Format(weekDate + 7, "yyyymmdd") & "）", MSG_TITLE, Format(weekDate, "yyyymmdd"))
    If text = "" Then Exit Function
    If Len(text) <> 8 Or Not IsNumeric(text) Then
        MsgBox "送りませんでした。" & vbCrLf & "『" & text & "』は週のシート名として読めません（例：20261021）。", vbExclamation, MSG_TITLE
        Exit Function
    End If
    d = DateSerial(CInt(Left(text, 4)), CInt(Mid(text, 5, 2)), CInt(Right(text, 2)))
    If Weekday(d) <> vbWednesday Then
        MsgBox "送りませんでした。" & vbCrLf & "『" & text & "』は水曜日ではありません。週のシート名は水曜日の日付です。", vbExclamation, MSG_TITLE
        Exit Function
    End If
    weekDate = d
    別の週を選ぶ = True
End Function

'アプリに送るJSON：{"week": "2026-10-14", "menus": [...10品...], "extra": [⑪, ⑫]}
Private Function 送る内容(ByVal weekDate As Date, names() As String, extra() As String) As String
    Dim i As Long
    Dim json As String

    json = "{""week"": """ & Format(weekDate, "yyyy-mm-dd") & """, ""menus"": ["
    For i = 1 To MENU_SLOTS
        json = json & """" & JSON文字(names(i)) & """"
        If i < MENU_SLOTS Then json = json & ", "
    Next i
    json = json & "], ""extra"": [""" & JSON文字(extra(1)) & """, """ & JSON文字(extra(2)) & """]}"
    送る内容 = json
End Function

Private Function JSON文字(ByVal s As String) As String
    s = Replace(s, "\", "\\")
    s = Replace(s, """", "\""")
    s = Replace(s, vbCr, "")
    s = Replace(s, vbLf, "")
    s = Replace(s, vbTab, " ")
    JSON文字 = s
End Function

Private Function 合言葉を読む() As String
    '合言葉は英数字と - _ だけ。振分表と同じく、見えない文字（BOM・改行・全角空白）を落とす
    Dim stm As Object
    Dim raw As String
    Dim i As Long
    Dim ch As String
    Dim result As String

    If Dir(LSS_FOLDER & TOKEN_FILE) = "" Then Exit Function
    Set stm = CreateObject("ADODB.Stream")
    stm.Type = 2
    stm.Charset = "utf-8"
    stm.Open
    stm.LoadFromFile LSS_FOLDER & TOKEN_FILE
    raw = stm.ReadText
    stm.Close
    For i = 1 To Len(raw)
        ch = Mid(raw, i, 1)
        If ch Like "[A-Za-z0-9_-]" Then result = result & ch
    Next i
    合言葉を読む = result
End Function

Private Function 送信(ByVal json As String, ByVal token As String, ByRef message As String, ByRef url As String) As Boolean
    'Byte() 型のまま send に渡すと「パラメーターが間違っています」になる。Variant で渡す
    Dim body As Variant
    Dim http As Object
    Dim respText As String

    body = UTF8にする(json)
    Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    On Error GoTo NetError
    http.setTimeouts 5000, 5000, 15000, 30000
    http.Open "POST", API_URL, False
    http.setRequestHeader "Authorization", "Bearer " & token
    http.setRequestHeader "Content-Type", "application/json; charset=utf-8"
    http.send body
    On Error GoTo 0

    respText = 返事を読む(http)
    message = 値を取り出す(respText, "message")
    url = 値を取り出す(respText, "url")
    If message = "" Then message = "アプリから想定外の返事がありました（" & http.Status & "）"
    送信 = (http.Status = 200)
    Exit Function

NetError:
    message = "メニューをアプリに送れませんでした（インターネットの接続か、合言葉ファイルの中身を確認してください）。" & vbCrLf & "内容: " & Err.Description & "（" & Err.Number & "）"
    送信 = False
End Function

'文字列をUTF-8のバイト列にする（先頭のBOM 3バイトは送らない）
Private Function UTF8にする(ByVal text As String) As Variant
    Dim stm As Object
    Set stm = CreateObject("ADODB.Stream")
    stm.Type = 2
    stm.Charset = "utf-8"
    stm.Open
    stm.WriteText text
    stm.Position = 0
    stm.Type = 1
    stm.Position = 3
    UTF8にする = stm.Read
    stm.Close
End Function

Private Function 返事を読む(http As Object) As String
    Dim stm As Object
    Set stm = CreateObject("ADODB.Stream")
    stm.Type = 1
    stm.Open
    stm.Write http.responseBody
    stm.Position = 0
    stm.Type = 2
    stm.Charset = "utf-8"
    返事を読む = stm.ReadText
    stm.Close
End Function

'返事のJSONから "key": "…" の値を取り出す
Private Function 値を取り出す(ByVal respText As String, ByVal key As String) As String
    Dim p As Long
    Dim q As Long
    Dim value As String

    p = InStr(respText, """" & key & """: """)
    If p = 0 Then Exit Function
    p = p + Len("""" & key & """: """)
    q = p
    Do While q <= Len(respText)
        If Mid(respText, q, 1) = """" And Mid(respText, q - 1, 1) <> "\" Then Exit Do
        q = q + 1
    Loop
    value = Mid(respText, p, q - p)
    value = Replace(value, "\n", vbCrLf)
    value = Replace(value, "\""", """")
    value = Replace(value, "\/", "/")
    値を取り出す = value
End Function
