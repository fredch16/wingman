import argparse

from api_helpers import get_json, load_access_token, parse_key_values, print_json


ENDPOINT = "me"
FIELDS = "id,user_id,username,name,account_type,profile_picture_url,followers_count,follows_count,media_count"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call Instagram API /me and pretty-print the JSON response."
    )
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--fields", default=FIELDS)
    parser.add_argument("--version", default=None)
    parser.add_argument("--param", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    params = parse_key_values(args.param)
    if args.fields:
        params["fields"] = args.fields

    result = get_json(args.endpoint, load_access_token(), params, args.version)
    print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
