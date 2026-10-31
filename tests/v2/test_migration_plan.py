# Feature: backend-v2-auth-bypass-fix, Property 4
"""Tests for scripts/migrate_assigned_to_sub.py. Fakes only: no network, MongoDB or Cognito."""
import json
import os
import sys
import types

import pytest
from bson import ObjectId
from hypothesis import given, settings, strategies as st

from backend_v2.shared.domain.directory_user import DirectoryUser
from scripts import migrate_assigned_to_sub as mig
from scripts.migrate_assigned_to_sub import (
    LEGACY_ALIAS_TO_EMAIL,
    MigrationPlan,
    PlannedChange,
    UnresolvedDoc,
    legacy_values,
    load_directory_users,
    plan_migration,
    resolve_legacy_identity,
)

ALIAS_EMAILS = sorted(set(LEGACY_ALIAS_TO_EMAIL.values()))
USERS = [
    DirectoryUser(f"00000000-0000-4000-8000-{i:012d}", email, email.split("@")[0].title())
    for i, email in enumerate(ALIAS_EMAILS, start=1)
]
SUB_BY_EMAIL = {u.email: u.sub for u in USERS}


# ---------------------------------------------------------------- fakes

class FakeResult:
    def __init__(self, matched_count):
        self.matched_count = matched_count


class FakeCollection:
    """Records every call; update_one applies simple equality filters to its documents."""

    def __init__(self, docs=None, backup_path=None):
        self.docs = list(docs or [])
        self.calls = []
        self.backup_path = backup_path
        self.backup_seen_at_first_update = None

    def find(self, query=None):
        self.calls.append(("find", query))
        return list(self.docs)

    def update_one(self, flt, update):
        if not any(c[0] == "update_one" for c in self.calls) and self.backup_path:
            self.backup_seen_at_first_update = os.path.exists(self.backup_path)
        self.calls.append(("update_one", flt, update))
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in flt.items()):
                doc.update(update["$set"])
                return FakeResult(1)
        return FakeResult(0)

    def writes(self):
        return [c for c in self.calls if c[0] != "find"]


class FakeCognito:
    def __init__(self, users):
        self.users = users

    def get_paginator(self, name):
        assert name == "list_users"
        outer = self

        class Paginator:
            def paginate(self, UserPoolId):
                half = len(outer.users) // 2
                yield {"Users": outer.users[:half]}
                yield {"Users": outer.users[half:]}

        return Paginator()


def cognito_user(sub, email, name, enabled=True):
    attrs = [{"Name": "email", "Value": email}]
    if sub is not None:
        attrs.append({"Name": "sub", "Value": sub})
    if name is not None:
        attrs.append({"Name": "name", "Value": name})
    return {"Enabled": enabled, "Attributes": attrs}


def doc(oid, assigned_to, receiver="", title="t"):
    return {"_id": oid, "title": title, "receiver": receiver, "assigned_to": assigned_to}


# ---------------------------------------------------------------- unit tests

def test_legacy_values_list_string_and_receiver_fallback():
    assert legacy_values({"assigned_to": [" a ", "", None, "b"]}) == ["a", "b"]
    assert legacy_values({"assigned_to": " a "}) == ["a"]
    assert legacy_values({"assigned_to": [], "receiver": " Bob "}) == ["Bob"]
    assert legacy_values({"receiver": "Bob"}) == ["Bob"]
    assert legacy_values({"assigned_to": 5, "receiver": "Bob"}) == ["Bob"]
    assert legacy_values({"assigned_to": ["a"], "receiver": "Bob"}) == ["a"]
    assert legacy_values({"assigned_to": [], "receiver": "  "}) == []
    assert legacy_values({}) == []


@pytest.mark.parametrize("alias,email", sorted(LEGACY_ALIAS_TO_EMAIL.items()))
def test_every_alias_resolves_to_its_user(alias, email):
    assert resolve_legacy_identity(alias, USERS) == SUB_BY_EMAIL[email]
    assert resolve_legacy_identity(f"  {alias.upper()} ", USERS) == SUB_BY_EMAIL[email]


def test_persona_codes_fold_to_alias_keys():
    assert resolve_legacy_identity("HEAD-1", USERS) == SUB_BY_EMAIL["head1@tu.ac.th"]
    assert resolve_legacy_identity("STAFF-1", USERS) == SUB_BY_EMAIL["staff1@tu.ac.th"]
    assert resolve_legacy_identity("FAC-A", USERS) == SUB_BY_EMAIL["fac_a@tu.ac.th"]
    assert resolve_legacy_identity("FAC-B", USERS) == SUB_BY_EMAIL["fac_b@tu.ac.th"]


