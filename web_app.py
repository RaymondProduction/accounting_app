#!/usr/bin/env python3

import json
import re
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, request, render_template_string, send_from_directory
from pydantic import BaseModel
from google import genai
from google.genai import types

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
CONFIG_FILE = BASE_DIR / "config.json"

UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)


class FormData(BaseModel):
    what_bought: Optional[str] = None
    amount: Optional[int] = None
    budget: Optional[str] = None
    person: Optional[str] = None
    category: Optional[str] = None


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_amount(value):
    if value is None:
        return None

    text = str(value)
    digits = re.findall(r"\d+", text.replace(" ", ""))

    if not digits:
        return None

    return int("".join(digits))


def analyze_image(image_path: Path):
    cfg = load_config()

    client = genai.Client(api_key=cfg["gemini_api_key"])
    image_bytes = image_path.read_bytes()

    mime_type = "image/png"
    if image_path.suffix.lower() in [".jpg", ".jpeg"]:
        mime_type = "image/jpeg"
    elif image_path.suffix.lower() == ".webp":
        mime_type = "image/webp"

    prompt = """
Прочитай скріншот чеку, накладної, замовлення або Google Form.

Поверни JSON:
{
  "what_bought": "що купили або що купуємо",
  "amount": число суми тільки ціле,
  "budget": null,
  "person": "Раймонд",
  "category": null
}

Правила:
- amount тільки ціле число без грн/UAH/пробілів/ком.
- Якщо товарів кілька — коротко перелічити в what_bought.
- Якщо категорію не очевидно — null.
- Якщо бюджет не видно — null.
- Не вигадуй значення.
"""

    response = client.models.generate_content(
        model=cfg["gemini_model"],
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config={
            "response_mime_type": "application/json",
            "response_schema": FormData,
        },
    )

    data = json.loads(response.text)
    data["amount"] = normalize_amount(data.get("amount"))
    return data




SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def get_google_services(cfg):
    token_path = BASE_DIR / cfg["oauth_token_file"]
    creds_path = BASE_DIR / cfg["oauth_credentials_file"]

    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    return sheets, drive


def upload_receipt_to_drive(cfg, drive, image_path: Path):
    file_metadata = {
        "name": image_path.name,
        "parents": [cfg["drive_folder_id"]],
    }

    media = MediaFileUpload(str(image_path), resumable=False)

    uploaded = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    return uploaded["webViewLink"]


def _col_letter(index):
    """0-based індекс колонки -> літера (0 -> A, 26 -> AA)."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _find_table(sheets, cfg):
    """Повертає (sheet_id, table) для потрібного аркуша; table може бути None.

    Якщо в config є "table_name" — шукаємо саме її, інакше беремо першу
    таблицю аркуша.
    """
    meta = sheets.spreadsheets().get(
        spreadsheetId=cfg["spreadsheet_id"],
        fields="sheets(properties(title,sheetId),tables(tableId,name,range))",
    ).execute()

    for s in meta.get("sheets", []):
        if s["properties"]["title"] != cfg["sheet_name"]:
            continue
        sheet_id = s["properties"]["sheetId"]
        tables = s.get("tables", [])
        if not tables:
            return sheet_id, None
        wanted = cfg.get("table_name")
        table = next((t for t in tables if t.get("name") == wanted), tables[0])
        return sheet_id, table

    raise RuntimeError(f"Аркуш '{cfg['sheet_name']}' не знайдено у книзі")


def save_to_google_sheet(data, image_path: Path):
    cfg = load_config()

    if cfg.get("dry_run"):
        print("[DRY RUN]", data, image_path)
        return "DRY RUN: у Google Sheet не записано"

    sheets, drive = get_google_services(cfg)

    receipt_link = upload_receipt_to_drive(cfg, drive, image_path)

    now = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%d.%m.%Y %H:%M:%S")

    row = [
        now,
        data.get("what_bought") or "",
        data.get("amount") or "",
        data.get("person") or "",
        receipt_link,
        data.get("category") or "",
        data.get("budget") or "",
    ]

    # На аркуші є нативна таблиця Google Sheets (Вставка -> Таблиці) з
    # фіксованим діапазоном. Якщо просто записати значення у перший
    # вільний рядок під нею через values().update(), клітинка опиняється
    # ЗА межами таблиці: без форматування, поза фільтром/сортуванням.
    #
    # Тому:
    #   1. Знаходимо перший порожній рядок у ТІЛІ таблиці й пишемо в нього
    #      (форматування він успадкує від таблиці автоматично). Нічого
    #      не зсуваємо — цей рядок і так уже всередині таблиці.
    #   2. Якщо тіло таблиці заповнене вщент — вставляємо новий рядок
    #      одразу під таблицею через insertDimension. Усе, що лежить
    #      нижче (напр. ручна таблиця бюджетів), зсувається вниз — так
    #      само поводиться Google Forms, тому дані під таблицею ніколи
    #      не перезаписуються. Потім розширюємо діапазон таблиці на цей
    #      новий порожній рядок через updateTable і пишемо в нього.
    #
    # Якщо таблиці на аркуші немає — стара логіка: перший рядок після
    # останнього непорожнього в колонці A.
    sheet_id, table = _find_table(sheets, cfg)

    if table is None:
        existing = sheets.spreadsheets().values().get(
            spreadsheetId=cfg["spreadsheet_id"],
            range=f"{cfg['sheet_name']}!A:A",
        ).execute()
        values = existing.get("values", [])
        last_row = 0
        for i, v in enumerate(values, start=1):
            if v and v[0]:
                last_row = i
        next_row = last_row + 1
        start_col = 0
    else:
        rng = table["range"]
        r0 = rng.get("startRowIndex", 0)      # 0-based, рядок заголовка
        r1 = rng["endRowIndex"]               # 0-based, ексклюзивно
        start_col = rng.get("startColumnIndex", 0)
        col_a = _col_letter(start_col)

        first_body = r0 + 2                   # 1-based, перший рядок тіла
        last_body = r1                        # 1-based, останній рядок тіла

        col_values = sheets.spreadsheets().values().get(
            spreadsheetId=cfg["spreadsheet_id"],
            range=f"{cfg['sheet_name']}!{col_a}{first_body}:{col_a}{last_body}",
        ).execute().get("values", [])

        next_row = None
        for j in range(last_body - first_body + 1):
            cell = col_values[j] if j < len(col_values) else []
            if not cell or not cell[0]:
                next_row = first_body + j
                break

        if next_row is None:
            next_row = last_body + 1
            new_range = dict(rng)
            new_range["endRowIndex"] = r1 + 1
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=cfg["spreadsheet_id"],
                body={"requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": r1,
                                "endIndex": r1 + 1,
                            },
                            "inheritFromBefore": True,
                        }
                    },
                    {
                        "updateTable": {
                            "table": {"tableId": table["tableId"], "range": new_range},
                            "fields": "range",
                        }
                    },
                ]},
            ).execute()

    start_letter = _col_letter(start_col)
    end_letter = _col_letter(start_col + len(row) - 1)

    sheets.spreadsheets().values().update(
        spreadsheetId=cfg["spreadsheet_id"],
        range=f"{cfg['sheet_name']}!{start_letter}{next_row}:{end_letter}{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()

    return "✅ Записано в Google Sheet"


HTML = """
<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8">
  <title>Витрати майстерня</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      max-width: 760px;
      margin: 30px auto;
      padding: 0 14px;
    }
    label {
      display: block;
      margin-top: 14px;
      font-weight: bold;
    }
    input, select {
      width: 100%;
      padding: 10px;
      font-size: 16px;
      box-sizing: border-box;
    }
    button {
      margin-top: 20px;
      padding: 12px 18px;
      font-size: 16px;
      cursor: pointer;
    }
    img {
      max-width: 100%;
      margin-top: 20px;
      border: 1px solid #ccc;
    }
    .box {
      background: #f5f5f5;
      padding: 12px;
      border-radius: 8px;
      margin-top: 18px;
    }
  </style>
