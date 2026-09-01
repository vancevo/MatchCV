import sys
import urllib.error

from app.master_seed import create_master_user
from app.main import seed


if __name__ == "__main__":
    try:
        user = create_master_user()
        owner_id = user["id"]
        seed(owner_id)
        print(f"Master account seeded: {user['email']} ({owner_id})")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        print(f"Supabase admin request failed: {exc.code} {details}", file=sys.stderr)
        raise
