# Wingman YouTube Comment Fetcher

This MVP discovers every video in the authenticated channel's uploads playlist,
stores the video catalog in SQLite, and syncs every top-level comment for
enabled videos. Uploads and comments are fully paginated. Each sync preserves
first-seen timestamps, refreshes last-seen timestamps, and reports inserted,
updated, and unchanged counts. Synced comments enter a reusable local Wingman
inbox with workflow fields for status, priority, category, research, and reply
drafts. Replies are not posted to YouTube.

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

5. Open `.env` and add the OAuth file paths and SQLite database path:

   ```dotenv
   YOUTUBE_CLIENT_SECRETS_FILE=client_secret.json
   YOUTUBE_TOKEN_FILE=token.json
   DATABASE_PATH=comments.db
   ```

   Do not commit `.env`.

6. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

7. Discover uploads and sync comments for all enabled videos:

   ```bash
   python fetch_comments.py
   ```

   On the first run, approve the read-only YouTube permission in the browser.
   The resulting OAuth token is saved to `token.json` for later runs.

The default command discovers the channel's uploads before syncing. Newly
discovered videos are enabled by default. Later discovery runs update metadata
without changing a video's enabled or disabled status.

## Commands

```bash
# Print active Wingman inbox comments
./wingman inbox

# Discover and store uploads without syncing comments
python fetch_comments.py --discover-only

# Sync comments for all enabled videos already stored
python fetch_comments.py --sync-enabled

# Sync comments for one video, regardless of stored status
python fetch_comments.py --video-id VIDEO_ID

# Enable or disable a stored video
python fetch_comments.py --enable-video VIDEO_ID
python fetch_comments.py --disable-video VIDEO_ID

# List stored videos and their status
python fetch_comments.py --list-videos
```

The script reports a result for each video and an overall summary. A video with
disabled comments or no comments does not prevent later videos from syncing.
Other API failures are reported with the affected video and page.
`comments.db` is created automatically and is ignored by Git.

Existing databases are migrated automatically when the inbox is opened or
comments are synced. Existing comments default to `new`; ignored and replied
comments are omitted from the active inbox.

## Tests

The uploads pagination, comment pagination, enabled-state, inbox repository,
database migration, and SQLite persistence tests do not contact YouTube:

```bash
python -m unittest -v
```
