"""删除超过保留期的工具审计记录：python -m scripts.purge_audit [days]。"""
from __future__ import annotations

import sys

from app.db.session import SessionLocal
from app.observability.retention import purge_expired_audit


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else None
    with SessionLocal() as db:
        deleted = purge_expired_audit(db, days=days)
    print(f"purged_agent_tool_calls={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
