# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.28.0",
#     "pymongo>=4.0",
# ]
# ///

"""Seed and clean up load-test data for the deployed e-Mailbox (backend_v2).

Creates fake Cognito accounts, fake documents in MongoDB (`documents` collection)
and one small PDF per document in S3, all tagged so they can be removed later.

Usage (Learner Lab credentials exported, MONGO_URI + COGNITO_USER_POOL_ID in .env):
    uv run aws_deploy/loadtest_data.py seed                 # 8 users + 500 documents
    uv run aws_deploy/loadtest_data.py seed --documents 1000
    uv run aws_deploy/loadtest_data.py status               # count what's tagged
    uv run aws_deploy/loadtest_data.py cleanup              # delete everything below

What cleanup removes:
    - every Mongo document with `loadtest: true` (seeded rows), plus any document
      uploaded by or assigned to a load-test account (rows Locust created via the API)
    - the S3 object behind each of those documents
    - the load-test Cognito users (loadtestNN@example.com)
Real users' data is never touched: only the accounts listed in LOADTEST_USERS.
"""

import argparse
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from botocore.exceptions import ClientError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aws_deploy.helpers.auth import get_aws_session, get_mongo_uri  # noqa: E402

# Shared password for every load-test account (Locust logs in with it).
# Test-only value; override with LOADTEST_PASSWORD if you want something else.
LOADTEST_PASSWORD = os.getenv("LOADTEST_PASSWORD", "LoadTest123!")

LOADTEST_USERS = [
    {"email": "loadtest01@example.com", "name": "LoadTest Admin 01", "group": "ADMIN"},
    {"email": "loadtest02@example.com", "name": "LoadTest Admin 02", "group": "ADMIN"},
    {"email": "loadtest03@example.com", "name": "LoadTest User 03", "group": "USER"},
    {"email": "loadtest04@example.com", "name": "LoadTest User 04", "group": "USER"},
    {"email": "loadtest05@example.com", "name": "LoadTest User 05", "group": "USER"},
    {"email": "loadtest06@example.com", "name": "LoadTest User 06", "group": "USER"},
    {"email": "loadtest07@example.com", "name": "LoadTest User 07", "group": "USER"},
    {"email": "loadtest08@example.com", "name": "LoadTest User 08", "group": "USER"},
]
LOADTEST_EMAILS = [u["email"] for u in LOADTEST_USERS]

TITLE_PREFIX = "[LOADTEST]"
S3_KEY_PREFIX = "uploads/loadtest_"  # must contain "uploads/" for S3FileStorageAdapter.get_file

TITLES = [
    "ประกาศกำหนดการประชุมคณะกรรมการ",
    "หนังสือเชิญเข้าร่วมอบรม",
    "แจ้งกำหนดส่งผลการประเมิน",
    "ขออนุมัติจัดโครงการสัมมนา",
    "บันทึกข้อความขอใช้ห้องประชุม",
    "แจ้งเวียนระเบียบการเบิกจ่าย",
    "ขอความอนุเคราะห์ส่งรายชื่อ",
    "รายงานผลการดำเนินงานประจำไตรมาส",
]
SENDERS = [
    "สำนักงานเลขานุการคณะ",
    "ฝ่ายวิชาการ",
    "งานทะเบียนและประเมินผล",
    "งานการเงินและพัสดุ",
    "ศูนย์บริการวิชาการ",
]
# Statuses reachable from registration, each with its own valid history path.
STATUS_PATHS = [
    ["RECEIVED", "ASSIGNED"],
    ["RECEIVED", "ASSIGNED", "ACKNOWLEDGED"],
    ["RECEIVED", "ASSIGNED", "ACKNOWLEDGED", "IN_PROGRESS"],
    ["RECEIVED", "ASSIGNED", "ACKNOWLEDGED", "IN_PROGRESS", "COMPLETED"],
]


