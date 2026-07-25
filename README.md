# Wingman YouTube Comment Fetcher

This MVP discovers every video in the authenticated channel's uploads playlist,
stores the video catalog in SQLite, and syncs every top-level comment for
enabled videos. Uploads and comments are fully paginated. Each sync preserves
first-seen timestamps, refreshes last-seen timestamps, and reports inserted,
updated, and unchanged counts. Synced comments enter a reusable local Wingman
inbox with workflow fields for status, priority, category, research, and reply
drafts. Approved replies can be posted directly to YouTube from the local UI.

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

   On the first run, approve the YouTube account-management permission in the
   browser. It is required to publish replies.
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

# Backfill creator-reply status for unchecked stored comments
python fetch_comments.py --backfill-replies

# Force either sync mode to ignore its one-hour freshness check
python fetch_comments.py --sync-enabled --refresh
python fetch_comments.py --backfill-replies --refresh
```

The script reports a result for each video and an overall summary. A video with
disabled comments or no comments does not prevent later videos from syncing.
Other API failures are reported with the affected video and page.
`comments.db` is created automatically and is ignored by Git.

After comment sync, Wingman checks previously unchecked threads for replies from
the authenticated creator channel. It uses embedded thread replies when
complete and requests the full reply list only when necessary. Confirmed creator
replies are excluded from the active inbox, and repeat runs do not recheck
completed rows. The backfill summary reports checked, replied, and unreplied
totals.

Top-level comments authored by the authenticated creator channel are excluded
from the inbox. `@CharbonnierLabs` is also excluded as a fallback for older
comments without channel IDs.

Top-level comment fetches and creator-reply backlog checks have independent
last-checked timestamps and a one-hour freshness window. A recently synced
video skips comment fetching without preventing a due backlog check.
`--refresh` bypasses the relevant freshness check; forced backlog refreshes
recheck previously unreplied comments but never recheck confirmed creator
replies.

Backlog runs print the pending count, current comment, API stage, and detection
result as they progress. Each successful comment is committed immediately, so
an interrupted run resumes from the remaining unchecked comments.

Existing databases are migrated automatically when the inbox is opened or
comments are synced. Existing comments default to `new`; ignored and replied
comments are omitted from the active inbox.

## Minimal inbox UI

Start the local Flask UI:

```bash
python app.py
```

Then open `http://127.0.0.1:5000`. The UI ranks classified, unreplied comments
and keeps unclassified comments in a separate section with explicit
classification actions. Comment details still support local workflow changes
and include a direct **Open on YouTube** link. The UI does not generate replies
automatically.

Comment detail pages include a reply form that publishes directly beneath the
top-level YouTube comment. Posting is explicit and immediate. Wingman updates
the local inbox to replied only after YouTube confirms the new reply.

Existing installations previously used a read-only OAuth scope. The first
authenticated action after this update will open the Google consent flow once
to grant the `youtube.force-ssl` permission and refresh the ignored local token.

## Classification playground

Add `OPENAI_API_KEY` and, optionally, `OPENAI_MODEL` to `.env`, then start the
Flask UI and open `http://127.0.0.1:5000/classification-playground`.

The playground contains twelve fixed comments covering common creator-inbox
scenarios. Use **Classify** for one example or **Classify All** for the complete
set. **Classify All** runs both the previous and current prompts so their
structured results can be compared side by side. It logs each prompt, raw
response, and parsed JSON to the terminal. Playground results are held in
process memory only: they are never written to the comments database and reset
when the Flask process restarts.

Classifier `production-v2` adds explicit `specific_appreciation` and
`humorous_engagement` categories and includes video-title context in both
playground and production requests. Existing stored classifications retain their
original version until they are explicitly reclassified.

## Production classification inbox

The main inbox displays classified, unreplied comments in descending priority,
then newest-first order. Use **Classify This**, **Classify Top 10 Unclassified**,
or **Classify All Unclassified** to run classification explicitly; opening the
page never calls OpenAI. The production workflow reuses the same structured
classifier and prompt as the playground.

Each completed classification stores its category, priority, reply-worthiness,
research flag, reason, timestamp, model, and prompt version in SQLite. Reasoning
and model metadata live in a separate developer panel, while the primary card
stays focused on the comment and its rank. **Generate Reply** is intentionally
a disabled placeholder.

Classification is temporarily configured as a dry run by default. OpenAI calls
still run, but their production-inbox results stay in Flask process memory and
reset when the app restarts; SQLite remains unchanged. Set
`WINGMAN_CLASSIFICATION_DRY_RUN=false` when persistent classification is wanted.

## Tests

The uploads pagination, comment pagination, creator-reply detection,
enabled-state, inbox repository, database migration, and SQLite persistence
tests do not contact YouTube:

```bash
python -m unittest -v
```
