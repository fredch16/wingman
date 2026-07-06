import argparse

from api_helpers import (
    discover_user_id,
    get_json,
    load_access_token,
    parse_key_values,
    print_json,
)


USER_ID = ""
ENDPOINT_TEMPLATE = "{user_id}/media"
FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,comments_count,like_count"
LIMIT = "25"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call Instagram API /<IG_ID>/media and pretty-print the JSON response."
    )
    parser.add_argument("--user-id", default=USER_ID)
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--fields", default=FIELDS)
    parser.add_argument("--limit", default=LIMIT)
    parser.add_argument("--version", default=None)
    parser.add_argument("--param", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    access_token = load_access_token()
    user_id = args.user_id or discover_user_id(access_token, args.version)
    endpoint = args.endpoint or ENDPOINT_TEMPLATE.format(user_id=user_id)

    params = parse_key_values(args.param)
    if args.fields:
        params["fields"] = args.fields
    if args.limit:
        params["limit"] = args.limit

    result = get_json(endpoint, access_token, params, args.version)
    print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