</head>
<body>

<h2>Витрати майстерня</h2>

{% if step == "upload" %}
<form method="post" enctype="multipart/form-data" action="/analyze">
  <label>Скріншот / чек / накладна</label>
  <input type="file" name="image" accept="image/*" required>
  <button type="submit">Розпізнати</button>
</form>
{% endif %}

{% if step == "edit" %}
<div class="box">
  Перевір дані. Можна руками виправити перед записом у таблицю.
</div>

<form method="post" action="/submit">
  <input type="hidden" name="image_path" value="{{ image_path }}">

  <label>Що купуємо?</label>
  <input name="what_bought" value="{{ data.what_bought or '' }}">

  <label>Сума</label>
  <input name="amount" value="{{ data.amount or '' }}">

  <label>Бюджет</label>
  <select name="budget">
    <option value="">Не вибрано</option>
    {% for v in budgets %}
      <option value="{{v}}" {% if data.budget == v %}selected{% endif %}>{{v}}</option>
    {% endfor %}
  </select>

  <label>Хто?</label>
  <select name="person">
    <option value="">Не вибрано</option>
    {% for v in persons %}
      <option value="{{v}}" {% if data.person == v %}selected{% endif %}>{{v}}</option>
    {% endfor %}
  </select>

  <label>Категорія</label>
  <select name="category">
    <option value="">Не вибрано</option>
    {% for v in categories %}
      <option value="{{v}}" {% if data.category == v %}selected{% endif %}>{{v}}</option>
    {% endfor %}
  </select>

  <button type="submit">Записати в Google Sheet</button>
</form>

<img src="/{{ image_path }}">
{% endif %}

{% if message %}
<h3>{{ message }}</h3>
<a href="/">Додати ще</a>
{% endif %}

</body>
</html>
"""


def template_options():
    cfg = load_config()

    return {
        "budgets": cfg.get("budgets", []),
        "persons": cfg.get("persons", []),
        "categories": cfg.get("categories", []),
    }


@app.route("/", methods=["GET"])
def index():
    return render_template_string(
        HTML,
        step="upload",
        message=None,
        **template_options(),
    )


@app.route("/analyze", methods=["POST"])
def analyze():
    file = request.files["image"]

    ext = Path(file.filename).suffix or ".png"
    filename = f"{uuid.uuid4()}{ext}"
    image_path = UPLOAD_DIR / filename
    file.save(image_path)

    data = analyze_image(image_path)

    return render_template_string(
        HTML,
        step="edit",
        data=data,
        image_path=f"uploads/{filename}",
        message=None,
        **template_options(),
    )


@app.route("/submit", methods=["POST"])
def submit():
    image_path = BASE_DIR / request.form["image_path"]

    data = {
        "what_bought": request.form.get("what_bought"),
        "amount": normalize_amount(request.form.get("amount")),
        "budget": request.form.get("budget") or None,
        "person": request.form.get("person") or None,
        "category": request.form.get("category") or None,
    }

    message = save_to_google_sheet(data, image_path)

    return render_template_string(
        HTML,
        step="done",
        message=message,
        **template_options(),
    )

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/login-google")
def login_google():
    cfg = load_config()
    get_google_services(cfg)
    return "✅ Google авторизація виконана. token.json створено."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8088, debug=True)