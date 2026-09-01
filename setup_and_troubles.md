# Google API Setup and Troubleshooting

This document describes the authentication approaches that were tried during development and the issues encountered while integrating Google Drive and Google Sheets.

---

# Initial Approach: Service Account

The first implementation used a Google Service Account.

Architecture:

```text
Expense OCR
    ↓
Service Account
    ↓
Google Drive
    ↓
Google Sheets
```

Advantages:

* No browser required.
* Works well on headless servers.
* Easy to deploy.

However, several issues were encountered.

---

# Problem 1: Wrong Google Drive Folder ID

Error:

```text
HttpError 404
File not found: <folder_id>
```

Cause:

The value stored in `drive_folder_id` was not actually a Google Drive folder ID. In one case, a spreadsheet ID was accidentally used instead.

Incorrect:

```json
"drive_folder_id": "spreadsheet_id_here"
```

Correct:

```text
https://drive.google.com/drive/folders/FOLDER_ID
```

Configuration:

```json
"drive_folder_id": "FOLDER_ID"
```

---

# Problem 2: Service Account Had No Access to the Folder

Error:

```text
HttpError 403
Insufficient permissions for the specified parent.
```

Cause:

The Google Drive folder was not shared with the Service Account.

Solution:

1. Open `service-account.json`.
2. Copy:

```json
"client_email": "xxxx@project.iam.gserviceaccount.com"
```

3. Open the Google Drive folder.
4. Click **Share**.
5. Add the Service Account email.
6. Grant **Editor** permissions.

The same permission must also be granted to the Google Spreadsheet.

---

# Problem 3: Service Account Has No Storage Quota

Error:

```text
HttpError 403
Service Accounts do not have storage quota.
```

Cause:

A Service Account does not own a personal Google Drive and therefore cannot upload files into a regular user's My Drive.

This is a Google limitation.

Because of this limitation, the project migrated from Service Account authentication to OAuth authentication.

---

# Final Architecture: OAuth

Architecture:

```text
Expense OCR
    ↓
OAuth
    ↓
User's Google Drive
    ↓
Google Sheets
```

Advantages:

* Files are uploaded directly into the user's Google Drive.
* No storage quota issues.
* Works with regular Gmail accounts.
* After the first login, no browser interaction is required.

---

# OAuth Setup

## Step 1

Create a Google Cloud Project.

---

## Step 2

Enable:

* Google Drive API
* Google Sheets API

---

## Step 3

Configure OAuth Consent Screen.

Settings:

```text
User Type:
External

Publishing Status:
Testing
```

---

## Step 4

Create OAuth Credentials.

```text
APIs & Services
→ Credentials
→ Create Credentials
→ OAuth Client ID
→ Desktop App
```

Download:

```text
credentials.json
```

---

# Problem 4: Unauthorized User

Error:

```text
Access blocked: This app's request is invalid
```

or

```text
The app is requesting access to sensitive information.
```

or

```text
This app is in testing mode.
```

Cause:

The Google account used for login was not added as a test user.

Solution:

```text
Google Cloud Console
→ OAuth Consent Screen
→ Audience
→ Test Users
→ Add Users
```

Add:

```text
your-email@gmail.com
```

---

# Problem 5: Missing credentials.json

Error:

```text
FileNotFoundError:
credentials.json
```

Cause:

OAuth credentials were not downloaded or placed into the project directory.

Correct project structure:

```text
expense_ocr/
├── web_app.py
├── config.json
├── credentials.json
├── token.json
├── uploads/
└── README.md
```

---

# Problem 6: Missing token.json

This is not an error.

`token.json` is generated automatically after the first successful OAuth login.

To force re-authentication:

```bash
rm token.json
```

Then restart the application.

---

# Problem 7: Invalid Spreadsheet Range

Error:

```text
Unable to parse range:
Form Responses 1!A:G
```

Cause:

The worksheet name in `config.json` did not match the actual tab name inside Google Sheets.

