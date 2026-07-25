# Wingman YouTube Comment Fetcher

This MVP follows YouTube pagination to fetch every available top-level comment
from one configured public YouTube video, upserts them into SQLite, and prints
them in the terminal. Each sync preserves when a comment was first seen,
refreshes when it was last seen, and reports inserted, updated, and unchanged
counts. Replies are not fetched yet; the script only prints each thread's total
reply count.

## Setup and run

1. Create or select a project in the [Google Cloud Console](https://console.cloud.google.com/).
2. In **APIs & Services > Library**, enable **YouTube Data API v3**.
3. Configure the OAuth consent screen, then create an **OAuth client ID** with
   application type **Desktop app**. Download its JSON file as
   `client_secret.json` in this directory.
4. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

5. Open `.env` and add the OAuth file paths and one public YouTube video ID:

   ```dotenv
   YOUTUBE_CLIENT_SECRETS_FILE=client_secret.json
   YOUTUBE_TOKEN_FILE=token.json
   YOUTUBE_VIDEO_ID=dQw4w9WgXcQ
   DATABASE_PATH=comments.db
   ```

   Use only the video ID, not the full YouTube URL. Do not commit `.env`.

6. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

7. Run the script:

   ```bash
   python fetch_comments.py
   ```

   On the first run, approve the read-only YouTube permission in the browser.
   The resulting OAuth token is saved to `token.json` for later runs.

The script reports missing configuration, invalid or missing videos, disabled
comments, quota/rate-limit failures, authorization errors, other API errors,
network failures, and videos for which no comments are returned. `comments.db`
is created automatically and is ignored by Git.

## Tests

The pagination and SQLite persistence tests do not contact YouTube:

```bash
python -m unittest -v
```
