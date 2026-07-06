import argparse

from api_helpers import (
    get_json,
    get_paged_json,
    load_access_token,
    parse_key_values,
    print_json,
)


MEDIA_ID = "18418211902178416"
ENDPOINT = f"{MEDIA_ID}/comments"
FIELDS = "id,text,username,timestamp,like_count"
LIMIT = "25"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call Instagram API /<IG_MEDIA_ID>/comments and pretty-print the JSON response."
    )
    parser.add_argument("--media-id", default=MEDIA_ID)
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--fields", default=FIELDS)
    parser.add_argument("--limit", default=LIMIT)
    parser.add_argument("--version", default=None)
    parser.add_argument(
        "--first-page",
        action="store_true",
        help="Only print the first API response instead of following paging.next.",
    )
    parser.add_argument("--param", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    endpoint = args.endpoint or (
        ENDPOINT if args.media_id == MEDIA_ID else f"{args.media_id}/comments"
    )

    params = parse_key_values(args.param)
    if args.fields:
        params["fields"] = args.fields
    if args.limit:
        params["limit"] = args.limit

    access_token = load_access_token()
    if args.first_page:
        result = get_json(endpoint, access_token, params, args.version)
    else:
        result = get_paged_json(endpoint, access_token, params, args.version)
    print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