def test_resolve_by_sub_email_name_and_unknown():
    a = DirectoryUser("sub-a", "Alice@Example.org", "Alice Smith")
    b = DirectoryUser("sub-b", "b@example.org", "Dup")
    c = DirectoryUser("sub-c", "c@example.org", "dup")
    users = [a, b, c]
    assert resolve_legacy_identity("sub-a", users) == "sub-a"
    assert resolve_legacy_identity("ALICE@example.ORG", users) == "sub-a"
    assert resolve_legacy_identity("alice smith", users) == "sub-a"
    assert resolve_legacy_identity("Dup", users) is None  # ambiguous
    assert resolve_legacy_identity("Nobody", users) is None
    assert resolve_legacy_identity("head1", users) is None  # alias target not in directory


def test_load_directory_users_paginates_filters_and_sorts():
    raw = [
        cognito_user("s2", "b@x.org", "Bravo"),
        cognito_user("s1", "a@x.org", "alpha"),
        cognito_user("s3", "c@x.org", "Gone", enabled=False),
        cognito_user(None, "d@x.org", "NoSub"),
        cognito_user("s5", "e@x.org", None),
    ]
    users = load_directory_users(FakeCognito(raw), "us-east-1_P")
    assert [u.sub for u in users] == ["s1", "s2", "s5"]
    assert users[2].name == "e"


def test_unresolved_documents_are_reported_and_not_changed():
    sub = USERS[0].sub
    records = [
        doc(ObjectId(), [USERS[0].email], title="ok"),
        doc(ObjectId(), [USERS[0].email, "Nobody Known"], title="mixed"),
        doc(ObjectId(), [sub], title="already"),
        doc(ObjectId(), [], receiver="", title="empty"),
    ]
    plan = plan_migration(records, USERS)
    assert plan.total == 4
    assert plan.unchanged == 2
    assert [c.title for c in plan.changes] == ["ok"]
    assert plan.changes[0].new_assigned_to == [sub]
    assert plan.changes[0].old_assigned_to == [USERS[0].email]
    assert plan.unresolved == [UnresolvedDoc(str(records[1]["_id"]), "mixed", ["Nobody Known"])]


def test_receiver_is_used_when_assigned_to_is_empty_and_duplicates_collapse():
    sub = USERS[0].sub
    records = [
        doc(ObjectId(), [], receiver=USERS[0].name),
        doc(ObjectId(), [USERS[0].email, USERS[0].email.upper(), sub]),
    ]
    plan = plan_migration(records, USERS)
    assert [c.new_assigned_to for c in plan.changes] == [[sub], [sub]]


def test_string_assigned_to_becomes_a_list():
    plan = plan_migration([doc(ObjectId(), USERS[0].email)], USERS)
    assert plan.changes[0].new_assigned_to == [USERS[0].sub]
    assert plan.changes[0].old_assigned_to == USERS[0].email


def _patch_main_environment(monkeypatch, collection, users_raw=None, env=None):
    monkeypatch.setattr(mig, "load_env_file", lambda: None)
    for key in ("MONGO_URI", "COGNITO_USER_POOL_ID"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env if env is not None else {
        "MONGO_URI": "mongodb://example.invalid/e_mailbox",
        "COGNITO_USER_POOL_ID": "us-east-1_TestPool1",
    }).items():
        monkeypatch.setenv(key, value)

    class FakeMongoClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_default_database(self):
            return {"documents": collection}

    fake_pymongo = types.ModuleType("pymongo")
    fake_pymongo.MongoClient = FakeMongoClient
    monkeypatch.setitem(sys.modules, "pymongo", fake_pymongo)
    fake_boto3 = types.ModuleType("boto3")
    raw = users_raw if users_raw is not None else [
        cognito_user(u.sub, u.email, u.name) for u in USERS
    ]
    fake_boto3.client = lambda *a, **k: FakeCognito(raw)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)


def test_dry_run_issues_no_writes_and_creates_no_backup(monkeypatch, capsys, tmp_path):
    coll = FakeCollection([doc(ObjectId(), [USERS[0].email], title="one")])
    _patch_main_environment(monkeypatch, coll)
    monkeypatch.chdir(tmp_path)
    before = set(os.listdir("/tmp"))
    code = mig.main([])
    out = capsys.readouterr().out
    assert code == 0
    assert coll.writes() == []
    assert "total=1 unchanged=0 to_update=1 unresolved=0" in out
    assert "dry run" in out
    assert not [f for f in set(os.listdir("/tmp")) - before if f.startswith("assigned_to_backup_")]


def test_unresolved_gives_exit_code_1(monkeypatch, capsys):
    coll = FakeCollection([doc(ObjectId(), ["Nobody Known"])])
    _patch_main_environment(monkeypatch, coll)
    assert mig.main([]) == 1
    assert "UNRESOLVED" in capsys.readouterr().out
    assert coll.writes() == []


