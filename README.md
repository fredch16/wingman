# Wingman

Wingman is an AI-assisted creator comment inbox for prioritizing conversations,
drafting replies in a creator's voice, learning from edits, and publishing
responses without leaving the workflow.

The project started as a hackathon MVP at the Cursor Social Media Hackathon in
Stuttgart in July 2026, where it won second place. Since then, Wingman has
evolved into a tool I use daily to manage real creator conversations: it syncs
comments, ranks what deserves attention, prepares contextual replies, learns
reusable preferences, and posts approved text directly to YouTube or Instagram.

## What Wingman does

- Discovers every upload on an authenticated YouTube channel.
- Discovers Instagram Reels and posts through the Instagram Graph API.
- Synchronizes top-level comments with full pagination and incremental refreshes.
- Detects existing creator replies and keeps completed threads out of the inbox.
- Classifies comments by priority, category, reply-worthiness, and research need.
- Generates editable replies from creator, video, comment, and thread context.
- Publishes replies to the originating platform and removes completed
  conversations immediately.
- Learns durable voice preferences from changes to AI drafts.
- Learns video-specific model answers for recurring questions.
- Prefills review-ready replies with video-specific keyword automations.
- Preserves ignored conversations in a separate view.
- Supports a fast keyboard-driven review workflow.

Wingman stores its working state in SQLite. Existing databases are migrated
automatically as fields are added.

## Daily workflow

The main screen is a two-pane inbox. Conversations are ranked on the left; the
selected comment, context, and editable reply are shown on the right.

1. Synchronize the latest comments.
2. Optionally classify new conversations individually or with **Classify**.
3. Use **Generate** to create every missing draft, or select **Only rated above
   0.2** first to limit generation to higher-priority classified comments.
4. Review or edit the selected reply.
5. Optionally teach Wingman a creator-wide preference or video-specific answer.
6. Post the reply. The completed conversation fades out and the next one opens.
7. Hide conversations that do not need a response.

The sidebar **Refetch** controls run synchronization without restarting the
application: **All** refreshes YouTube then Instagram, while **YT** and **IG**
refresh only that platform. Refetch, bulk classification, and bulk generation
run in the background and display live item counts and progress in the sidebar.
Only one long-running workflow runs at a time to avoid competing database writes.

AI is optional for replying. Every conversation opens with an empty response
editor, so you can type a manual reply and press `Ctrl+Enter` to post it
directly to YouTube or Instagram without classifying, generating, or approving
it first. **Generate Draft** remains available beside the direct post action.

### Keyboard controls

| Key | Action |
| --- | --- |
| `j` | Select the next comment |
| `k` | Select the previous comment |
| `r` | Generate or regenerate the selected reply |
| `e` or `a` | Focus the reply editor |
| `Ctrl+Enter` | Post the selected reply |
| `h` | Hide the selected conversation |
| `o` or `O` | Open the source comment in a new tab |
| `Esc` | Leave the reply editor |

Shortcuts are inactive while typing, apart from `Ctrl+Enter` and `Esc`.

## Keyword automations

Open **Automations** in the sidebar to create a review-first response rule:

1. Select one stored YouTube video or Instagram post. Available thumbnails are
   shown beside each item.
2. Enter comma-separated, case-sensitive keyword variations such as
   `PCB, pcb, Pcb`.
3. Enter the response Wingman should prefill.

When a comment on that specific video contains any configured string, Wingman
places the default response in the normal reply editor and identifies the
matched keyword. Matching is a simple case-sensitive substring comparison. The
response remains fully editable and is never posted automatically; review it,
then use `Ctrl+Enter` or the platform post button. Automations can be edited,
paused, enabled, or deleted from the same page.

## Setup

Wingman requires Python 3.11 or newer, an OpenAI API key, and credentials for at
least one supported platform.

