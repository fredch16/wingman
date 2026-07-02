import os

from dotenv import load_dotenv
from flask import Flask, redirect, render_template_string, request, url_for

from database import (
    connect,
    count_pending_top_level_comments,
    get_comment_by_id,
    get_last_action,
    get_next_review_comment,
    get_queue_stats,
    initialize_database,
    mark_comment_replied,
    restore_action,
    update_comment_notes,
    update_comment_status,
)
from youtube_api import (
    YouTubeApiError,
    authenticate,
    delete_comment,
    post_reply_to_comment,
)


load_dotenv()


def env_value(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


DATABASE_PATH = env_value("DATABASE_PATH", "comments.db")
CLIENT_SECRETS_FILE = env_value("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
TOKEN_FILE = env_value("YOUTUBE_TOKEN_FILE", "token.json")
SKIP_MINUTES = int(env_value("WINGMAN_SKIP_MINUTES", "60"))

VALID_FILTERS = {"pending", "skipped", "ignored", "needs_research", "replied", "all"}

app = Flask(__name__)


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Wingman Review</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f8;
      --panel: #ffffff;
      --text: #1f2428;
      --muted: #656d76;
      --line: #d8dee4;
      --accent: #1967d2;
      --accent-dark: #0f4fa8;
      --danger: #b42318;
      --warning: #8a5a00;
      --success: #1f7a4d;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Arial, Helvetica, sans-serif;
      line-height: 1.45;
    }

    .shell {
      width: min(1120px, calc(100vw - 32px));
      margin: 28px auto;
    }

    .topbar {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }

    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.1;
    }

    .subtle {
      color: var(--muted);
      font-size: 14px;
    }

    .stats,
    .toolbar,
    .notice,
    .error,
    .empty,
    .comment,
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 1px;
      overflow: hidden;
      margin-bottom: 14px;
      background: var(--line);
    }

    .stat {
      background: var(--panel);
      padding: 12px;
      min-width: 0;
    }

    .stat strong {
      display: block;
      font-size: 22px;
      line-height: 1;
    }

    .stat span {
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
    }

    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px;
      margin-bottom: 14px;
    }

    .tabs,
    .search {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }

    .tab,
    button,
    .button {
      border: 1px solid var(--line);
      border-radius: 8px;
      min-height: 40px;
      padding: 9px 12px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--text);
      background: #fff;
    }

    .tab.active {
      border-color: var(--accent);
      color: var(--accent);
      background: #eef4ff;
    }

    input[type="search"] {
      width: min(260px, 100%);
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      font: inherit;
      background: #fff;
      color: var(--text);
    }

    .primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }

    .primary:hover {
      background: var(--accent-dark);
    }

    .danger {
      color: var(--danger);
      border-color: #f1aeb5;
      background: #fff7f7;
    }

    .warning {
      color: var(--warning);
      border-color: #f3d19c;
      background: #fffaf0;
    }

    .notice,
    .error,
    .empty {
      padding: 14px 16px;
      margin-bottom: 14px;
    }

    .notice {
      border-color: #9ec5fe;
      color: #0f4fa8;
    }

    .error {
      border-color: #f1aeb5;
      color: var(--danger);
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 340px;
      gap: 14px;
      align-items: start;
    }

    .comment {
      padding: 20px;
    }

    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 14px;
    }

    .video {
      color: var(--text);
      font-weight: 700;
    }

    .status {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .02em;
    }

    .author {
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 8px;
    }

    .text {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 18px;
    }

    .link-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }

    .panel {
      padding: 16px;
      margin-bottom: 14px;
    }

    label {
      display: block;
      font-weight: 700;
      margin-bottom: 8px;
    }

    textarea {
      width: 100%;
      min-height: 150px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      font: inherit;
      background: #fff;
      color: var(--text);
    }

    textarea.notes {
      min-height: 96px;
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 10px;
      margin-top: 12px;
    }

    .shortcuts {
      color: var(--muted);
      font-size: 13px;
      margin-top: 10px;
    }

    @media (max-width: 860px) {
      .stats {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .toolbar,
      .topbar {
        display: block;
      }

      .search {
        margin-top: 10px;
      }

      .layout {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 560px) {
      .shell {
        width: min(100vw - 20px, 1120px);
        margin: 18px auto;
      }

      .tab,
      button,
      .button,
      input[type="search"] {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <main class="shell">
    <div class="topbar">
      <div>
        <h1>Wingman Review</h1>
        <div class="subtle">{{ pending_count }} comments in the normal queue</div>
      </div>
      <form method="post" action="{{ url_for('undo_last_action') }}">
        <button type="submit">Undo Last Action</button>
      </form>
    </div>

    <section class="stats">
      <div class="stat"><strong>{{ stats.pending }}</strong><span>Pending</span></div>
      <div class="stat"><strong>{{ stats.skipped }}</strong><span>Skipped</span></div>
      <div class="stat"><strong>{{ stats.needs_research }}</strong><span>Needs research</span></div>
      <div class="stat"><strong>{{ stats.ignored }}</strong><span>Ignored</span></div>
      <div class="stat"><strong>{{ stats.replied }}</strong><span>Replied</span></div>
      <div class="stat"><strong>{{ stats.replied_today }}</strong><span>Replied today</span></div>
    </section>

    <section class="toolbar">
      <nav class="tabs">
        {% for key, label in filters %}
          <a class="tab {% if status_filter == key %}active{% endif %}" href="{{ url_for('index', status=key, q=search) }}">{{ label }}</a>
        {% endfor %}
      </nav>
      <form class="search" method="get" action="{{ url_for('index') }}">
        <input type="hidden" name="status" value="{{ status_filter }}">
        <input type="search" name="q" value="{{ search }}" placeholder="Search comments">
        <button type="submit">Search</button>
      </form>
    </section>

    {% if message %}
      <div class="notice">{{ message }}</div>
    {% endif %}

    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}

    {% if comment %}
      <div class="layout">
        <section class="comment">
          <div class="meta">
            {% if comment["video_title"] %}
              <span class="video">{{ comment["video_title"] }}</span>
            {% endif %}
            {% if comment["published_at"] %}
              <span>{{ comment["published_at"] }}</span>
            {% endif %}
            <span>{{ comment["like_count"] or 0 }} likes</span>
            <span class="status">{{ display_status }}</span>
          </div>
          <div class="author">{{ comment["author_name"] or "Unknown author" }}</div>
          <div class="text">{{ comment["text"] or "" }}</div>
          <div class="link-row">
            {% if video_url %}
              <a class="button" href="{{ video_url }}" target="_blank" rel="noreferrer">Open Video</a>
            {% endif %}
            {% if comment_url %}
              <a class="button" href="{{ comment_url }}" target="_blank" rel="noreferrer">Open Comment</a>
            {% endif %}
          </div>
        </section>

        <aside>
          <form class="panel" method="post" action="{{ url_for('submit_reply') }}">
            <input type="hidden" name="comment_id" value="{{ comment['id'] }}">
            <input type="hidden" name="status" value="{{ status_filter }}">
            <input type="hidden" name="q" value="{{ search }}">
            <label for="reply_text">Reply</label>
            <textarea id="reply_text" name="reply_text" required autofocus></textarea>
            <div class="actions">
              <button class="primary" type="submit" id="reply_button">Confirm & Reply</button>
            </div>
          </form>

          <form class="panel" method="post" action="{{ url_for('comment_action') }}">
            <input type="hidden" name="comment_id" value="{{ comment['id'] }}">
            <input type="hidden" name="status" value="{{ status_filter }}">
            <input type="hidden" name="q" value="{{ search }}">
            <div class="actions">
              <button type="submit" name="action" value="skip" id="skip_button">Skip For Now</button>
              <button class="warning" type="submit" name="action" value="needs_research" id="research_button">Needs Research</button>
              <button class="danger" type="submit" name="action" value="ignore" id="ignore_button">Ignore</button>
            </div>
            <div class="shortcuts">Shortcuts: Ctrl+Enter reply, S skip, I ignore, R research.</div>
          </form>

          <form class="panel" method="post" action="{{ url_for('save_notes') }}">
            <input type="hidden" name="comment_id" value="{{ comment['id'] }}">
            <input type="hidden" name="status" value="{{ status_filter }}">
            <input type="hidden" name="q" value="{{ search }}">
            <label for="notes">Notes</label>
            <textarea class="notes" id="notes" name="notes">{{ comment["notes"] or "" }}</textarea>
            <div class="actions">
              <button type="submit">Save Notes</button>
            </div>
          </form>
        </aside>
      </div>
    {% else %}
      <div class="empty">No comments found for this queue.</div>
    {% endif %}
  </main>

  <script>
    document.addEventListener("keydown", function (event) {
      const active = document.activeElement;
      const isTyping = active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT");

      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        const button = document.getElementById("reply_button");
        if (button) button.click();
        return;
      }

      if (isTyping) return;

      const key = event.key.toLowerCase();
      const targets = {
        "s": "skip_button",
        "i": "ignore_button",
        "r": "research_button"
      };
      if (targets[key]) {
        event.preventDefault();
        const button = document.getElementById(targets[key]);
        if (button) button.click();
      }
    });
  </script>
</body>
</html>
"""


def current_filter() -> str:
    status = request.values.get("status", "pending").strip()
    return status if status in VALID_FILTERS else "pending"


def current_search() -> str:
    return request.values.get("q", "").strip()


def redirect_to_queue(message: str):
    return redirect(
        url_for(
            "index",
            status=current_filter(),
            q=current_search(),
            message=message,
        )
    )


def youtube_video_url(comment) -> str | None:
    if not comment["video_id"]:
        return None
    return f"https://www.youtube.com/watch?v={comment['video_id']}"


def youtube_comment_url(comment) -> str | None:
    if not comment["video_id"] or not comment["youtube_comment_id"]:
        return None
    return (
        f"https://www.youtube.com/watch?v={comment['video_id']}"
        f"&lc={comment['youtube_comment_id']}"
    )


def load_page_data(message: str | None = None, error: str | None = None):
    status_filter = current_filter()
    search = current_search()

    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    comment = get_next_review_comment(connection, status_filter, search)
    pending_count = count_pending_top_level_comments(connection)
    stats = get_queue_stats(connection)
    connection.close()

    display_status = None
    video_url = None
    comment_url = None
    if comment:
        display_status = "pending" if comment["status"] == "synced" else comment["status"]
        display_status = display_status.replace("_", " ")
        video_url = youtube_video_url(comment)
        comment_url = youtube_comment_url(comment)

    return render_template_string(
        PAGE_TEMPLATE,
        comment=comment,
        pending_count=pending_count,
        stats=stats,
        status_filter=status_filter,
        search=search,
        filters=[
            ("pending", "Pending"),
            ("skipped", "Skipped"),
            ("needs_research", "Needs Research"),
            ("ignored", "Ignored"),
            ("replied", "Replied"),
            ("all", "All Active"),
        ],
        display_status=display_status,
        video_url=video_url,
        comment_url=comment_url,
        message=message,
        error=error,
    )


@app.get("/")
def index():
    return load_page_data(message=request.args.get("message"))


@app.post("/reply")
def submit_reply():
    comment_id = request.form.get("comment_id", "").strip()
    reply_text = request.form.get("reply_text", "").strip()

    if not comment_id.isdigit():
        return load_page_data(error="Missing or invalid comment ID."), 400

    if not reply_text:
        return load_page_data(error="Write a reply before submitting."), 400

    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    comment = get_comment_by_id(connection, int(comment_id))

    if not comment:
        connection.close()
        return load_page_data(error="That comment is no longer in the database."), 404

    if comment["status"] == "replied":
        connection.close()
        return redirect_to_queue("That comment was already replied to.")

    if comment["is_reply"]:
        connection.close()
        return load_page_data(error="Wingman only replies to top-level comments."), 400

    try:
        youtube = authenticate(CLIENT_SECRETS_FILE, TOKEN_FILE)
        youtube_reply_id = post_reply_to_comment(
            youtube,
            comment["youtube_comment_id"],
            reply_text,
        )
        mark_comment_replied(connection, int(comment_id), reply_text, youtube_reply_id)
    except (FileNotFoundError, YouTubeApiError) as exc:
        connection.close()
        return load_page_data(error=str(exc)), 500

    connection.close()
    return redirect_to_queue("Reply posted to YouTube.")


@app.post("/action")
def comment_action():
    comment_id = request.form.get("comment_id", "").strip()
    action = request.form.get("action", "").strip()

    if not comment_id.isdigit():
        return load_page_data(error="Missing or invalid comment ID."), 400

    actions = {
        "skip": ("skipped", "skip", "Skipped for now."),
        "ignore": ("ignored", "ignore", "Ignored. It will not appear in the normal queue."),
        "needs_research": (
            "needs_research",
            "needs_research",
            "Marked as needs research.",
        ),
    }
    if action not in actions:
        return load_page_data(error="Unknown action."), 400

    status, action_name, message = actions[action]
    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    update_comment_status(
        connection,
        int(comment_id),
        status,
        action_name,
        skip_minutes=SKIP_MINUTES,
    )
    connection.close()
    return redirect_to_queue(message)


@app.post("/notes")
def save_notes():
    comment_id = request.form.get("comment_id", "").strip()
    notes = request.form.get("notes", "").strip()

    if not comment_id.isdigit():
        return load_page_data(error="Missing or invalid comment ID."), 400

    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    update_comment_notes(connection, int(comment_id), notes)
    connection.close()
    return redirect_to_queue("Notes saved.")


@app.post("/undo")
def undo_last_action():
    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    action = get_last_action(connection)

    if not action:
        connection.close()
        return redirect_to_queue("Nothing to undo.")

    try:
        if action["action"] == "reply" and action["youtube_reply_id"]:
            youtube = authenticate(CLIENT_SECRETS_FILE, TOKEN_FILE)
            delete_comment(youtube, action["youtube_reply_id"])
        restore_action(connection, action)
    except (FileNotFoundError, YouTubeApiError) as exc:
        connection.close()
        return load_page_data(error=str(exc)), 500

    connection.close()
    return redirect_to_queue("Last action undone.")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
