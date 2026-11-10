#!/usr/bin/env bash
# Run from a tested checkout on wingman-pi as fred.
set -Eeuo pipefail

source_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
target=/home/fred/wingman-hackathon
backup_dir=/home/fred/wingman-deploy-backups
revision=$(git -C "$source_dir" rev-parse HEAD)
files=(src deploy tests pyproject.toml requirements.txt README.md app.py fetch_comments.py wingman)

[[ "$source_dir" != "$target" ]]
mkdir -p "$backup_dir"
exec 9>"$backup_dir/deploy.lock"
flock -n 9
backup="$backup_dir/$(date -u +%Y%m%dT%H%M%SZ)-${revision:0:12}.tar.gz"
tar --exclude='__pycache__' --exclude='*.egg-info' -czf "$backup" -C "$target" "${files[@]}"

restart() {
  sudo -n /usr/bin/systemctl restart wingman-web wingman-webhook
}

copy_code() {
  local from=$1 item
  for item in "${files[@]}"; do
    if [[ -d "$from/$item" ]]; then
      rsync -a --checksum --delete --exclude='__pycache__' --exclude='*.egg-info' "$from/$item/" "$target/$item/" || return
    else
      cp "$from/$item" "$target/$item" || return
    fi
  done
}

rollback() {
  trap - ERR
  echo "Deployment failed; restoring $backup" >&2
  restore_dir=$(mktemp -d "$backup_dir/restore.XXXXXX")
  tar -xzf "$backup" -C "$restore_dir"
  copy_code "$restore_dir"
  rm -rf "$restore_dir"
  "$target/.venv/bin/python" -m pip install --no-deps --no-build-isolation -e "$target" || true
  restart || true
  exit 1
}
trap rollback ERR

# Only application files are copied: .env, credentials, creator.md and state stay local.
copy_code "$source_dir"
"$target/.venv/bin/python" -m pip install -e "$target"
restart
curl --retry 10 --retry-connrefused --retry-delay 1 --max-time 15 -fsS -o /dev/null http://127.0.0.1:5000/
# The webhook-only listener intentionally rejects its root path.
webhook_status=$(curl --retry 10 --retry-connrefused --retry-delay 1 --max-time 15 -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:5001/)
[[ "$webhook_status" == 404 ]]
systemctl is-active --quiet wingman-web wingman-webhook
curl -fsS http://127.0.0.1:5000/static/app.js | cmp - "$target/src/wingman/static/app.js"
printf '%s\n' "$revision" > "$target/.deployed-revision"
trap - ERR
echo "Deployed $revision; dashboard and webhook checks passed. Backup: $backup"
