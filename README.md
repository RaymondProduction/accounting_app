# Accounting App

A web application for automatically recording expenses based on screenshots of receipts, invoices, and orders.

## Features

- Upload screenshots, receipts, and invoices.
- Automatically extract data using Gemini AI.
- Manually review and edit extracted information before saving.
- Upload images to Google Drive.
- Store expense records in Google Sheets.
- Supports Linux, Raspberry Pi, and other servers.

---

## Technology Stack

- Python 3.10+
- Flask
- Gemini API
- Google Drive API
- Google Sheets API
- OAuth 2.0

---

## Project Structure

```text
expense_ocr/
├── web_app.py
├── config.json
├── credentials.json
├── token.json
├── uploads/
├── logs/
└── README.md
```

---

## Installation

### Create a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### Install Dependencies

```bash
pip install \
    flask \
    google-genai \
    google-api-python-client \
    google-auth \
    google-auth-oauthlib \
    pydantic
```

---

## Gemini Configuration

Create an API key in Google AI Studio and add it to `config.json`.

---

## Google API Configuration

1. Create a Google Cloud Project.
2. Enable:
   - Google Drive API
   - Google Sheets API
3. Create an OAuth Client ID of type `Desktop App`.
4. Download `credentials.json`.

---

## First Authentication

Start the application:

```bash
python3 web_app.py
```

Open:

```text
http://127.0.0.1:8088/login-google
```

After successful authentication, a file named:

```text
token.json
```

will be created.

---

## Example Spreadsheet

| Timestamp | Description | Amount | Person | Receipt | Category | Budget |
| ---------- | ----------- | ------ | ------ | -------- | -------- | ------ |

---

## Example `config.json`

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "gemini_model": "gemini-2.5-flash",

  "oauth_credentials_file": "credentials.json",
  "oauth_token_file": "token.json",

  "spreadsheet_id": "GOOGLE_SHEET_ID",
  "sheet_name": "Expenses",

  "drive_folder_id": "GOOGLE_DRIVE_FOLDER_ID",

  "dry_run": false
}
```

---

## Running the Application

```bash
python3 web_app.py
```

Web interface:

```text
http://127.0.0.1:8088
```

or

```text
http://SERVER_IP:8088
```

---

## Workflow

```text
Image
  ↓
Gemini OCR
  ↓
User Validation
  ↓
Google Drive
  ↓
Google Sheets
```

---

## Security Notes

- Never commit `credentials.json`, `token.json`, or `config.json` to a public repository.
- Store API keys and OAuth credentials securely.
- Restrict access to the web application if it is exposed to the Internet.

Recommended `.gitignore`:

```gitignore
venv/
uploads/
__pycache__/
*.pyc
token.json
credentials.json
config.json
```

---

## Authentication

This application uses OAuth 2.0 and does not require a Google Service Account.

Required files:

- `credentials.json` – OAuth client credentials downloaded from Google Cloud Console.
- `token.json` – generated automatically after the first successful login.

A `service-account.json` file is not used because Google Drive uploads are performed on behalf of the authenticated user.

## License

MIT License.