# YouTube Comment Assistant

First milestone for a local YouTube Comment Assistant.

This project authenticates with the YouTube Data API v3, fetches all comment threads related to your authenticated channel, and stores top-level comments in a local SQLite database.

It does not post replies, generate AI drafts, filter comments, or provide a Flask UI yet.

## Project Structure

```text
youtube-comment-assistant/
├── sync_comments.py
├── youtube_api.py
├── database.py
├── requirements.txt
├── .env.example
├── README.md
└── comments.db
```

`comments.db` is created when you run the sync script.

## WSL Ubuntu Setup

From inside WSL:

```bash
cd youtube-comment-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Google Cloud OAuth Setup

1. Open the Google Cloud Console.
2. Create or select a project.
3. Enable **YouTube Data API v3** for the project.
4. Configure the OAuth consent screen.
5. Create an OAuth client ID:
   - Application type: **Desktop app**
   - Download the JSON file.
6. Save the downloaded JSON file in this folder as:

```text
client_secret.json
```

Or update `.env` if you use another file name:

```text
YOUTUBE_CLIENT_SECRETS_FILE=client_secret.json
YOUTUBE_TOKEN_FILE=token.json
DATABASE_PATH=comments.db
```

## Run the Sync

```bash
source .venv/bin/activate
python sync_comments.py
```

The first run opens an OAuth flow. In WSL, the script prints a local authorization URL. Open that URL in your Windows browser if it does not open automatically, approve access, and return to the terminal.

The script saves OAuth credentials to `token.json` so later runs can refresh automatically.

## What Gets Stored

The SQLite table is created automatically:

```sql
CREATE TABLE IF NOT EXISTS comments (
id INTEGER PRIMARY KEY AUTOINCREMENT,
youtube_comment_id TEXT UNIQUE NOT NULL,
youtube_thread_id TEXT,
video_id TEXT,
video_title TEXT,
author_name TEXT,
author_channel_id TEXT,
text TEXT,
like_count INTEGER,
published_at TEXT,
updated_at TEXT,
fetched_at TEXT,
status TEXT DEFAULT 'synced'
);
```

Duplicates are avoided with `youtube_comment_id`. When a comment already exists, the sync updates fields such as text, like count, YouTube updated timestamp, fetched timestamp, and related metadata.

## Notes

- This milestone stores top-level comments only.
- Replies are not stored yet.
- No AI functionality is included yet.
- No replies are posted to YouTube.
- The code is structured so later milestones can add AI draft replies, review status, and one-click approval without changing the basic sync foundation.