def tiny_pdf(text: str) -> bytes:
    """Build a minimal valid one-page PDF (~600 bytes) so preview/download works."""
    safe = "".join(c for c in text if c.isascii() and c not in "()\\")[:60]
    stream = f"BT /F1 14 Tf 50 750 Td ({safe}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def connect(session):
    """Return (cognito_client, s3_client, mongo documents collection, pool_id, bucket)."""
    from pymongo import MongoClient

    pool_id = os.getenv("COGNITO_USER_POOL_ID")
    if not pool_id:
        sys.exit("error: COGNITO_USER_POOL_ID is not set (see aws_deploy/setup_cognito.py)")
    account_id = session.client("sts").get_caller_identity()["Account"]
    bucket = os.getenv("S3_BUCKET_NAME") or f"cs361-g3-documents-{account_id}"

    mongo_uri = get_mongo_uri()
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000)
    db = client.get_default_database()
    return (
        session.client("cognito-idp"),
        session.client("s3"),
        db["documents"],
        pool_id,
        bucket,
    )


# --------------------------------------------------------------------------- users


def ensure_users(cognito, pool_id: str) -> dict[str, dict]:
    """Create (or refresh) the load-test accounts and return {email: {sub, name, group}}."""
    result = {}
    for user in LOADTEST_USERS:
        email = user["email"]
        try:
            cognito.admin_create_user(
                UserPoolId=pool_id,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "name", "Value": user["name"]},
                ],
                MessageAction="SUPPRESS",
            )
            print(f" + created {email}")
        except ClientError as err:
            if err.response["Error"]["Code"] != "UsernameExistsException":
                raise
            print(f" = exists  {email}")

        cognito.admin_set_user_password(
            UserPoolId=pool_id, Username=email, Password=LOADTEST_PASSWORD, Permanent=True
        )
        cognito.admin_add_user_to_group(
            UserPoolId=pool_id, Username=email, GroupName=user["group"]
        )
        attrs = cognito.admin_get_user(UserPoolId=pool_id, Username=email)["UserAttributes"]
        sub = next(a["Value"] for a in attrs if a["Name"] == "sub")
        result[email] = {"sub": sub, "name": user["name"], "group": user["group"]}
    return result


def existing_user_subs(cognito, pool_id: str) -> dict[str, str]:
    """Return {email: sub} for load-test accounts that currently exist."""
    subs = {}
    for email in LOADTEST_EMAILS:
        try:
            attrs = cognito.admin_get_user(UserPoolId=pool_id, Username=email)["UserAttributes"]
            subs[email] = next(a["Value"] for a in attrs if a["Name"] == "sub")
        except ClientError as err:
            if err.response["Error"]["Code"] != "UserNotFoundException":
                raise
    return subs


# ------------------------------------------------------------------------ documents


def build_document(i: int, users: dict[str, dict]) -> tuple[dict, bytes]:
    admins = [e for e, u in users.items() if u["group"] == "ADMIN"]
    recipients = [e for e, u in users.items() if u["group"] == "USER"]
    uploader = random.choice(admins)
    recipient = users[random.choice(recipients)]

    title = f"{TITLE_PREFIX} {random.choice(TITLES)} #{i:04d}"
    doc_date = date.today() - timedelta(days=random.randint(0, 365))
    uploaded_at = datetime.now(timezone.utc) - timedelta(
        days=random.randint(0, 120), minutes=random.randint(0, 1440)
    )
    file_name = f"loadtest_{i:04d}.pdf"
    pdf = tiny_pdf(f"Load test document {i:04d}")

    path = random.choice(STATUS_PATHS)
    history = []
    for step, status in enumerate(path):
        history.append({
            "status": status,
            "changed_by": uploader if status in ("RECEIVED", "ASSIGNED") else recipient["sub"],
            "changed_at": (uploaded_at + timedelta(hours=step)).isoformat(),
            "note": "Seeded for load test",
        })

    doc = {
        "title": title,
        "document_date": doc_date.isoformat(),
        "sender": random.choice(SENDERS),
        "receiver": recipient["name"],
        "file_path": f"{S3_KEY_PREFIX}{uuid4().hex}_{file_name}",
        "file_name": file_name,
        "file_type": "application/pdf",
        "file_size": len(pdf),
        "uploaded_by": uploader,
        "uploaded_at": uploaded_at.isoformat(),
        "status": path[-1],
        "assigned_to": [recipient["sub"]],
        "status_history": history,
        "loadtest": True,  # cleanup marker; ignored by the app
    }
    return doc, pdf