@pytest.mark.parametrize("env", [
    {"COGNITO_USER_POOL_ID": "us-east-1_TestPool1"},
    {"MONGO_URI": "mongodb://example.invalid/e_mailbox"},
    {},
])
def test_missing_configuration_exits_2(monkeypatch, capsys, env):
    coll = FakeCollection()
    _patch_main_environment(monkeypatch, coll, env=env)
    assert mig.main([]) == 2
    assert "error:" in capsys.readouterr().err
    assert coll.calls == []


def test_apply_writes_backup_before_first_update_and_guards_on_old_value(monkeypatch, tmp_path, capsys):
    id1, id2 = ObjectId(), ObjectId()
    backup = tmp_path / "backup.json"
    coll = FakeCollection(
        [
            doc(id1, [USERS[0].email], receiver="R1"),
            doc(id2, USERS[1].name, receiver="R2"),
            doc(ObjectId(), ["Nobody Known"]),
        ],
        backup_path=str(backup),
    )
    coll.docs[0]["status_history"] = [{"x": 1}]
    _patch_main_environment(monkeypatch, coll)
    code = mig.main(["--apply", "--backup-file", str(backup)])
    assert code == 1  # one unresolved document
    assert coll.backup_seen_at_first_update is True
    updates = [c for c in coll.calls if c[0] == "update_one"]
    assert [u[1] for u in updates] == [
        {"_id": id1, "assigned_to": [USERS[0].email]},
        {"_id": id2, "assigned_to": USERS[1].name},
    ]
    assert [u[2] for u in updates] == [
        {"$set": {"assigned_to": [USERS[0].sub]}},
        {"$set": {"assigned_to": [USERS[1].sub]}},
    ]
    assert coll.docs[0]["receiver"] == "R1" and coll.docs[0]["status_history"] == [{"x": 1}]
    assert coll.docs[2]["assigned_to"] == ["Nobody Known"]
    assert json.loads(backup.read_text(encoding="utf-8")) == [
        {"_id": str(id1), "assigned_to": [USERS[0].email]},
        {"_id": str(id2), "assigned_to": USERS[1].name},
    ]
    assert "updated=2 not_matched=0" in capsys.readouterr().out


def test_apply_does_not_overwrite_a_concurrent_edit(monkeypatch, tmp_path):
    oid = ObjectId()
    coll = FakeCollection([doc(oid, [USERS[0].email])])
    plan = plan_migration(coll.docs, USERS)
    coll.docs[0]["assigned_to"] = ["someone-else"]  # edited after planning
    matched, missed = mig.apply_changes(coll, plan.changes, str(tmp_path / "b.json"))
    assert (matched, missed) == (0, [str(oid)])
    assert coll.docs[0]["assigned_to"] == ["someone-else"]


def test_apply_refuses_to_overwrite_existing_backup(monkeypatch, tmp_path, capsys):
    backup = tmp_path / "b.json"
    backup.write_text("keep", encoding="utf-8")
    coll = FakeCollection([doc(ObjectId(), [USERS[0].email])])
    _patch_main_environment(monkeypatch, coll)
    assert mig.main(["--apply", "--backup-file", str(backup)]) == 2
    assert backup.read_text(encoding="utf-8") == "keep"
    assert coll.writes() == []


def test_restore_puts_old_values_back(monkeypatch, tmp_path, capsys):
    id1, id2 = ObjectId(), ObjectId()
    original = [doc(id1, ["Prapaporn"]), doc(id2, "head1@tu.ac.th")]
    coll = FakeCollection([dict(d) for d in original])
    _patch_main_environment(monkeypatch, coll)
    backup = tmp_path / "b.json"
    assert mig.main(["--apply", "--backup-file", str(backup)]) == 0
    assert coll.docs[0]["assigned_to"] == [SUB_BY_EMAIL["user1@tu.ac.th"]]
    capsys.readouterr()
    assert mig.main(["--restore", str(backup)]) == 0
    assert [d["assigned_to"] for d in coll.docs] == [d["assigned_to"] for d in original]
    assert "restored=2 not_found=0" in capsys.readouterr().out


def test_restore_and_apply_are_mutually_exclusive(capsys):
    with pytest.raises(SystemExit) as exc:
        mig.build_parser().parse_args(["--apply", "--restore", "x.json"])
    assert exc.value.code == 2


def test_default_backup_path_format():
    path = mig.default_backup_path()
    assert path.startswith("/tmp/assigned_to_backup_") and path.endswith("Z.json")


def test_importing_the_module_does_not_connect():
    # The import at the top of this file already ran; main() is the only entry that connects.
    assert callable(mig.main)
    assert PlannedChange and MigrationPlan


# ---------------------------------------------------------------- Property 4

