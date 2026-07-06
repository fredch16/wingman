# Wingman Comment Assistant

Local comment review assistant for YouTube comments and Instagram Reel comments.

This project authenticates with the YouTube Data API v3, fetches comment threads and replies related to your authenticated channel, and stores comments in a local SQLite database. It can also sync comments from Instagram Reels through the Instagram Graph API.

It can generate editable AI drafts, but it does not post AI output automatically. The Flask UI only posts replies after your manual approval, and Instagram posting is not enabled yet.

## Project Structure

```text
youtube-comment-assistant/
├── sync_comments.py
├── sync_instagram_comments.py
├── youtube_api.py
├── instagram_api.py
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

To sync only one video, pass its YouTube video ID:

```bash
python sync_comments.py --video-id VIDEO_ID_HERE
```

You can also put a default video filter in `.env`:

```text
YOUTUBE_VIDEO_ID=VIDEO_ID_HERE
```

The first run opens an OAuth flow. In WSL, the script prints a local authorization URL. Open that URL in your Windows browser if it does not open automatically, approve access, and return to the terminal.

The script saves OAuth credentials to `token.json` so later runs can refresh automatically.

## Instagram Setup

Put your Instagram User access token in `.env`. This sync uses the Instagram API with
Instagram Login on `graph.instagram.com`, not Facebook Login on `graph.facebook.com`.

```text
INSTAGRAM_ACCESS_TOKEN=your_meta_graph_api_token_here
```

Optionally add your numeric Instagram `user_id`:

```text
INSTAGRAM_USER_ID=17841400000000000
```

Do not use the Instagram username here, and do not use a Facebook Page ID.

If `INSTAGRAM_USER_ID` is blank, the sync script discovers it from `graph.instagram.com/me`.

If Meta deprecates the default Graph API version, override it:

```text
INSTAGRAM_GRAPH_API_VERSION=v25.0
```

Sync Instagram Reel comments:

```bash
source .venv/bin/activate
python sync_instagram_comments.py
```

To sync only one Reel/media ID:

```bash
python sync_instagram_comments.py --media-id INSTAGRAM_MEDIA_ID_HERE
```

To keep Instagram review focused on specific Reels, add comma-separated media IDs:

```text
INSTAGRAM_MEDIA_IDS=18418211902178416
INSTAGRAM_REVIEW_MEDIA_IDS=18418211902178416
```

## Run the Manual Review UI

After syncing comments into SQLite, start the local Flask app:

```bash
source .venv/bin/activate
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

The UI has separate **YouTube** and **Instagram** tabs. The YouTube tab can post manually approved replies. The Instagram tab currently supports sync, notes, manual video context, statuses, and AI drafts; Instagram reply posting is intentionally left for a later milestone.

YouTube is treated as the source of truth for whether your channel has already replied. During sync, if a top-level comment already has a reply from your authenticated channel, Wingman marks it as `externally_replied` so it leaves the normal pending queue. SQLite still owns local workflow states such as `ignored`, `skipped`, and `needs_reply`.

The review UI also supports:

- **Skip For Now**: marks the comment as `skipped` and returns it to the normal queue after the skip window.
- **Needs Reply**: moves the comment into a reply queue for later drafting.
- **Ignore**: marks the comment as `ignored` without posting a reply.
- **Move to Pending**: moves skipped, ignored, or needs reply comments back to the normal pending queue.
- **Undo Last Action**: reverts the most recent local action. If the action posted a YouTube reply, Wingman tries to delete that reply from YouTube too.
- **Previous / Next**: browse through the current queue without changing comment state.
- **Generate Batch Drafts**: generates AI drafts for comments in the needs reply queue that do not already have drafts.
- Queue filters for pending, needs reply, skipped, external replies, ignored, replied, and all active comments.
- Search across comment text, author, video title, and notes.
- Notes for follow-up context.
- Replied comments show the reply text Wingman posted.
- Links to open the original YouTube video, comment, or Studio comments page.
- Comment like/heart actions are not automated because the official YouTube Data API comments methods do not provide supported endpoints for those actions.
- Keyboard shortcuts: A focuses the reply box, Esc leaves it, Enter or Cmd/Ctrl+Enter replies, D generates an AI draft, N marks needs reply, P moves back to pending, S skips, I ignores, O opens the YouTube comment, and arrow keys browse the queue. Shift+Enter adds a new line while writing a reply.

