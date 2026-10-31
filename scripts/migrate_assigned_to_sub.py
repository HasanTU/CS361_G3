# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.28.0",
#     "pymongo>=4.3.0",
#     "dnspython>=2.3.0",
# ]
# ///
"""One-time migration: rewrite legacy `assigned_to` values to Cognito `sub` values.

Usage:
    uv run scripts/migrate_assigned_to_sub.py                         # dry run, writes nothing
    uv run scripts/migrate_assigned_to_sub.py --apply                 # writes a backup, then updates
    uv run scripts/migrate_assigned_to_sub.py --apply --backup-file PATH
    uv run scripts/migrate_assigned_to_sub.py --restore PATH          # puts assigned_to back from a backup
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aws_deploy.helpers.auth import load_env_file, normalize_mongo_uri
from backend_v2.shared.domain.directory_user import DirectoryUser, resolve_recipient

# Legacy alias table, derived from KNOWN_USER_ALIASES in the old documents/search
# handlers. Keys are compared after .strip().casefold().
LEGACY_ALIAS_TO_EMAIL = {
    "คุณธารินี สุขสวัสดิ์": "staff1@tu.ac.th", "staff1": "staff1@tu.ac.th", "staff-1": "staff1@tu.ac.th",
    "อ.ดร.ปกรณ์ สุขใจ": "head1@tu.ac.th", "หัวหน้าสาขาวิชาวิทยาการคอมพิวเตอร์": "head1@tu.ac.th",
    "หัวหน้าสาขา": "head1@tu.ac.th", "head1": "head1@tu.ac.th", "head-1": "head1@tu.ac.th",
    "อ.ดร.วรินทร์ พงษ์ดี": "fac_a@tu.ac.th", "fac_a": "fac_a@tu.ac.th", "fac-a": "fac_a@tu.ac.th",
    "อ.ดร.ณัฐวุฒิ ศรีวัฒน์": "fac_b@tu.ac.th", "fac_b": "fac_b@tu.ac.th", "fac-b": "fac_b@tu.ac.th",
    "kasidit": "admin1@tu.ac.th", "admin1": "admin1@tu.ac.th", "admin one": "admin1@tu.ac.th",
    "saowaluk": "admin2@tu.ac.th", "admin2": "admin2@tu.ac.th", "admin two": "admin2@tu.ac.th",
    "prapaporn": "user1@tu.ac.th", "user1": "user1@tu.ac.th", "user one": "user1@tu.ac.th",
    "songsakdi": "user2@tu.ac.th", "user2": "user2@tu.ac.th", "user two": "user2@tu.ac.th",
    "wilawan": "user3@tu.ac.th", "user3": "user3@tu.ac.th", "user three": "user3@tu.ac.th",
}


@dataclass
class PlannedChange:
    doc_id: str
    title: str
    old_assigned_to: Any
    new_assigned_to: list[str]


@dataclass
class UnresolvedDoc:
    doc_id: str
    title: str
    unresolved_values: list[str]


@dataclass
class MigrationPlan:
    total: int
    unchanged: int
    changes: list[PlannedChange]
    unresolved: list[UnresolvedDoc]


def load_directory_users(cognito_client: Any, pool_id: str) -> list[DirectoryUser]:
    """List enabled Cognito users (same filtering as CognitoUserDirectory.list_users)."""
    users: list[DirectoryUser] = []
    paginator = cognito_client.get_paginator("list_users")
    for page in paginator.paginate(UserPoolId=pool_id):
        for raw in page.get("Users", []):
            if raw.get("Enabled") is False:
                continue
            attrs = {a["Name"]: a["Value"] for a in raw.get("Attributes", [])}
            sub = attrs.get("sub")
            if not sub:
                continue
            email = attrs.get("email", "")
            name = attrs.get("name") or email.split("@")[0] or sub
            users.append(DirectoryUser(sub=sub, email=email, name=name))
    users.sort(key=lambda u: (u.name.casefold(), u.sub))
    return users


def legacy_values(record: dict) -> list[str]:
    """Return the identity strings that granted access to a stored document."""
    raw = record.get("assigned_to")
    if isinstance(raw, list):
        values = [str(x).strip() for x in raw if x is not None and str(x).strip()]
    elif isinstance(raw, str):
        values = [raw.strip()] if raw.strip() else []
    else:
        values = []
    if not values:
        receiver = record.get("receiver")
        if isinstance(receiver, str) and receiver.strip():
            values = [receiver.strip()]
    return values


def resolve_legacy_identity(value: str, users: Sequence[DirectoryUser]) -> Optional[str]:
    """Return the sub that a legacy value identified, or None when it is unknown or ambiguous."""
    candidate = LEGACY_ALIAS_TO_EMAIL.get(value.strip().casefold(), value)
    try:
        return resolve_recipient(candidate, users).sub
    except ValueError:
        return None


def plan_migration(records: Iterable[dict], users: Sequence[DirectoryUser]) -> MigrationPlan:
    """Compute the changes without touching any database."""
    total = 0
    unchanged = 0
    changes: list[PlannedChange] = []
    unresolved: list[UnresolvedDoc] = []
    for record in records:
        total += 1
        doc_id = str(record.get("_id"))
        title = str(record.get("title") or "")
        values = legacy_values(record)
        if not values:
            unchanged += 1
            continue
        resolved: list[Optional[str]] = [resolve_legacy_identity(v, users) for v in values]
        bad = [v for v, sub in zip(values, resolved) if sub is None]
        if bad:
            unresolved.append(UnresolvedDoc(doc_id, title, bad))
            continue
        new: list[str] = []
        for sub in resolved:
            if sub not in new:
                new.append(sub)
        old_raw = record.get("assigned_to")
        if new == old_raw:
            unchanged += 1
            continue
        changes.append(PlannedChange(doc_id, title, old_raw, new))
    return MigrationPlan(total, unchanged, changes, unresolved)


def _fmt(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def print_plan(plan: MigrationPlan) -> None:
    for change in plan.changes:
        print(f"{change.doc_id}  {change.title}  {_fmt(change.old_assigned_to)} -> {_fmt(change.new_assigned_to)}")
    for doc in plan.unresolved:
        print(f"UNRESOLVED  {doc.doc_id}  {doc.title}  {_fmt(doc.unresolved_values)}")
    print(
        f"total={plan.total} unchanged={plan.unchanged} "
        f"to_update={len(plan.changes)} unresolved={len(plan.unresolved)}"
    )


def default_backup_path() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"/tmp/assigned_to_backup_{stamp}.json"


def write_backup(path: str, changes: Sequence[PlannedChange]) -> None:
    """Write the old `assigned_to` values. Refuses to overwrite an existing file."""
    entries = [{"_id": c.doc_id, "assigned_to": c.old_assigned_to} for c in changes]
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(entries, handle, ensure_ascii=False, indent=2)


def apply_changes(collection: Any, changes: Sequence[PlannedChange], backup_path: str) -> tuple[int, list[str]]:
    """Write the backup, then update. Returns (matched count, doc ids that did not match)."""
    from bson import ObjectId

    write_backup(backup_path, changes)
    matched = 0
    missed: list[str] = []
    for change in changes:
        result = collection.update_one(
            {"_id": ObjectId(change.doc_id), "assigned_to": change.old_assigned_to},
            {"$set": {"assigned_to": change.new_assigned_to}},
        )
        if getattr(result, "matched_count", 0) > 0:
            matched += 1
        else:
            missed.append(change.doc_id)
    return matched, missed


def restore_backup(collection: Any, backup_path: str) -> tuple[int, list[str]]:
    """Put `assigned_to` back from a backup file. Returns (matched count, ids not found)."""
    from bson import ObjectId

    with open(backup_path, "r", encoding="utf-8") as handle:
        entries = json.load(handle)
    matched = 0
    missed: list[str] = []
    for entry in entries:
        result = collection.update_one(
            {"_id": ObjectId(entry["_id"])},
            {"$set": {"assigned_to": entry["assigned_to"]}},
        )
        if getattr(result, "matched_count", 0) > 0:
            matched += 1
        else:
            missed.append(str(entry["_id"]))
    return matched, missed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rewrite legacy assigned_to values (names, emails, aliases) to Cognito sub values. "
        "Dry run unless --apply is given."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write a backup, then update the documents")
    mode.add_argument("--restore", metavar="PATH", help="put assigned_to back from a backup file")
    parser.add_argument("--backup-file", metavar="PATH", help="backup path for --apply (default: /tmp/assigned_to_backup_<UTC time>.json)")
    parser.add_argument("--user-pool-id", metavar="ID", help="Cognito user pool id (default: env COGNITO_USER_POOL_ID)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_file()

    mongo_uri = normalize_mongo_uri(os.getenv("MONGO_URI", ""))
    pool_id = args.user_pool_id or os.getenv("COGNITO_USER_POOL_ID", "")
    if not mongo_uri:
        print("error: MONGO_URI is not set.", file=sys.stderr)
        return 2
    if not args.restore and not pool_id:
        print("error: COGNITO_USER_POOL_ID is not set (or pass --user-pool-id).", file=sys.stderr)
        return 2
    if not args.restore and "_" not in pool_id:
        print("error: the user pool id must look like <region>_<id>.", file=sys.stderr)
        return 2

    from pymongo import MongoClient

    client = MongoClient(mongo_uri)
    try:
        collection = client.get_default_database()["documents"]
    except Exception as err:
        print(f"error: MONGO_URI has no default database ({type(err).__name__}).", file=sys.stderr)
        return 2

    if args.restore:
        try:
            matched, missed = restore_backup(collection, args.restore)
        except (OSError, ValueError, KeyError) as err:
            print(f"error: cannot restore from {args.restore} ({type(err).__name__}).", file=sys.stderr)
            return 2
        print(f"restored={matched} not_found={len(missed)}")
        for doc_id in missed:
            print(f"NOT FOUND  {doc_id}")
        return 0

    import boto3

    cognito = boto3.client("cognito-idp", region_name=pool_id.split("_", 1)[0])
    users = load_directory_users(cognito, pool_id)
    plan = plan_migration(collection.find({}), users)
    print_plan(plan)

    if args.apply and plan.changes:
        backup_path = args.backup_file or default_backup_path()
        try:
            matched, missed = apply_changes(collection, plan.changes, backup_path)
        except FileExistsError:
            print(f"error: backup file already exists: {backup_path}", file=sys.stderr)
            return 2
        print(f"backup={backup_path}")
        print(f"updated={matched} not_matched={len(missed)}")
        for doc_id in missed:
            print(f"NOT MATCHED (changed concurrently?)  {doc_id}")
    elif args.apply:
        print("nothing to update; no backup written")
    else:
        print("dry run: nothing written (use --apply to update)")

    return 1 if plan.unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
