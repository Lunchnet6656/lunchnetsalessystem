Attribute VB_Name = "LssAutoSend"
'振分表の持参数をLSS（salesアプリ）へ送る。最終決定のあとに[確定して送る]ボタンで1回だけ押す。
'仮の数字は送らない（保存では送らない）。押し忘れは19:30にアプリからLINEで知らせる。
'送れたときだけ振分表をB4で印刷する（紙とアプリの数字を必ず一致させるため）。
Option Explicit

Private Const API_URL As String = "https://www.lunchnetsalessystem.com/api/item-quantity/"
Private Const LSS_FOLDER As String = "\\192.168.111.250\Data\共有\◇LSS関係\"
Private Const TOKEN_FILE As String = "lss_token.txt"
Private Const OMORI_ROW As Long = 41
Private Const OMORI_NO As Long = 11
Private Const MSG_TITLE As String = "持参数の確定"

'[確定して送る]ボタン（旧[データ変換]ボタン）から呼ぶ。ボタンの登録先はそのままでよいよう名前を引き継ぐ
Public Sub データアップロード用変換()
    Dim wsSource As Worksheet
    Dim targetDate As Date
    Dim problems As String
    Dim filePath As String
    Dim token As String
    Dim message As String

    On Error GoTo ErrHandler
    Set wsSource = ThisWorkbook.Sheets("振分表")
    If Not IsDate(wsSource.Range("AP1").Value) Then
        MsgBox "振分表のAP1に日付が入っていません。", vbExclamation, MSG_TITLE
        Exit Sub
    End If
    targetDate = wsSource.Range("AP1").Value

    If MsgBox(Format(targetDate, "m/d(aaa)") & " 分の持参数を確定してアプリに送ります。" & vbCrLf & _
              "送れたら振分表をB4で印刷します。よろしいですか？", vbYesNo + vbQuestion, MSG_TITLE) = vbNo Then Exit Sub

    problems = 空白チェック(wsSource)
    If problems <> "" Then
        MsgBox "送りませんでした（印刷もしていません）。" & vbCrLf & _
               "空白のセルがあると、数字が隣の店にズレて登録されてしまうためです。" & vbCrLf & vbCrLf & _
               problems & vbCrLf & "0 を入れてから、もう一度[確定して送る]を押してください。", vbExclamation, MSG_TITLE
        Exit Sub
    End If

    token = 合言葉を読む()
    If token = "" Then
        MsgBox "送れませんでした（印刷もしていません）。" & vbCrLf & _
               "合言葉ファイルが見つかりません：" & vbCrLf & LSS_FOLDER & TOKEN_FILE, vbExclamation, MSG_TITLE
        Exit Sub
    End If

    Application.StatusBar = "持参数をアプリに送っています…"
    filePath = 送信ファイル作成(wsSource, targetDate)

    If 送信(filePath, token, message) Then
        Application.StatusBar = "振分表を印刷しています…"
        wsSource.PrintOut
        Application.StatusBar = False
        MsgBox message & vbCrLf & vbCrLf & "振分表をB4で印刷しました。", vbInformation, MSG_TITLE
    Else
        Application.StatusBar = False
        MsgBox message & vbCrLf & vbCrLf & "（印刷はしていません）", vbExclamation, MSG_TITLE
    End If
    Exit Sub

ErrHandler:
    Application.StatusBar = False
    Application.EnableEvents = True
    Application.DisplayAlerts = True
    Application.ScreenUpdating = True
    MsgBox "送れませんでした（印刷もしていません）。" & vbCrLf & "内容: " & Err.Description & vbCrLf & vbCrLf & _
           "急ぐときはLSSの画面からアップロードしてください。", vbExclamation, MSG_TITLE
End Sub

'送信対象の店（店舗名変換にある店）の列で、メニュー行・大盛り行に空白がないか
Private Function 空白チェック(ws As Worksheet) As String
    Dim conv As Object
    Dim cols As Collection
    Dim r As Variant
    Dim c As Variant
    Dim result As String

    Set conv = 店舗名変換を読む()
    Set cols = 送信列(ws, conv)
    For Each r In 送信行(ws)
        For Each c In cols
            If Trim(CStr(ws.Cells(r, c).Value)) = "" Then
                result = result & "・" & 行の名前(ws, CLng(r)) & " の「" & ws.Cells(7, c).Value & "」" & vbCrLf
            End If
        Next c
    Next r
    空白チェック = result
End Function

Private Function 行の名前(ws As Worksheet, ByVal r As Long) As String
    If r = OMORI_ROW Then
        行の名前 = "大盛りご飯の数"
    Else
        行の名前 = "No." & ws.Cells(r, 1).Value & " " & ws.Cells(r, 2).Value
    End If
End Function

