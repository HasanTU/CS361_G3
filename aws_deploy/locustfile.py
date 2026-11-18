"""Locust load test for the deployed e-Mailbox (backend_v2 API).

Simulates the 3 most common user actions (course item 5):
    list   GET  /api/v1/documents          ~40% of requests
    detail GET  /api/v1/documents/<id>     ~40%
    write  ~20%: admins PATCH /api/v1/documents/<id>        (edit sender, JSON)
                 users  PATCH /api/v1/documents/<id>/status (advance workflow)

Follows the class ground rules: test accounts only, ~80% reads / 20% writes,
no uploads, no deletes, and writes only touch [LOADTEST] documents.
`uv run aws_deploy/loadtest_data.py cleanup` removes all of it afterwards.
Max 80 users, 2 minutes per step.

Run (from the project root, with the API URL that deploy_v2.py / monitoring.py prints):
    locust -f aws_deploy/locustfile.py --host https://<api-id>.execute-api.us-east-1.amazonaws.com
then open http://localhost:8089, set users + spawn rate, and press Start.
"""

import itertools
import os
import random
import threading
from datetime import date, timedelta

from locust import HttpUser, between, task

PASSWORD = os.getenv("LOADTEST_PASSWORD", "LoadTest123!")  # same as loadtest_data.py
ADMIN_ACCOUNTS = ["loadtest01@example.com", "loadtest02@example.com"]
USER_ACCOUNTS = [f"loadtest{n:02d}@example.com" for n in range(3, 9)]

# Valid next step for a recipient (see backend_v2/shared/domain/document_status.py).
NEXT_STATUS = {
    "ASSIGNED": "ACKNOWLEDGED",
    "FORWARDED": "ACKNOWLEDGED",
    "ACKNOWLEDGED": "IN_PROGRESS",
    "IN_PROGRESS": "COMPLETED",
}

TEST_TITLE_PREFIX = "[LOADTEST]"  # admins see real documents too; never write to those
SENDERS = ["สำนักงานเลขานุการคณะ", "ฝ่ายวิชาการ", "งานทะเบียนและประเมินผล", "งานการเงินและพัสดุ"]

_lock = threading.Lock()
_admin_cycle = itertools.cycle(ADMIN_ACCOUNTS)
_user_cycle = itertools.cycle(USER_ACCOUNTS)


class _ApiUser(HttpUser):
    abstract = True
    wait_time = between(1, 3)  # "think time" between actions, like a real person
    account_cycle = None

    def on_start(self):
        with _lock:
            self.email = next(self.account_cycle)
        self.docs: dict[str, str] = {}  # id -> status, from the last list call
        self.test_doc_ids: list[str] = []  # ids of [LOADTEST] documents (safe to write)
        self.login()
        self.list_documents()

    # ----------------------------------------------------------------- helpers

    def login(self):
        with self.client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": PASSWORD},
            name="POST /auth/login",
            catch_response=True,
        ) as r:
            if r.status_code == 200 and r.json().get("token"):
                self.client.headers["Authorization"] = f"Bearer {r.json()['token']}"
            else:
                r.failure(f"login failed for {self.email}: {r.status_code} {r.text[:120]}")

    def _relogin_if_expired(self, r) -> bool:
        if r.status_code == 401:
            r.failure("401, token expired or invalid, logging in again")
            self.login()
            return True
        return False

    # ------------------------------------------------------------ read tasks

    @task(4)
    def list_documents(self):
        with self.client.get("/api/v1/documents", name="GET /documents (list)", catch_response=True) as r:
            if self._relogin_if_expired(r):
                return
            if r.status_code == 200:
                documents = [d for d in r.json().get("documents", []) if d.get("id")]
                self.docs = {d["id"]: d.get("status", "") for d in documents}
                self.test_doc_ids = [
                    d["id"] for d in documents if str(d.get("title", "")).startswith(TEST_TITLE_PREFIX)
                ]
            else:
                r.failure(f"{r.status_code} {r.text[:120]}")

    @task(4)
    def view_document(self):
        if not self.docs:
            return self.list_documents()
        doc_id = random.choice(list(self.docs))
        with self.client.get(
            f"/api/v1/documents/{doc_id}", name="GET /documents/[id] (detail)", catch_response=True
        ) as r:
            if self._relogin_if_expired(r):
                return
            if r.status_code == 404:
                self.docs.pop(doc_id, None)  # deleted meanwhile; not a server problem
                r.success()
            elif r.status_code != 200:
                r.failure(f"{r.status_code} {r.text[:120]}")


class AdminUser(_ApiUser):
    """Staff: sees every document and edits the details of test documents."""

    weight = 1
    account_cycle = _admin_cycle

    @task(2)
    def edit_document(self):
        if not self.test_doc_ids:
            return self.list_documents()
        doc_id = random.choice(self.test_doc_ids)
        day = date.today() - timedelta(days=random.randint(0, 30))
        with self.client.patch(
            f"/api/v1/documents/{doc_id}",
            json={"sender": random.choice(SENDERS), "document_date": day.isoformat()},
            name="PATCH /documents/[id] (write)",
            catch_response=True,
        ) as r:
            if self._relogin_if_expired(r):
                return
            if r.status_code != 200:
                r.failure(f"{r.status_code} {r.text[:120]}")


class NormalUser(_ApiUser):
    """Lecturer: sees assigned documents and moves them through the workflow."""

    weight = 3
    account_cycle = _user_cycle

    @task(2)
    def update_status(self):
        candidates = [i for i, s in self.docs.items() if s in NEXT_STATUS]
        if not candidates:
            return self.list_documents()  # nothing left to advance; refresh instead
        doc_id = random.choice(candidates)
        new_status = NEXT_STATUS[self.docs[doc_id]]
        with self.client.patch(
            f"/api/v1/documents/{doc_id}/status",
            json={"status": new_status, "note": "Locust"},
            name="PATCH /documents/[id]/status (write)",
            catch_response=True,
        ) as r:
            if self._relogin_if_expired(r):
                return
            if r.status_code == 200:
                self.docs[doc_id] = new_status
            elif r.status_code == 400 and "transition" in r.text.lower():
                # Another simulated user on the same account advanced it first.
                self.docs.pop(doc_id, None)
                r.success()
            else:
                r.failure(f"{r.status_code} {r.text[:120]}")