By default, skipped comments return to the pending queue after 60 minutes. Override this in `.env`:

```text
WINGMAN_SKIP_MINUTES=60
WINGMAN_PORT=5000
```

## OpenAI Draft Replies

Wingman can generate an AI draft and place it into the reply box for you to edit. It never posts AI output automatically. You still have to click **Confirm & Reply** to publish.

To enable OpenAI drafts:

1. Create or sign in to an OpenAI Platform account.
2. Add billing/payment details in the OpenAI Platform dashboard.
3. Create an API key.
4. Add the key to `.env`:

```text
AI_PROVIDER=openai
OPENAI_MODEL=gpt-5.5
OPENAI_API_KEY=your_api_key_here
CREATOR_CONTEXT_FILE=fred.md
```

Then restart the Flask app and click **Generate Draft** in the review UI.

`fred.md` contains the general creator voice and reply rules that are included with every AI prompt. Edit that file whenever you want to tune how drafts sound overall. Use the per-comment notes field for details that apply only to one comment. Use the Video Description field in the review UI for short manual context about the video; it is saved for every comment from that video and included in AI draft prompts.

Every AI generation is logged in SQLite in `ai_drafts` with:

- `comment_id`
- `model`
- `provider`
- `prompt_version`
- `prompt_text`
- `suggested_reply`
- `status`
- `created_at`

The current prompt version is `youtube_reply_v4`. It sends only the context needed for a useful reply:

- creator context from `fred.md`
- video title, YouTube title, or Instagram caption-derived title
- manual video description, only when you have saved one
- commenter display name
- comment text
- your private notes for that comment, only when notes are present

The model is instructed to draft a concise YouTube creator reply, follow your creator context, avoid inventing facts, and return only editable reply text.

## What Gets Stored

The SQLite table is created automatically:

```sql
CREATE TABLE IF NOT EXISTS comments (
id INTEGER PRIMARY KEY AUTOINCREMENT,
youtube_comment_id TEXT UNIQUE NOT NULL,
youtube_thread_id TEXT,
parent_comment_id TEXT,
is_reply INTEGER DEFAULT 0,
video_id TEXT,
video_title TEXT,
video_description TEXT,
author_name TEXT,
author_channel_id TEXT,
text TEXT,
like_count INTEGER,
published_at TEXT,
    updated_at TEXT,
    fetched_at TEXT,
    wingman_reply_text TEXT,
    youtube_reply_id TEXT,
    replied_at TEXT,
    skipped_until TEXT,
    last_seen_at TEXT,
    notes TEXT,
    ai_draft_text TEXT,
    ai_draft_model TEXT,
    ai_draft_provider TEXT,
    ai_drafted_at TEXT,
    status TEXT DEFAULT 'synced'
);
```

AI draft attempts are logged separately:

```sql
CREATE TABLE IF NOT EXISTS ai_drafts (
id INTEGER PRIMARY KEY AUTOINCREMENT,
comment_id INTEGER NOT NULL,
model TEXT NOT NULL,
provider TEXT NOT NULL,
prompt_version TEXT NOT NULL,
prompt_text TEXT NOT NULL,
suggested_reply TEXT NOT NULL,
status TEXT DEFAULT 'generated',
created_at TEXT NOT NULL
);
```

Duplicates are avoided with `youtube_comment_id`. When a comment already exists, the sync updates fields such as text, like count, YouTube updated timestamp, fetched timestamp, reply status, and related metadata.

Instagram comments are stored separately in `instagram_comments` so the YouTube workflow stays stable while Instagram support is still growing. Duplicate Instagram comments are avoided with `instagram_comment_id`.

## Notes

- This milestone stores top-level comments and replies.
- The manual review UI can post replies you type yourself.
- AI drafts are editable suggestions only and are never posted automatically.
- The code is structured so later milestones can add AI draft replies, review status, and one-click approval without changing the basic sync foundation.