'これまでの[データ変換]と同じ形のxlsxを作る（シート名=日付、A=対象wk、B=No.、C～=店）
Private Function 送信ファイル作成(ws As Worksheet, ByVal targetDate As Date) As String
    Dim conv As Object
    Dim cols As Collection
    Dim newBook As Workbook
    Dim sh As Worksheet
    Dim outRow As Long
    Dim outCol As Long
    Dim r As Variant
    Dim c As Variant
    Dim folder As String
    Dim outPath As String

    Set conv = 店舗名変換を読む()
    Set cols = 送信列(ws, conv)

    Application.ScreenUpdating = False
    Application.EnableEvents = False
    Set newBook = Workbooks.Add
    Set sh = newBook.Sheets(1)
    sh.Name = Format(targetDate, "yyyymmdd")
    sh.Cells(1, 1).Value = "対象wk"
    sh.Cells(1, 2).Value = "No."
    outCol = 3
    For Each c In cols
        sh.Cells(1, outCol).Value = conv(CStr(ws.Cells(7, c).Value))
        outCol = outCol + 1
    Next c

    outRow = 2
    For Each r In 送信行(ws)
        sh.Cells(outRow, 1).Value = ThisWorkbook.Sheets(2).Name
        If r = OMORI_ROW Then
            sh.Cells(outRow, 2).Value = OMORI_NO
        Else
            sh.Cells(outRow, 2).Value = ws.Cells(r, 1).Value
        End If
        outCol = 3
        For Each c In cols
            sh.Cells(outRow, outCol).Value = ws.Cells(r, c).Value
            outCol = outCol + 1
        Next c
        outRow = outRow + 1
    Next r

    'NASに控えを残す（つながらないときはPCの一時フォルダ）
    folder = LSS_FOLDER
    If Dir(folder, vbDirectory) = "" Then folder = Environ("TEMP") & "\"
    outPath = folder & "振分け設定データアップロード用" & Format(targetDate, "yyyymmdd") & ".xlsx"
    Application.DisplayAlerts = False
    newBook.SaveAs Filename:=outPath, FileFormat:=xlOpenXMLWorkbook
    newBook.Close SaveChanges:=False
    Application.DisplayAlerts = True
    Application.EnableEvents = True
    Application.ScreenUpdating = True
    送信ファイル作成 = outPath
End Function

'7行目の店名を左から見て、店舗名変換にある店の列。「店」の列で終わり（これまでの[データ変換]と同じ）
Private Function 送信列(ws As Worksheet, conv As Object) As Collection
    Dim cols As New Collection
    Dim lastCol As Long
    Dim col As Long
    Dim storeName As String

    lastCol = ws.Cells(7, ws.Columns.Count).End(xlToLeft).Column
    For col = 3 To lastCol
        storeName = CStr(ws.Cells(7, col).Value)
        If storeName <> "" Then
            If conv.Exists(storeName) Then cols.Add col
            If InStr(storeName, "店") > 0 Then Exit For
        End If
    Next col
    Set 送信列 = cols
End Function

'A列が1～10のメニュー行＋大盛りご飯の行
Private Function 送信行(ws As Worksheet) As Collection
    Dim targetRows As New Collection
    Dim cell As Range

    For Each cell In ws.Range("A8:A35")
        If IsNumeric(cell.Value) And Not IsEmpty(cell.Value) Then
            If cell.Value >= 1 And cell.Value <= 10 Then targetRows.Add cell.Row
        End If
    Next cell
    targetRows.Add OMORI_ROW
    Set 送信行 = targetRows
End Function

Private Function 店舗名変換を読む() As Object
    Dim conv As Object
    Dim ws As Worksheet
    Dim r As Long

    Set conv = CreateObject("Scripting.Dictionary")
    Set ws = ThisWorkbook.Sheets("店舗名変換")
    r = 2
    Do While ws.Cells(r, 1).Value <> ""
        conv(CStr(ws.Cells(r, 1).Value)) = ws.Cells(r, 2).Value
        r = r + 1
    Loop
    Set 店舗名変換を読む = conv
End Function

Private Function 合言葉を読む() As String
    Dim f As Integer
    Dim tokenLine As String

    If Dir(LSS_FOLDER & TOKEN_FILE) = "" Then Exit Function
    f = FreeFile
    Open LSS_FOLDER & TOKEN_FILE For Input As #f
    If Not EOF(f) Then Line Input #f, tokenLine
    Close #f
    合言葉を読む = Trim(tokenLine)
End Function

Private Function 送信(ByVal xlsxPath As String, ByVal token As String, ByRef message As String) As Boolean
    Dim stm As Object
    Dim body() As Byte
    Dim http As Object

    Set stm = CreateObject("ADODB.Stream")
    stm.Type = 1
    stm.Open
    stm.LoadFromFile xlsxPath
    body = stm.Read
    stm.Close

    Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    http.setTimeouts 5000, 5000, 15000, 30000
    http.Open "POST", API_URL, False
    http.setRequestHeader "Authorization", "Bearer " & token
    http.setRequestHeader "Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    On Error GoTo NetError
    http.send body
    On Error GoTo 0

    message = 返事の文(http)
    If message = "" Then message = "アプリから想定外の返事がありました（" & http.Status & "）"
    送信 = (http.Status = 200)
    Exit Function

NetError:
    message = "持参数をアプリに送れませんでした（インターネットにつながらない可能性）。" & vbCrLf & "内容: " & Err.Description
    送信 = False
End Function

'返事のJSONから message だけ取り出す（UTF-8で読む）
Private Function 返事の文(http As Object) As String
    Dim stm As Object
    Dim respText As String
    Dim p As Long
    Dim q As Long

    Set stm = CreateObject("ADODB.Stream")
    stm.Type = 1
    stm.Open
    stm.Write http.responseBody
    stm.Position = 0
    stm.Type = 2
    stm.Charset = "utf-8"
    respText = stm.ReadText
    stm.Close

    p = InStr(respText, """message"": """)
    If p = 0 Then Exit Function
    p = p + Len("""message"": """)
    q = p
    Do While q <= Len(respText)
        If Mid(respText, q, 1) = """" And Mid(respText, q - 1, 1) <> "\" Then Exit Do
        q = q + 1
    Loop
    respText = Mid(respText, p, q - p)
    respText = Replace(respText, "\n", vbCrLf)
    respText = Replace(respText, "\""", """")
    返事の文 = respText
End Function
