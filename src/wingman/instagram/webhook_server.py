"""Expose only Instagram's signed webhook, never the Wingman dashboard."""

from __future__ import annotations

import os
from typing import Callable

from dotenv import load_dotenv
from werkzeug.serving import WSGIRequestHandler, run_simple
from werkzeug.wrappers import Response

from wingman.web import create_app


class WebhookRequestHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-") -> None:
        # Meta's verification token is in the query string: log the path only.
        path = self.path.partition("?")[0]
        print(f"Webhook HTTP {self.command} {path} {code}", flush=True)


def webhook_only_app() -> Callable:
    flask_app = create_app({"START_REPLY_WORKER": False})

    def application(environ, start_response):
        if environ.get("PATH_INFO") != "/webhooks/instagram":
            return Response("Not found", status=404)(environ, start_response)
        return flask_app(environ, start_response)

    return application


def main() -> None:
    load_dotenv()
    if not os.getenv("INSTAGRAM_APP_SECRET") or not os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN"):
        raise SystemExit(
            "Set INSTAGRAM_APP_SECRET and INSTAGRAM_WEBHOOK_VERIFY_TOKEN in .env first."
        )
    port = int(os.getenv("WINGMAN_WEBHOOK_PORT", "5001"))
    print(f"Webhook-only listener on http://127.0.0.1:{port}/webhooks/instagram", flush=True)
    run_simple(
        "127.0.0.1", port, webhook_only_app(), use_debugger=False,
        use_reloader=False, threaded=True, request_handler=WebhookRequestHandler,
    )


if __name__ == "__main__":
    main()