1. Create a project in the
   [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen.
4. Create an OAuth client with application type **Desktop app**.
5. Save the downloaded credentials as `client_secret.json` in the repository
   root.
6. Create the local environment file:

   ```bash
   cp .env.example .env
   ```

7. Configure the required values:

   ```dotenv
   OPENAI_API_KEY=...
   YOUTUBE_CLIENT_SECRETS_FILE=client_secret.json
   YOUTUBE_TOKEN_FILE=token.json
   DATABASE_PATH=comments.db
   ```

   To enable Instagram, add a professional-account Graph API token:

   ```dotenv
   INSTAGRAM_ACCESS_TOKEN=...
   INSTAGRAM_ACCOUNT_ID=...
   INSTAGRAM_GRAPH_API_VERSION=v25.0
   ```

8. Create an environment and install Wingman:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -e .
   ```

The first authenticated YouTube operation opens the Google consent flow.
Wingman requests `youtube.force-ssl` because publishing replies requires it.
The resulting token is stored locally in `token.json`.

## Run the application

Start the web inbox:

```bash
wingman-web
```

Then open <http://127.0.0.1:5000>.

The compatibility launcher remains available:

```bash
python app.py
```

Print the active inbox in the terminal:

```bash
wingman inbox
```

The repository-local `./wingman inbox` launcher also works before installation.

## Quick synchronization

Run synchronization commands from the repository root with `.venv` activated:

```bash
cd /home/fredch/Projects/cursor-hackathon-wingman
source .venv/bin/activate
```

To synchronize YouTube only:

```bash
wingman-sync
```

To synchronize Instagram only:

```bash
wingman-instagram
```

For the normal daily workflow—synchronize both platforms and then launch the
inbox on port 5000—run:

```bash
wingman daily
```

This runs a complete YouTube sync followed by a complete Instagram sync. The app
starts at <http://127.0.0.1:5000> only after both succeed and stays attached to
the terminal; stop it with `Ctrl+C`. Add `--refresh` to bypass both platforms'
freshness caches:

```bash
wingman daily --refresh
```

The equivalent standalone entry point is `wingman-daily` after reinstalling the
package. To synchronize both platforms without launching the app, run:

```bash
wingman-sync
wingman-instagram
```

Both platforms write into the same SQLite inbox configured by `DATABASE_PATH`.

## Synchronize YouTube

The default command performs the complete YouTube workflow: it discovers the
authenticated channel's uploads, updates the local video catalog, synchronizes
comments for every enabled video, and checks those threads for existing creator
replies.

```bash
wingman-sync
```

Use `--sync-enabled` when the catalog is already current and you only want to
refresh comments. By default, videos checked within the last hour are skipped;
add `--refresh` to force API checks.

Additional YouTube commands:

```bash
# Discover uploads without syncing comments
wingman-sync --discover-only

# Sync comments for every enabled stored video, without discovering uploads
wingman-sync --sync-enabled

# Force comment refreshes for every enabled stored video
wingman-sync --sync-enabled --refresh

# Sync one video regardless of its stored enabled state
wingman-sync --video-id VIDEO_ID

# Manage the stored video catalog
wingman-sync --list-videos
wingman-sync --enable-video VIDEO_ID
wingman-sync --disable-video VIDEO_ID

# Check stored threads for creator replies
wingman-sync --backfill-replies

# Force a fresh creator-reply check
wingman-sync --backfill-replies --refresh
```

`python fetch_comments.py` remains as a compatibility entrypoint and accepts the
same arguments.

Synchronization is resilient across videos: disabled comments, empty results,
or a failure on one video do not stop later videos. Successful comment and reply
checks are committed incrementally so interrupted runs can resume.

## Synchronize Instagram

The default Instagram command discovers all available media, synchronizes their
top-level conversations, reconstructs reply threads, and detects existing
creator replies:

```bash
wingman-instagram
```

Instagram skips comment requests for media checked within the last 60 minutes.
The media catalog is still refreshed, but cached comments are reused. Force all
comment requests with:

```bash
wingman-instagram --refresh
```

Change the cache duration for one run with
`--freshness-minutes MINUTES`, or set the daily default in `.env`:

```dotenv
INSTAGRAM_SYNC_FRESHNESS_MINUTES=60
```

To verify the authenticated account and list its media without changing the
local database:

```bash
wingman-instagram --list-media
```

To limit a run to one or more posts:

```bash
wingman-instagram --media-id MEDIA_ID
```

Instagram's flat comment response is reconstructed into conversations using
parent IDs. Existing replies from `@charbonnierlabs` are detected during sync,
so answered threads stay out of the active inbox.

## Move inbox state between devices

For the temporary Git-based multi-device workflow, Wingman can save a consistent
compressed database snapshot:

```bash
wingman state save
wingman state status
```

This writes two Git-trackable files:

```text
state/wingman-state.db.gz
state/wingman-state.db.gz.meta
```

The save operation uses SQLite's backup mechanism, compacts the result, and
records a SHA-256 checksum. Manually entered video summaries and learned
`## Response Guidance` are included with the database state. Wingman records a
separate count and checksum for those contexts and verifies them during status
checks and restoration, so a restore cannot silently lose or alter them. It
does not copy `.env`, API keys, OAuth tokens, or `creator.md`.

On the device with the newest state:

```bash
wingman state save
git add state/wingman-state.db.gz state/wingman-state.db.gz.meta
git commit -m "chore: save Wingman state" \
  -m "- Update the shared compressed inbox snapshot"
git push
```

On the other device, stop Wingman before restoring, then run:

```bash
git pull --ff-only
wingman state status
wingman state restore
```

Before restoration, Wingman saves the receiving device's current database under
`.wingman-backups/`. If the snapshot checksum or SQLite integrity check fails,
the current database is left untouched.

This is a single-writer workflow: always publish from one device and pull before
working on another. Git cannot merge two database snapshots. The snapshot is
compressed but **not encrypted** and contains viewer usernames, comments,
drafts, classifications, ignored state, and learned video context. Only push it
to a private repository whose collaborators may access that information.

## Classification and reply generation

Classification is explicit. Opening the inbox never calls OpenAI. Use the inbox
menu to classify or reclassify comments and to generate or regenerate drafts in
bulk. Bulk generation only processes classified comments with priority strictly
above `0.2`; comments rated exactly `0.2`, lower-rated comments, and unclassified
comments can still be generated individually. Developer mode reveals numerical
scores, reasons, model metadata, and the classification playground.

Reply generation combines:

1. The shared reply-generation instructions.
2. The editable creator profile in `creator.md`.
3. The selected video's stored context and learned response guidance.
4. The viewer comment and available thread context.

The generated text stays editable until it is posted. A successful post is
recorded locally only after the source platform confirms it.

## Learning

### Creator-wide preferences

**Learn from Change** compares the untouched generated draft with the edited
reply. Wingman proposes up to two durable voice rules, such as preferred length,
acknowledgement style, tone, technical depth, or uncertainty handling.

Nothing is learned automatically. Proposed rules can be edited, accepted, or
rejected. Accepted, non-duplicate rules are appended to `creator.md` under
`## Learned Preferences`.

### Video-specific response guidance

**Learn for This Video** compares the viewer's comment with the current reply
and infers:

- the kind of future question for which the answer is relevant; and
- a reusable model answer grounded in the creator's response.

After review, the guidance is appended to that video's context under
`## Response Guidance`. Future drafts for the same video receive it
automatically; unrelated videos do not.

Video summaries and learned guidance can also be reviewed from **Video context**
in the sidebar.

## Project structure

```text
.
├── src/wingman/
│   ├── ai/             # prompts, classification, generation, and learning
│   ├── db/             # SQLite schema, repositories, and video catalog
│   ├── instagram/      # Graph API client, synchronization, and publishing
│   ├── youtube/        # synchronization, reply detection, and publishing
│   ├── static/         # browser JavaScript and styles
│   ├── templates/      # Flask/Jinja views
│   ├── cli.py          # terminal inbox command
│   ├── daily.py        # all-platform synchronization and app launch
│   ├── playground.py   # fixed classification regression scenarios
│   └── web.py          # Flask application factory and routes
├── tests/              # isolated unit and route tests
├── app.py              # compatibility web launcher
├── fetch_comments.py   # compatibility synchronization launcher
├── creator.md          # editable creator voice profile
├── pyproject.toml      # package metadata and console commands
└── requirements.txt    # dependency-only compatibility install
```

The `src` layout prevents accidental imports from the repository root and keeps
runtime code separate from tests and local data.

## Tests

The suite covers pagination, synchronization, migrations, repository behavior,
classification, reply generation, preference learning, video-specific learning,
creator-reply detection, YouTube and Instagram publishing, routes, and keyboard
workflow regressions. Tests use fakes and temporary SQLite databases; they do
not contact Instagram, YouTube, or OpenAI.

```bash
python -m unittest -v
```

## Local files and secrets

These files are intentionally ignored and must not be committed:

- `.env`
- `client_secret.json`
- `token.json`
- `comments.db`
- virtual environments and Python caches

Set `CREATOR_CONTEXT_FILE` only when the creator profile should live somewhere
other than the root `creator.md`. Set `OPENAI_MODEL` to override the configured
model, and use `WINGMAN_CLASSIFICATION_DRY_RUN=true` for temporary,
process-local classification experiments.
