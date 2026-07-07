import os

from dotenv import load_dotenv
from flask import Flask, redirect, render_template_string, request, url_for

from ai_service import AIDraftError, generate_reply_draft, refine_reply_draft
from database import (
    connect,
    count_instagram_review_comments,
    count_pending_top_level_comments,
    count_pending_instagram_comments,
    count_review_comments,
    get_comment_by_id,
    get_comments_needing_drafts,
    get_instagram_comment_by_id,
    get_instagram_comments_needing_drafts,
    get_instagram_queue_stats,
    get_last_action,
    get_next_instagram_review_comment,
    get_next_review_comment,
    get_queue_stats,
    initialize_database,
    mark_comment_replied,
    mark_instagram_comment_replied,
    restore_action,
    save_ai_draft,
    save_instagram_ai_draft,
    update_comment_notes,
    update_comment_status,
    update_video_description,
    update_instagram_comment_notes,
    update_instagram_comment_status,
    update_instagram_video_description,
)
from instagram_api import (
    InstagramApiError,
    InstagramClient,
    post_reply_to_comment as post_instagram_reply_to_comment,
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


def csv_env_values(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


DATABASE_PATH = env_value("DATABASE_PATH", "comments.db")
CLIENT_SECRETS_FILE = env_value("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
TOKEN_FILE = env_value("YOUTUBE_TOKEN_FILE", "token.json")
SKIP_MINUTES = int(env_value("WINGMAN_SKIP_MINUTES", "60"))
WINGMAN_PORT = int(env_value("WINGMAN_PORT", "5000"))
INSTAGRAM_ACCESS_TOKEN = env_value("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_API_VERSION = env_value("INSTAGRAM_GRAPH_API_VERSION", "v25.0")

VALID_FILTERS = {
    "pending",
    "skipped",
    "ignored",
    "needs_reply",
    "externally_replied",
    "replied",
    "all",
}
VALID_PLATFORMS = {"youtube", "instagram"}

app = Flask(__name__)


DASHBOARD_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Wingman</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090a0c;
      --surface: #121418;
      --surface-2: #1a1d22;
      --text: #f4f5f6;
      --muted: #8f969f;
      --line: #262a31;
      --accent: #7dd3fc;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.4;
    }
    .shell { width: min(960px, calc(100vw - 28px)); margin: 22px auto; }
    .nav { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 18px; }
    .brand { font-size: 18px; font-weight: 700; letter-spacing: 0; }
    .nav a { color: var(--muted); text-decoration: none; font-weight: 650; }
    .nav a.active, .nav a:hover { color: var(--text); }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      margin-bottom: 12px;
    }
    h1 { margin: 0; font-size: clamp(30px, 6vw, 58px); line-height: .95; letter-spacing: 0; }
    .muted { color: var(--muted); }
    .launch, .ghost {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      padding: 0 14px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--surface);
      color: var(--text);
      text-decoration: none;
      font-weight: 700;
    }
    .launch { background: var(--text); color: var(--bg); border-color: var(--text); }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
    .metric {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 88px;
    }
    .metric strong { display: block; font-size: 30px; line-height: 1; margin-bottom: 8px; }
    .metric span { color: var(--muted); font-size: 14px; }
    .split { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .list {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
    }
    .list h2 { font-size: 14px; text-transform: uppercase; color: var(--muted); margin: 4px 6px 8px; letter-spacing: .06em; }
    .item { display: block; padding: 12px 6px; color: var(--text); border-top: 1px solid var(--line); }
    .item:first-of-type { border-top: 0; }
    .item strong { display: block; font-size: 14px; margin-bottom: 4px; }
    .item span { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; color: var(--muted); font-size: 14px; }
    @media (max-width: 760px) {
      .hero, .split { grid-template-columns: 1fr; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <main class="shell">
    <nav class="nav">
      <div class="brand">Wingman</div>
      <div>
        <a class="active" href="{{ url_for('index') }}">Dashboard</a>
        <span class="muted"> / </span>
        <a href="{{ url_for('reply_queue', platform=default_platform, status='pending') }}">Inbox</a>
        <span class="muted"> / </span>
        <a href="{{ url_for('reply_queue', platform=default_platform, status='needs_reply') }}">Reply</a>
        <span class="muted"> / </span>
        <a href="{{ url_for('admin_dashboard') }}">Admin</a>
      </div>
    </nav>

    <section class="hero">
      <div>
        <div class="muted">Inbox</div>
        <h1>{{ total_pending }}</h1>
      </div>
      <a class="launch" href="{{ url_for('reply_queue', platform=default_platform, status='pending') }}">Triage</a>
      <a class="ghost" href="{{ url_for('reply_queue', platform=default_platform, status='needs_reply') }}">Reply</a>
    </section>

    <section class="grid">
      <div class="metric"><strong>{{ youtube.pending }}</strong><span>YT inbox</span></div>
      <div class="metric"><strong>{{ instagram.pending }}</strong><span>IG inbox</span></div>
      <div class="metric"><strong>{{ youtube.needs_reply + instagram.needs_reply }}</strong><span>Reply pile</span></div>
      <div class="metric"><strong>{{ youtube.replied_today + instagram.replied_today }}</strong><span>Replied today</span></div>
    </section>

    <section class="split">
      <div class="list">
        <h2>YouTube</h2>
        {% for comment in youtube_top %}
          <div class="item">
            <strong>{{ comment["author_name"] or "Unknown" }}</strong>
            <span>{{ comment["text"] or "" }}</span>
          </div>
        {% else %}
          <div class="item"><span>Nothing pending.</span></div>
        {% endfor %}
      </div>
      <div class="list">
        <h2>Instagram</h2>
        {% for comment in instagram_top %}
          <div class="item">
            <strong>{{ comment["author_name"] or "Unknown" }}</strong>
            <span>{{ comment["text"] or "" }}</span>
          </div>
        {% else %}
          <div class="item"><span>Nothing pending.</span></div>
        {% endfor %}
      </div>
    </section>
  </main>
</body>
</html>
"""


ADMIN_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Wingman Admin</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090a0c;
      --surface: #121418;
      --surface-2: #1a1d22;
      --text: #f4f5f6;
      --muted: #8f969f;
      --line: #262a31;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.4;
    }
    .shell { width: min(1080px, calc(100vw - 28px)); margin: 22px auto 42px; }
    .nav { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 18px; }
    .nav a { color: var(--muted); text-decoration: none; font-weight: 700; }
    .nav a.active, .nav a:hover { color: var(--text); }
    .muted { color: var(--muted); }
    .hero {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 14px;
      margin-bottom: 12px;
    }
    h1 { margin: 0; font-size: clamp(30px, 6vw, 58px); line-height: .95; letter-spacing: 0; }
    h2 { margin: 22px 0 8px; font-size: 14px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
    .metric, .video {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .metric { padding: 14px; min-height: 82px; }
    .metric strong { display: block; font-size: 28px; line-height: 1; margin-bottom: 8px; }
    .metric span { color: var(--muted); font-size: 14px; }
    .video { padding: 14px; margin-bottom: 8px; }
    .video-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: start;
      margin-bottom: 10px;
    }
    .title { font-weight: 800; overflow-wrap: anywhere; }
    .counts { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }
    textarea {
      width: 100%;
      min-height: 84px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      resize: vertical;
      font: inherit;
      background: #0d0f13;
      color: var(--text);
    }
    textarea::placeholder { color: #68707a; }
    .actions { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 8px; }
    button, .button {
      border: 1px solid var(--line);
      background: var(--surface-2);
      color: var(--text);
      border-radius: 999px;
      min-height: 34px;
      padding: 0 12px;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
    }
    .notice {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      padding: 12px 14px;
      margin-bottom: 12px;
      color: var(--muted);
    }
    @media (max-width: 760px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .video-head { grid-template-columns: 1fr; }
      .counts { justify-content: flex-start; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <nav class="nav">
      <a href="{{ url_for('index') }}">Wingman</a>
      <div>
        <a href="{{ url_for('reply_queue', platform='instagram', status='pending') }}">Inbox</a>
        <span class="muted"> / </span>
        <a href="{{ url_for('reply_queue', platform='instagram', status='needs_reply') }}">Reply</a>
        <span class="muted"> / </span>
        <a class="active" href="{{ url_for('admin_dashboard') }}">Admin</a>
      </div>
    </nav>

    <section class="hero">
      <div>
        <div class="muted">Prompt context</div>
        <h1>Admin</h1>
      </div>
    </section>

    {% if message %}<div class="notice">{{ message }}</div>{% endif %}

    <section class="grid">
      <div class="metric"><strong>{{ youtube_videos|length }}</strong><span>YouTube videos</span></div>
      <div class="metric"><strong>{{ instagram_media|length }}</strong><span>Instagram media</span></div>
      <div class="metric"><strong>{{ youtube_comment_total + instagram_comment_total }}</strong><span>Total comments</span></div>
      <div class="metric"><strong>{{ described_total }}</strong><span>Descriptions set</span></div>
    </section>

    <h2>YouTube</h2>
    {% for video in youtube_videos %}
      <form class="video" method="post" action="{{ url_for('admin_save_video_description') }}">
        <input type="hidden" name="platform" value="youtube">
        <input type="hidden" name="object_id" value="{{ video.object_id }}">
        <div class="video-head">
          <div>
            <div class="title">{{ video.title or "Untitled video" }}</div>
            <div class="muted">{{ video.object_id }}</div>
          </div>
          <div class="counts">
            <span class="pill">{{ video.total_comments }} comments</span>
            <span class="pill">{{ video.pending_count }} inbox</span>
            <span class="pill">{{ video.needs_reply_count }} reply</span>
            <span class="pill">{{ video.replied_count }} done</span>
          </div>
        </div>
        <textarea name="video_description" placeholder="Prompt description for every comment on this video">{{ video.video_description or "" }}</textarea>
        <div class="actions">
          {% if video.permalink %}<a class="button" href="{{ video.permalink }}" target="_blank" rel="noreferrer">Open</a>{% else %}<span></span>{% endif %}
          <button type="submit">Save</button>
        </div>
      </form>
    {% else %}
      <div class="notice">No YouTube videos found.</div>
    {% endfor %}

    <h2>Instagram</h2>
    {% for media in instagram_media %}
      <form class="video" method="post" action="{{ url_for('admin_save_video_description') }}">
        <input type="hidden" name="platform" value="instagram">
        <input type="hidden" name="object_id" value="{{ media.object_id }}">
        <div class="video-head">
          <div>
            <div class="title">{{ media.title or "Instagram media" }}</div>
            <div class="muted">{{ media.object_id }}</div>
          </div>
          <div class="counts">
            <span class="pill">{{ media.total_comments }} comments</span>
            <span class="pill">{{ media.pending_count }} inbox</span>
            <span class="pill">{{ media.needs_reply_count }} reply</span>
            <span class="pill">{{ media.replied_count }} done</span>
          </div>
        </div>
        <textarea name="video_description" placeholder="Prompt description for every comment on this Reel">{{ media.video_description or "" }}</textarea>
        <div class="actions">
          {% if media.permalink %}<a class="button" href="{{ media.permalink }}" target="_blank" rel="noreferrer">Open</a>{% else %}<span></span>{% endif %}
          <button type="submit">Save</button>
        </div>
      </form>
    {% else %}
      <div class="notice">No Instagram media found.</div>
    {% endfor %}
  </main>
</body>
</html>
"""


REPLY_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Wingman Reply</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090a0c;
      --surface: #121418;
      --surface-2: #1a1d22;
      --text: #f4f5f6;
      --muted: #8f969f;
      --line: #262a31;
      --soft: #20242b;
      --accent: #7dd3fc;
      --danger: #fb7185;
      --ok: #86efac;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }
    a { color: inherit; }
    .shell { width: min(760px, calc(100vw - 24px)); margin: 12px auto 36px; }
    .nav { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
    .nav a { color: var(--muted); text-decoration: none; font-weight: 700; }
    .nav a.active, .nav a:hover { color: var(--text); }
    .muted { color: var(--muted); }
    .topline {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 14px;
    }
    .tabs, .platforms, .source, .buttons, .left, .right {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .tabs { margin-bottom: 10px; }
    .chip, .icon-button, button, .button {
      border: 1px solid var(--line);
      background: var(--surface);
      border-radius: 999px;
      min-height: 32px;
      padding: 0 10px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      color: var(--muted);
      font-weight: 700;
      font-size: 14px;
      font-family: inherit;
      cursor: pointer;
    }
    .chip.active, .icon-button:hover, button:hover, .button:hover {
      color: var(--text);
      border-color: #3a404a;
      background: var(--surface-2);
    }
    .chip span { margin-left: 6px; color: var(--muted); }
    .chip.primary { color: var(--bg); background: var(--text); border-color: var(--text); }
    .thread {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-height: 58vh;
    }
    .comment-row {
      display: grid;
      grid-template-columns: 38px minmax(0, 1fr);
      gap: 12px;
    }
    .avatar {
      width: 38px;
      height: 38px;
      border-radius: 50%;
      background: var(--soft);
      display: grid;
      place-items: center;
      color: var(--muted);
      font-weight: 800;
    }
    .bubble {
      background: var(--soft);
      border-radius: 8px;
      padding: 12px;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-weight: 700;
      color: var(--text);
    }
    .meta span { color: var(--muted); font-weight: 500; }
    .comment-text {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin-top: 6px;
      font-size: 16px;
    }
    .source { margin: 10px 0 0 50px; }
    .reply-form {
      margin: 16px 0 0 50px;
      border-top: 1px solid var(--line);
      padding-top: 16px;
    }
    textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      resize: vertical;
      font: inherit;
      background: var(--surface);
      color: var(--text);
    }
    textarea::placeholder { color: #68707a; }
    #reply_text { min-height: 112px; }
    #notes { min-height: 72px; }
    #rephrase_hint { min-height: 54px; margin-top: 8px; }
    .buttons { justify-content: space-between; margin-top: 10px; }
    button.primary { background: var(--text); border-color: var(--text); color: var(--bg); }
    button.good { color: var(--ok); }
    button.danger { color: var(--danger); }
    button.micro, .micro { min-width: 32px; width: 32px; padding: 0; }
    .batch { margin: 0 0 10px; }
    .notice, .error, .empty {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      padding: 12px 14px;
      margin-bottom: 12px;
      color: var(--muted);
    }
    .error { color: var(--danger); }
    .mini-form { display: inline; }
    .status-line {
      display: flex;
      gap: 10px;
      justify-content: space-between;
      margin: 12px 0;
      color: var(--muted);
      font-size: 14px;
    }
    .empty a { color: var(--text); font-weight: 800; }
    @media (max-width: 620px) {
      .reply-form, .source { margin-left: 0; }
      .comment-row { grid-template-columns: 1fr; }
      .avatar { display: none; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <nav class="nav">
      <a href="{{ url_for('index') }}">Wingman</a>
      <div class="platforms">
        <a class="{% if platform == 'youtube' %}active{% endif %}" href="{{ url_for('reply_queue', platform='youtube', status=status_filter) }}">YT</a>
        <span class="muted"> / </span>
        <a class="{% if platform == 'instagram' %}active{% endif %}" href="{{ url_for('reply_queue', platform='instagram', status=status_filter) }}">IG</a>
      </div>
    </nav>

    <div class="topline">
      <div>{{ platform_label }}</div>
      <div>{{ offset + 1 if review_count else 0 }}/{{ review_count }}</div>
    </div>
    <div class="tabs">
      <a class="chip {% if status_filter == 'pending' %}primary{% endif %}" href="{{ url_for('reply_queue', platform=platform, status='pending') }}">Inbox <span>{{ stats.pending }}</span></a>
      <a class="chip {% if status_filter == 'needs_reply' %}primary{% endif %}" href="{{ url_for('reply_queue', platform=platform, status='needs_reply') }}">Reply <span>{{ stats.needs_reply }}</span></a>
      <a class="chip {% if status_filter == 'ignored' %}active{% endif %}" href="{{ url_for('reply_queue', platform=platform, status='ignored') }}">Ignored</a>
      <a class="chip {% if status_filter == 'replied' %}active{% endif %}" href="{{ url_for('reply_queue', platform=platform, status='replied') }}">Done</a>
    </div>

    {% if message %}<div class="notice">{{ message }}</div>{% endif %}
    {% if error %}<div class="error">{{ error }}</div>{% endif %}

    {% if status_filter == 'needs_reply' %}
      <form class="batch" method="post" action="{{ url_for('generate_batch_drafts') }}">
        <input type="hidden" name="platform" value="{{ platform }}">
        <button class="button" type="submit" title="Generate drafts for everything in Reply without a draft">Batch drafts</button>
      </form>
    {% endif %}

    {% if comment %}
      <section class="thread">
        <div class="comment-row">
          <div class="avatar">{{ (comment["author_name"] or "?")[:1].upper() }}</div>
          <article class="bubble">
            <div class="meta">
              {{ comment["author_name"] or "Unknown" }}
              <span>{{ display_status }}</span>
              <span>{{ comment["like_count"] or 0 }} likes</span>
            </div>
            <div class="comment-text">{{ comment["text"] or "" }}</div>
          </article>
        </div>

        <div class="source">
          {% if video_url %}<a class="icon-button" href="{{ video_url }}" target="_blank" rel="noreferrer" title="Open video">↗</a>{% endif %}
          {% if comment_url %}<a class="icon-button" id="open_comment_link" href="{{ comment_url }}" target="_blank" rel="noreferrer" title="Open comment">⌕</a>{% endif %}
          {% if has_previous %}<a class="icon-button" id="previous_comment_link" href="{{ url_for('reply_queue', platform=platform, status=status_filter, q=search, offset=previous_offset) }}" title="Previous">←</a>{% endif %}
          {% if has_next %}<a class="icon-button" id="next_comment_link" href="{{ url_for('reply_queue', platform=platform, status=status_filter, q=search, offset=next_offset) }}" title="Next">→</a>{% endif %}
        </div>

        {% if status_filter == 'pending' %}
          <form class="reply-form" method="post" action="{{ url_for('comment_action') }}">
            <input type="hidden" name="comment_id" value="{{ comment['id'] }}">
            <input type="hidden" name="platform" value="{{ platform }}">
            <input type="hidden" name="status" value="{{ status_filter }}">
            <input type="hidden" name="q" value="{{ search }}">
            <input type="hidden" name="offset" value="{{ offset }}">
            <textarea id="notes" name="notes" placeholder="Notes for the prompt">{{ comment["notes"] or "" }}</textarea>
            <div class="buttons">
              <div class="left">
                <button class="good micro" type="submit" name="action" value="needs_reply" id="needs_reply_button" title="Needs reply">N</button>
                <button class="micro" type="submit" name="action" value="skip" id="skip_button" title="Skip">S</button>
                <button class="danger micro" type="submit" name="action" value="ignore" id="ignore_button" title="Ignore">I</button>
                <button type="submit" id="notes_button" formaction="{{ url_for('save_notes') }}" formmethod="post" title="Save notes">Save</button>
              </div>
              <div class="right">
                <span class="muted">A notes · N reply · S next · I ignore</span>
              </div>
            </div>
          </form>
        {% else %}
          <form class="reply-form" method="post" action="{{ url_for('submit_reply') }}">
            <input type="hidden" name="comment_id" value="{{ comment['id'] }}">
            <input type="hidden" name="platform" value="{{ platform }}">
            <input type="hidden" name="status" value="{{ status_filter }}">
            <input type="hidden" name="q" value="{{ search }}">
            <input type="hidden" name="offset" value="{{ offset }}">
            <textarea id="reply_text" name="reply_text" placeholder="Reply">{{ comment["ai_draft_text"] or "" }}</textarea>
            <textarea id="rephrase_hint" name="rephrase_hint" placeholder="Nudge the draft"></textarea>
            <div class="buttons">
              <div class="left">
                <button type="submit" id="draft_button" name="draft_mode" value="fresh" formaction="{{ url_for('generate_draft') }}" formmethod="post" title="Draft">Draft</button>
                <button type="submit" id="rephrase_button" name="draft_mode" value="rephrase" formaction="{{ url_for('generate_draft') }}" formmethod="post" title="Rephrase">Rephrase</button>
              </div>
              <div class="right">
                <button class="primary" type="submit" id="reply_button">Reply</button>
              </div>
            </div>
          </form>
        {% endif %}

        <div class="status-line">
          <span>{{ comment["video_title"] or platform_label }}</span>
          <span>{{ stats.pending }} inbox · {{ stats.needs_reply }} reply</span>
        </div>

        {% if status_filter != 'pending' %}
          <div class="buttons">
            <div class="left">
              <form class="mini-form" method="post" action="{{ url_for('comment_action') }}">
                <input type="hidden" name="comment_id" value="{{ comment['id'] }}">
                <input type="hidden" name="platform" value="{{ platform }}">
                <input type="hidden" name="status" value="{{ status_filter }}">
                <input type="hidden" name="q" value="{{ search }}">
                <input type="hidden" name="offset" value="{{ offset }}">
                <button type="submit" name="action" value="pending" id="pending_button" title="Back to inbox">Inbox</button>
                <button class="danger" type="submit" name="action" value="ignore" id="ignore_button" title="Ignore">Ignore</button>
              </form>
            </div>
          </div>
        {% endif %}
      </section>
    {% else %}
      <div class="empty">
        {% if status_filter == 'pending' %}
          Inbox clear. <a href="{{ url_for('reply_queue', platform=platform, status='needs_reply') }}">Go to Reply</a>.
        {% elif status_filter == 'needs_reply' %}
          Reply pile clear.
        {% else %}
          Nothing here.
        {% endif %}
      </div>
    {% endif %}
  </main>

  <script>
    document.addEventListener("keydown", function (event) {
      const active = document.activeElement;
      const isTyping = active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT");
      const key = event.key.toLowerCase();
      if (event.key === "Escape" && isTyping) {
        event.preventDefault();
        active.blur();
        return;
      }
      if (
        event.key === "Enter"
        && (event.ctrlKey || event.metaKey)
        && active
        && active.id === "notes"
      ) {
        event.preventDefault();
        const button = document.getElementById("notes_button");
        if (button) button.click();
        return;
      }
      if (
        event.key === "Enter"
        && !event.shiftKey
        && (!isTyping || (active && active.id === "reply_text"))
      ) {
        const button = document.getElementById("reply_button");
        if (button) {
          event.preventDefault();
          button.click();
          return;
        }
      }
      if (isTyping && active.id !== "reply_text") return;
      if (key === "a" && !isTyping) {
        event.preventDefault();
        const notes = document.getElementById("notes");
        if (notes) {
          notes.focus();
          return;
        }
        const reply = document.getElementById("reply_text");
        if (reply) reply.focus();
        return;
      }
      const targets = {
        "arrowleft": "previous_comment_link",
        "arrowright": "next_comment_link",
        "d": "draft_button",
        "n": "needs_reply_button",
        "p": "pending_button",
        "r": "rephrase_button",
        "s": "skip_button",
        "i": "ignore_button",
        "o": "open_comment_link"
      };
      if (targets[key] && !event.ctrlKey && !event.metaKey && !isTyping) {
        event.preventDefault();
        const target = document.getElementById(targets[key]);
        if (target) target.click();
      }
    });
  </script>
</body>
</html>
"""
def current_filter() -> str:
    status = request.values.get("status", "pending").strip()
    return status if status in VALID_FILTERS else "pending"


def current_platform() -> str:
    platform = request.values.get("platform", "youtube").strip()
    return platform if platform in VALID_PLATFORMS else "youtube"


def current_search() -> str:
    return request.values.get("q", "").strip()


def current_offset() -> int:
    offset = request.values.get("offset", "0").strip()
    return max(int(offset), 0) if offset.isdigit() else 0


def redirect_to_queue(message: str, offset: int | None = None):
    return redirect(
        url_for(
            "reply_queue",
            platform=current_platform(),
            status=current_filter(),
            q=current_search(),
            offset=current_offset() if offset is None else max(offset, 0),
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


def youtube_studio_comments_url(comment) -> str | None:
    if not comment["video_id"]:
        return None
    return f"https://studio.youtube.com/video/{comment['video_id']}/comments"


def instagram_media_url(comment) -> str | None:
    return comment["media_permalink"] or None


def instagram_comment_url(comment) -> str | None:
    return comment["media_permalink"] or None


def top_youtube_comments(connection, limit: int = 4):
    return list(
        connection.execute(
            """
            SELECT *
            FROM comments
            WHERE COALESCE(is_reply, 0) = 0
              AND COALESCE(TRIM(text), '') <> ''
              AND status = 'synced'
            ORDER BY like_count DESC, datetime(published_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )


def top_instagram_comments(connection, limit: int = 4):
    media_ids = csv_env_values("INSTAGRAM_REVIEW_MEDIA_IDS")
    clauses = [
        "COALESCE(is_reply, 0) = 0",
        "COALESCE(TRIM(text), '') <> ''",
        "status = 'synced'",
    ]
    params = []
    if media_ids:
        placeholders = ", ".join("?" for _ in media_ids)
        clauses.append(f"instagram_media_id IN ({placeholders})")
        params.extend(media_ids)
    params.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT *
            FROM instagram_comments
            WHERE {" AND ".join(clauses)}
            ORDER BY like_count DESC, datetime(published_at) DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    )


def admin_youtube_videos(connection):
    return list(
        connection.execute(
            """
            SELECT
                video_id AS object_id,
                MAX(video_title) AS title,
                MAX(video_description) AS video_description,
                'https://www.youtube.com/watch?v=' || video_id AS permalink,
                COUNT(*) AS total_comments,
                SUM(CASE WHEN status = 'synced' THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN status = 'needs_reply' THEN 1 ELSE 0 END) AS needs_reply_count,
                SUM(CASE WHEN status = 'ignored' THEN 1 ELSE 0 END) AS ignored_count,
                SUM(CASE WHEN status IN ('replied', 'externally_replied') THEN 1 ELSE 0 END) AS replied_count,
                MAX(datetime(published_at)) AS latest_comment_at
            FROM comments
            WHERE COALESCE(is_reply, 0) = 0
              AND COALESCE(video_id, '') <> ''
            GROUP BY video_id
            ORDER BY datetime(latest_comment_at) DESC, total_comments DESC
            """
        ).fetchall()
    )


def admin_instagram_media(connection):
    return list(
        connection.execute(
            """
            SELECT
                instagram_media_id AS object_id,
                COALESCE(
                    NULLIF(MAX(video_title), ''),
                    NULLIF(MAX(media_caption), ''),
                    'Instagram media'
                ) AS title,
                MAX(video_description) AS video_description,
                MAX(media_permalink) AS permalink,
                COUNT(*) AS total_comments,
                SUM(CASE WHEN status = 'synced' THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN status = 'needs_reply' THEN 1 ELSE 0 END) AS needs_reply_count,
                SUM(CASE WHEN status = 'ignored' THEN 1 ELSE 0 END) AS ignored_count,
                SUM(CASE WHEN status IN ('replied', 'externally_replied') THEN 1 ELSE 0 END) AS replied_count,
                MAX(datetime(published_at)) AS latest_comment_at
            FROM instagram_comments
            WHERE COALESCE(is_reply, 0) = 0
              AND COALESCE(instagram_media_id, '') <> ''
            GROUP BY instagram_media_id
            ORDER BY datetime(latest_comment_at) DESC, total_comments DESC
            """
        ).fetchall()
    )


def admin_update_video_description(
    connection, platform: str, object_id: str, video_description: str
) -> None:
    if platform == "instagram":
        connection.execute(
            """
            UPDATE instagram_comments
            SET video_description = ?,
                last_seen_at = datetime('now')
            WHERE instagram_media_id = ?
            """,
            (video_description, object_id),
        )
    else:
        connection.execute(
            """
            UPDATE comments
            SET video_description = ?,
                last_seen_at = datetime('now')
            WHERE video_id = ?
            """,
            (video_description, object_id),
        )
    connection.commit()


def admin_data(message: str | None = None):
    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    youtube_videos = admin_youtube_videos(connection)
    instagram_media = admin_instagram_media(connection)
    youtube_comment_total = sum(row["total_comments"] for row in youtube_videos)
    instagram_comment_total = sum(row["total_comments"] for row in instagram_media)
    described_total = sum(
        1 for row in [*youtube_videos, *instagram_media] if row["video_description"]
    )
    connection.close()
    return render_template_string(
        ADMIN_TEMPLATE,
        youtube_videos=youtube_videos,
        instagram_media=instagram_media,
        youtube_comment_total=youtube_comment_total,
        instagram_comment_total=instagram_comment_total,
        described_total=described_total,
        message=message,
    )


def dashboard_data():
    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    instagram_media_ids = csv_env_values("INSTAGRAM_REVIEW_MEDIA_IDS")
    youtube = get_queue_stats(connection)
    instagram = get_instagram_queue_stats(connection, instagram_media_ids)
    youtube_top = top_youtube_comments(connection)
    instagram_top = top_instagram_comments(connection)
    connection.close()
    default_platform = "instagram" if instagram["pending"] else "youtube"
    return render_template_string(
        DASHBOARD_TEMPLATE,
        youtube=youtube,
        instagram=instagram,
        youtube_top=youtube_top,
        instagram_top=instagram_top,
        total_pending=youtube["pending"] + instagram["pending"],
        default_platform=default_platform,
    )


def load_page_data(message: str | None = None, error: str | None = None):
    platform = current_platform()
    status_filter = current_filter()
    search = current_search()
    offset = current_offset()

    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    instagram_media_ids = csv_env_values("INSTAGRAM_REVIEW_MEDIA_IDS")
    if platform == "instagram":
        review_count = count_instagram_review_comments(
            connection, status_filter, search, instagram_media_ids
        )
    else:
        review_count = count_review_comments(connection, status_filter, search)
    if review_count and offset >= review_count:
        offset = review_count - 1
    if platform == "instagram":
        comment = get_next_instagram_review_comment(
            connection, status_filter, search, offset, instagram_media_ids
        )
        pending_count = count_pending_instagram_comments(
            connection, instagram_media_ids
        )
        stats = get_instagram_queue_stats(connection, instagram_media_ids)
    else:
        comment = get_next_review_comment(connection, status_filter, search, offset)
        pending_count = count_pending_top_level_comments(connection)
        stats = get_queue_stats(connection)
    connection.close()

    display_status = None
    video_url = None
    comment_url = None
    studio_url = None
    if comment:
        display_status = "pending" if comment["status"] == "synced" else comment["status"]
        display_status = display_status.replace("_", " ")
        if platform == "instagram":
            video_url = instagram_media_url(comment)
            comment_url = instagram_comment_url(comment)
            studio_url = None
        else:
            video_url = youtube_video_url(comment)
            comment_url = youtube_comment_url(comment)
            studio_url = youtube_studio_comments_url(comment)

    return render_template_string(
        REPLY_TEMPLATE,
        platform=platform,
        platform_label="Instagram" if platform == "instagram" else "YouTube",
        comment=comment,
        pending_count=pending_count,
        review_count=review_count,
        offset=offset,
        previous_offset=max(offset - 1, 0),
        next_offset=offset + 1 if offset + 1 < review_count else offset,
        has_previous=offset > 0,
        has_next=offset + 1 < review_count,
        stats=stats,
        status_filter=status_filter,
        search=search,
        filters=[
            ("pending", "Pending"),
            ("needs_reply", "Later"),
            ("skipped", "Skipped"),
            ("externally_replied", "Done"),
            ("ignored", "Ignored"),
            ("replied", "Replied"),
            ("all", "All"),
        ],
        display_status=display_status,
        video_url=video_url,
        comment_url=comment_url,
        studio_url=studio_url,
        message=message,
        error=error,
    )


@app.get("/")
def index():
    return dashboard_data()


@app.get("/admin")
def admin_dashboard():
    return admin_data(message=request.args.get("message"))


@app.post("/admin/video-description")
def admin_save_video_description():
    platform = request.form.get("platform", "").strip()
    object_id = request.form.get("object_id", "").strip()
    video_description = request.form.get("video_description", "").strip()

    if platform not in VALID_PLATFORMS:
        return admin_data(message="Unknown platform."), 400
    if not object_id:
        return admin_data(message="Missing video/media ID."), 400

    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    admin_update_video_description(
        connection,
        platform,
        object_id,
        video_description,
    )
    connection.close()
    return redirect(
        url_for(
            "admin_dashboard",
            message="Prompt description saved.",
        )
    )


@app.get("/reply")
def reply_queue():
    return load_page_data(message=request.args.get("message"))


@app.post("/reply")
def submit_reply():
    platform = current_platform()
    comment_id = request.form.get("comment_id", "").strip()
    reply_text = request.form.get("reply_text", "").strip()

    if not comment_id.isdigit():
        return load_page_data(error="Missing or invalid comment ID."), 400

    if not reply_text:
        return load_page_data(error="Write a reply before submitting."), 400

    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    if platform == "instagram":
        comment = get_instagram_comment_by_id(connection, int(comment_id))
    else:
        comment = get_comment_by_id(connection, int(comment_id))

    if not comment:
        connection.close()
        return load_page_data(error="That comment is no longer in the database."), 404

    if comment["status"] == "replied":
        connection.close()
        return redirect_to_queue("That comment was already replied to.")

    if comment["status"] == "externally_replied":
        connection.close()
        return redirect_to_queue("That comment already has an external reply.")

    if comment["is_reply"]:
        connection.close()
        return load_page_data(error="Wingman only replies to top-level comments."), 400

    if platform == "instagram":
        try:
            client = InstagramClient(INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_API_VERSION)
            instagram_reply_id = post_instagram_reply_to_comment(
                client,
                comment["instagram_comment_id"],
                reply_text,
            )
            mark_instagram_comment_replied(
                connection,
                int(comment_id),
                reply_text,
                instagram_reply_id,
            )
        except InstagramApiError as exc:
            connection.close()
            return load_page_data(error=str(exc)), 500

        connection.close()
        return redirect_to_queue("Reply posted to Instagram.", offset=0)

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
    return redirect_to_queue("Reply posted to YouTube.", offset=0)


@app.post("/draft")
def generate_draft():
    platform = current_platform()
    comment_id = request.form.get("comment_id", "").strip()
    current_reply = request.form.get("reply_text", "").strip()
    rephrase_hint = request.form.get("rephrase_hint", "").strip()
    should_rephrase = request.form.get("draft_mode") == "rephrase"

    if not comment_id.isdigit():
        return load_page_data(error="Missing or invalid comment ID."), 400

    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    if platform == "instagram":
        comment = get_instagram_comment_by_id(connection, int(comment_id))
    else:
        comment = get_comment_by_id(connection, int(comment_id))

    if not comment:
        connection.close()
        return load_page_data(error="That comment is no longer in the database."), 404

    if comment["is_reply"]:
        connection.close()
        return load_page_data(error="Wingman only drafts replies to top-level comments."), 400

    try:
        if should_rephrase:
            draft = refine_reply_draft(dict(comment), current_reply, rephrase_hint)
        else:
            draft = generate_reply_draft(dict(comment))
        if platform == "instagram":
            save_instagram_ai_draft(
                connection,
                int(comment_id),
                draft.text,
                draft.model,
                draft.provider,
                draft.prompt_version,
                draft.prompt_text,
            )
        else:
            save_ai_draft(
                connection,
                int(comment_id),
                draft.text,
                draft.model,
                draft.provider,
                draft.prompt_version,
                draft.prompt_text,
            )
    except AIDraftError as exc:
        connection.close()
        return load_page_data(error=str(exc)), 500

    connection.close()
    message = "Draft rephrased." if should_rephrase else "Draft generated."
    return redirect_to_queue(message)


@app.post("/action")
def comment_action():
    platform = current_platform()
    comment_id = request.form.get("comment_id", "").strip()
    action = request.form.get("action", "").strip()
    notes = request.form.get("notes")

    if not comment_id.isdigit():
        return load_page_data(error="Missing or invalid comment ID."), 400

    actions = {
        "needs_reply": ("needs_reply", "needs_reply", "Marked as needs reply."),
        "pending": ("synced", "pending", "Moved back to pending."),
        "skip": ("skipped", "skip", "Skipped for now."),
        "ignore": ("ignored", "ignore", "Ignored. It will not appear in the normal queue."),
    }
    if action not in actions:
        return load_page_data(error="Unknown action."), 400

    status, action_name, message = actions[action]
    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    if notes is not None:
        if platform == "instagram":
            update_instagram_comment_notes(connection, int(comment_id), notes.strip())
        else:
            update_comment_notes(connection, int(comment_id), notes.strip())

    if action == "skip" and current_filter() == "pending":
        connection.close()
        return redirect_to_queue("Skipped.", offset=current_offset() + 1)

    if platform == "instagram":
        update_instagram_comment_status(
            connection,
            int(comment_id),
            status,
            skip_minutes=SKIP_MINUTES,
        )
    else:
        update_comment_status(
            connection,
            int(comment_id),
            status,
            action_name,
            skip_minutes=SKIP_MINUTES,
        )
    connection.close()
    return redirect_to_queue(message)


@app.post("/drafts/batch")
def generate_batch_drafts():
    platform = current_platform()
    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    if platform == "instagram":
        comments = get_instagram_comments_needing_drafts(connection)
    else:
        comments = get_comments_needing_drafts(connection)

    generated_count = 0
    failed_count = 0
    first_error = None

    for comment in comments:
        try:
            draft = generate_reply_draft(dict(comment))
            if platform == "instagram":
                save_instagram_ai_draft(
                    connection,
                    int(comment["id"]),
                    draft.text,
                    draft.model,
                    draft.provider,
                    draft.prompt_version,
                    draft.prompt_text,
                )
            else:
                save_ai_draft(
                    connection,
                    int(comment["id"]),
                    draft.text,
                    draft.model,
                    draft.provider,
                    draft.prompt_version,
                    draft.prompt_text,
                )
            generated_count += 1
        except AIDraftError as exc:
            failed_count += 1
            if first_error is None:
                first_error = str(exc)
            break

    connection.close()

    if failed_count:
        return redirect(
            url_for(
                "reply_queue",
                platform=platform,
                status="needs_reply",
                message=(
                    f"Generated {generated_count} drafts before an error: "
                    f"{first_error}"
                ),
            )
        )

    return redirect(
        url_for(
            "reply_queue",
            platform=platform,
            status="needs_reply",
            message=f"Generated {generated_count} batch drafts.",
        )
    )


@app.post("/notes")
def save_notes():
    platform = current_platform()
    comment_id = request.form.get("comment_id", "").strip()
    notes = request.form.get("notes", "").strip()

    if not comment_id.isdigit():
        return load_page_data(error="Missing or invalid comment ID."), 400

    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    if platform == "instagram":
        update_instagram_comment_notes(connection, int(comment_id), notes)
    else:
        update_comment_notes(connection, int(comment_id), notes)
    connection.close()
    return redirect_to_queue("Notes saved.")


@app.post("/video-description")
def save_video_description():
    platform = current_platform()
    comment_id = request.form.get("comment_id", "").strip()
    video_description = request.form.get("video_description", "").strip()

    if not comment_id.isdigit():
        return load_page_data(error="Missing or invalid comment ID."), 400

    connection = connect(DATABASE_PATH)
    initialize_database(connection)
    if platform == "instagram":
        update_instagram_video_description(connection, int(comment_id), video_description)
    else:
        update_video_description(connection, int(comment_id), video_description)
    connection.close()
    return redirect_to_queue("Video description saved.")


@app.post("/undo")
def undo_last_action():
    if current_platform() == "instagram":
        return redirect_to_queue("Undo is not wired for Instagram actions yet.")

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
    return redirect_to_queue("Last action undone.", offset=0)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=WINGMAN_PORT, debug=False)