NAME_POOL = ["Alice Smith", "Bob Jones", "ปกรณ์ ทดสอบ", "Carol", "Dave"]
EMAIL_POOL = ["a@example.org", "B@example.org", "c@example.org", "head1@tu.ac.th", "fac_a@tu.ac.th", "User1@tu.ac.th"]


@st.composite
def scenario(draw):
    emails = draw(st.lists(st.sampled_from(EMAIL_POOL), unique=True, min_size=1, max_size=6))
    subs = draw(st.lists(st.uuids(), unique=True, min_size=len(emails), max_size=len(emails)))
    names = [draw(st.sampled_from(NAME_POOL)) for _ in emails]
    users = [DirectoryUser(str(s), e, n) for s, e, n in zip(subs, emails, names)]

    def value_spec():
        return st.one_of(
            st.tuples(st.just("sub"), st.integers(0, len(users) - 1)),
            st.tuples(st.just("email"), st.integers(0, len(users) - 1), st.sampled_from(["same", "upper", "lower"])),
            st.tuples(st.just("alias"), st.sampled_from(sorted(LEGACY_ALIAS_TO_EMAIL)), st.sampled_from(["same", "upper"])),
            st.tuples(st.just("name"), st.sampled_from(NAME_POOL)),
            st.tuples(st.just("unknown"), st.text(alphabet="xyz", min_size=1, max_size=6)),
        )

    records = []
    for _ in range(draw(st.integers(0, 6))):
        mode = draw(st.sampled_from(["list", "string", "receiver"]))
        count = draw(st.integers(1, 3)) if mode == "list" else 1
        specs = [draw(value_spec()) for _ in range(count)]
        records.append((mode, specs))
    return users, records


def render(spec, users):
    kind = spec[0]
    if kind == "sub":
        return users[spec[1]].sub
    if kind == "email":
        email = users[spec[1]].email
        return {"same": email, "upper": email.upper(), "lower": email.lower()}[spec[2]]
    if kind == "alias":
        return spec[1].upper() if spec[2] == "upper" else spec[1]
    if kind == "name":
        return spec[1]
    return "nobody-" + spec[1]


def expected_sub(spec, users):
    """Independent model of what the value identified before the fix."""
    kind = spec[0]
    if kind == "sub":
        return users[spec[1]].sub
    if kind == "email":
        return users[spec[1]].sub
    if kind == "alias":
        target = LEGACY_ALIAS_TO_EMAIL[spec[1]].casefold()
        hits = [u.sub for u in users if u.email.casefold() == target]
        return hits[0] if hits else None
    if kind == "name":
        hits = [u.sub for u in users if u.name.casefold() == spec[1].casefold()]
        return hits[0] if len(hits) == 1 else None
    return None


def build_record(index, mode, values):
    oid = ObjectId()
    if mode == "list":
        return {"_id": oid, "title": f"d{index}", "receiver": "", "assigned_to": list(values)}
    if mode == "string":
        return {"_id": oid, "title": f"d{index}", "receiver": "", "assigned_to": values[0]}
    return {"_id": oid, "title": f"d{index}", "receiver": values[0], "assigned_to": []}


@settings(max_examples=100, deadline=None)
@given(scenario())
def test_property_4_migration_keeps_access_for_legacy_recipients_only(data):
    users, specs_by_record = data
    records = []
    expected = []
    for i, (mode, specs) in enumerate(specs_by_record):
        values = [render(s, users) for s in specs]
        records.append(build_record(i, mode, values))
        expected.append([expected_sub(s, users) for s in specs])

    plan = plan_migration(records, users)
    assert plan.total == len(records)
    assert plan.unchanged + len(plan.changes) + len(plan.unresolved) == len(records)

    changes = {c.doc_id: c for c in plan.changes}
    unresolved = {u.doc_id: u for u in plan.unresolved}
    for record, subs in zip(records, expected):
        doc_id = str(record["_id"])
        if any(s is None for s in subs):
            assert doc_id in unresolved and doc_id not in changes
            bad = unresolved[doc_id].unresolved_values
            assert len(bad) == sum(1 for s in subs if s is None)
        else:
            assert doc_id not in unresolved
            if doc_id in changes:
                assert changes[doc_id].new_assigned_to == list(dict.fromkeys(subs))
                assert changes[doc_id].old_assigned_to == record["assigned_to"]

    # Apply the plan to copies, then plan again: nothing left to change (idempotence).
    migrated = []
    for record in records:
        copy = dict(record)
        change = changes.get(str(record["_id"]))
        if change:
            copy["assigned_to"] = change.new_assigned_to
        migrated.append(copy)
    for record, copy in zip(records, migrated):
        if str(record["_id"]) in unresolved:
            assert copy == record  # unresolved documents are not changed
    second = plan_migration(migrated, users)
    assert second.changes == []
    assert [u.doc_id for u in second.unresolved] == [u.doc_id for u in plan.unresolved]
