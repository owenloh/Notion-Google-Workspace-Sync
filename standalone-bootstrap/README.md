# Standalone Google bootstrap

Run the Google OAuth + Drive setup **without cloning the whole repo**. You only
need this folder's `bootstrap_standalone.py`, the three pip deps, and your OAuth
client JSON.

## 0. Prerequisites (Google Cloud, once)

- Enable APIs: **Drive, Docs, Sheets, Tasks**.
- OAuth **consent screen**: User type *External*; add your Gmail as a *Test user*.
- Create an **OAuth Client ID → "Desktop app"** and download its JSON
  (e.g. `oauth_client.json`). That file is what you pass to `--credentials`.

## 1. Install deps

```bash
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

## 2. Mint the refresh token (opens a browser — desktop only)

```bash
python bootstrap_standalone.py auth --credentials oauth_client.json
```

It opens a browser for consent and prints:

```
GOOGLE_OAUTH_REFRESH_TOKEN=1//0g....
```

## 3. Create the Drive mirror folder + index sheet

```bash
python bootstrap_standalone.py init \
  --credentials oauth_client.json \
  --refresh-token "1//0g....the token from step 2...."
```

It prints (find-or-create, so re-running is safe):

```
GOOGLE_DRIVE_MIRROR_FOLDER_ID=...
GOOGLE_INDEX_SHEET_ID=...
```

## 4. Put the three printed values into Railway

`GOOGLE_OAUTH_REFRESH_TOKEN`, `GOOGLE_DRIVE_MIRROR_FOLDER_ID`,
`GOOGLE_INDEX_SHEET_ID` — alongside `GOOGLE_CREDENTIALS_JSON` (the same client
JSON, as a single-line string), the Notion + relay keys, and `ADMIN_API_KEY`.
The first reflection (the equivalent of `bootstrap mirror`) then runs on the
server via `POST /admin/full-sync?key=$ADMIN_API_KEY`.

> Notes
> - You can pass credentials via env instead of flags: `GOOGLE_CREDENTIALS_JSON`
>   (the JSON string) and `GOOGLE_OAUTH_REFRESH_TOKEN`.
> - The scopes requested here **must** match the deployed app:
>   `drive`, `documents`, `spreadsheets`, `tasks`.
