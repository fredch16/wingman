import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv


def env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_access_token() -> str:
    load_dotenv()
    access_token = env_value("INSTAGRAM_ACCESS_TOKEN")
    if not access_token:
        raise SystemExit("INSTAGRAM_ACCESS_TOKEN is missing from .env.")
    return access_token


def instagram_base_url(version: str | None = None) -> str:
    version = version or env_value("INSTAGRAM_GRAPH_API_VERSION", "v25.0")
    return f"https://graph.instagram.com/{version.strip('/')}"


def parse_key_values(values: list[str]) -> dict[str, str]:
    params = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Invalid --param value {value!r}. Use key=value.")
        key, param_value = value.split("=", 1)
        params[key.strip()] = param_value.strip()
    return params


def _request_json(url: str, access_token: str) -> dict:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Instagram API error {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"Could not reach Instagram API: {exc}") from exc


def _endpoint_url(endpoint: str, params: dict[str, str], version: str | None = None) -> str:
    if endpoint.startswith("https://"):
        return endpoint
    return f"{instagram_base_url(version)}/{endpoint.strip('/')}?{urlencode(params)}"


def get_json(endpoint: str, access_token: str, params: dict[str, str], version: str | None = None) -> dict:
    return _request_json(_endpoint_url(endpoint, params, version), access_token)


def get_paged_json(
    endpoint: str,
    access_token: str,
    params: dict[str, str],
    version: str | None = None,
) -> dict:
    url = _endpoint_url(endpoint, params, version)
    all_data = []
    page_count = 0
    last_response = {}

    while url:
        response = _request_json(url, access_token)
        page_count += 1
        last_response = response
        all_data.extend(response.get("data", []))
        url = response.get("paging", {}).get("next")

    result = {"data": all_data, "page_count": page_count}
    if "paging" in last_response:
        result["last_page_paging"] = last_response["paging"]
    return result


def discover_user_id(access_token: str, version: str | None = None) -> str:
    response = get_json(
        "me",
        access_token,
        {"fields": "user_id,username"},
        version,
    )
    user_id = response.get("user_id")
    if not user_id:
        raise SystemExit("Could not discover user_id from /me. Pass an ID explicitly.")
    return user_id


def print_json(value: dict) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))