Incorrect:

```json
"sheet_name": "Form Responses 1"
```

Actual worksheet:

```text
Expenses
```

Correct:

```json
"sheet_name": "Expenses"
```

It is also recommended to always quote worksheet names:

```python
range=f"'{cfg['sheet_name']}'!A:G"
```

This avoids issues with spaces and non-English characters.

---

# Problem 8: Spreadsheet Permissions

Error:

```text
HttpError 403
The caller does not have permission.
```

Cause:

The authenticated account or Service Account did not have access to the spreadsheet.

Solution:

Open Google Sheets:

```text
Share
→ Add user
→ Grant Editor permissions
```

---

# Problem 9: Wrong Column Order

The spreadsheet columns did not match the order used in code.

Spreadsheet:

```text
Timestamp
Description
Amount
Person
Receipt
Category
Budget
```

Code:

```python
[
    timestamp,
    description,
    amount,
    budget,
    person,
    receipt,
    category
]
```

Result:

Values appeared under incorrect columns.

Solution:

```python
[
    timestamp,
    description,
    amount,
    person,
    receipt,
    category,
    budget
]
```

A more robust approach would be to read the header row and map columns by name instead of position.

---

# Problem 10: Uploaded Image Returned 404

Error:

```text
GET /uploads/file.jpg HTTP/1.1 404
```

Cause:

Flask had no route to serve uploaded images.

Solution:

```python
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)
```

---

# Problem 11: New Row Added Outside the Formatted Table

Symptom:

The expense sheet contains a native Google Sheets table (Insert -> Tables)
with a fixed range. Rows written by the app landed *below* that range:
no banding, not covered by the table filter/sort, visually detached.

Cause:

The code wrote values with `spreadsheets.values.update()` into the first
free row after the last non-empty cell in column A. That row is outside
the table's defined `range`, so Sheets treats it as a plain cell, not a
table row. Unlike typing in the UI, a raw values write does not expand a
table's range.

Solution:

Write *into* the table instead:

1. Read book metadata and locate the table:

```python
meta = sheets.spreadsheets().get(
    spreadsheetId=cfg["spreadsheet_id"],
    fields="sheets(properties(title),tables(tableId,name,range))",
).execute()
```

2. Find the first empty row inside the table body (scan column A from the
   row after the header to `range.endRowIndex`) and write there. The new
   row inherits the table formatting automatically.

3. If the table body is completely full, do NOT just widen the table
   range with `UpdateTableRequest` alone: that overlays the table onto
   whatever is already filled below it (e.g. a manual budget table) and
   the following write overwrites those cells.

   Instead insert a real row right below the table first, so everything
   below shifts down (this is what Google Forms does when appending a
   response), then extend the table over that new blank row and write to
   it:

```python
sheets.spreadsheets().batchUpdate(
    spreadsheetId=cfg["spreadsheet_id"],
    body={"requests": [
        {
            "insertDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": end_row_index,       # row just below the table
                    "endIndex": end_row_index + 1,
                },
                "inheritFromBefore": True,
            }
        },
        {
            "updateTable": {
                "table": {"tableId": table_id, "range": new_range},  # endRowIndex + 1
                "fields": "range",
            }
        },
    ]},
).execute()
```

Notes:

* `AppendCellsRequest` with `tableId` also appends into a table and grows
  it, but it takes typed `CellData` and loses `valueInputOption`
  ("USER_ENTERED") parsing of dates/numbers, so the two-step approach
  above was used instead.
* If the sheet has more than one table, the code picks the first. Set
  `"table_name"` in `config.json` to target a specific one by name.
* The write range is derived from the table's real `startColumnIndex`,
  not a hard-coded `A:G`.

---

# Final Authentication Files

The application currently uses:

```text
credentials.json
token.json
```

The application no longer uses:

```text
service-account.json
```

because OAuth authentication proved to be a better solution for uploading files into a personal Google Drive account.