def seed(args):
    session, _, _ = get_aws_session()
    cognito, s3, documents, pool_id, bucket = connect(session)

    print(f"\ninfo: Ensuring {len(LOADTEST_USERS)} load-test accounts in {pool_id}")
    users = ensure_users(cognito, pool_id)

    already = documents.count_documents({"loadtest": True})
    start = already + 1
    print(f"\ninfo: {already} seeded documents already exist; adding {args.documents} more")

    built = [build_document(i, users) for i in range(start, start + args.documents)]

    def upload(item):
        doc, pdf = item
        s3.put_object(Bucket=bucket, Key=doc["file_path"], Body=pdf, ContentType="application/pdf")

    print(f"info: Uploading {len(built)} PDFs to s3://{bucket}/{S3_KEY_PREFIX}* ...")
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(upload, built))

    print("info: Inserting documents into MongoDB ...")
    documents.insert_many([doc for doc, _ in built])

    total = documents.count_documents({})
    print(f"\ndone: {documents.count_documents({'loadtest': True})} load-test documents "
          f"({total} total in collection)")
    print(f"      accounts: loadtest01..{len(LOADTEST_USERS):02d}@example.com "
          f"(01-02 ADMIN, rest USER), password from LOADTEST_PASSWORD in this script")


# -------------------------------------------------------------------------- cleanup


def cleanup_query(subs: dict[str, str]) -> dict:
    test_ids = LOADTEST_EMAILS + list(subs.values())
    return {"$or": [
        {"loadtest": True},
        {"uploaded_by": {"$in": test_ids}},
        {"assigned_to": {"$in": test_ids}},
    ]}


def status(args):
    session, _, _ = get_aws_session()
    cognito, _, documents, pool_id, _ = connect(session)
    subs = existing_user_subs(cognito, pool_id)
    print(f"\nload-test accounts present : {len(subs)}/{len(LOADTEST_USERS)}")
    print(f"seeded documents           : {documents.count_documents({'loadtest': True})}")
    print(f"documents cleanup would hit: {documents.count_documents(cleanup_query(subs))}")
    print(f"documents total            : {documents.count_documents({})}")


def cleanup(args):
    session, _, _ = get_aws_session()
    cognito, s3, documents, pool_id, bucket = connect(session)
    subs = existing_user_subs(cognito, pool_id)
    query = cleanup_query(subs)

    targets = list(documents.find(
        query, {"file_path": 1, "assigned_to": 1, "uploaded_by": 1, "loadtest": 1}
    ))
    # A real admin's document that a load-test account was merely added to (alongside
    # real recipients) is kept; everything else matched is test data.
    test_ids = set(subs.values()) | set(LOADTEST_EMAILS)
    to_delete, skipped = [], []
    for d in targets:
        assigned = set(d.get("assigned_to") or [])
        if d.get("loadtest") or d.get("uploaded_by") in test_ids or assigned <= test_ids:
            to_delete.append(d)
        else:
            skipped.append(d)

    print(f"\nAbout to delete {len(to_delete)} documents (+ their S3 files) "
          f"and {len(subs)} Cognito accounts.")
    if skipped:
        print(f"Keeping {len(skipped)} real documents that are also assigned to load-test "
              f"accounts: {[str(d['_id']) for d in skipped]}")
    if not args.yes and input("Type 'delete' to continue: ").strip() != "delete":
        sys.exit("aborted")

    keys = [d["file_path"] for d in to_delete
            if isinstance(d.get("file_path"), str) and "uploads/" in d["file_path"]]
    keys = [k[k.find("uploads/"):] for k in keys]
    for i in range(0, len(keys), 1000):  # delete_objects takes max 1000 keys
        chunk = keys[i:i + 1000]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True})
    print(f" - removed {len(keys)} S3 objects")

    res = documents.delete_many({"_id": {"$in": [d["_id"] for d in to_delete]}})
    print(f" - removed {res.deleted_count} Mongo documents")

    for email in subs:
        cognito.admin_delete_user(UserPoolId=pool_id, Username=email)
    print(f" - removed {len(subs)} Cognito accounts")
    print(f"\ndone: {documents.count_documents({})} documents remain")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_seed = sub.add_parser("seed", help="create load-test accounts and documents")
    p_seed.add_argument("--documents", type=int, default=500, help="how many documents to add (default 500)")
    sub.add_parser("status", help="count load-test data without changing anything")
    p_clean = sub.add_parser("cleanup", help="delete all load-test data")
    p_clean.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()
    {"seed": seed, "status": status, "cleanup": cleanup}[args.command](args)


if __name__ == "__main__":
    main()
