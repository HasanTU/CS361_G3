# Handover: backend_v2 auth bypass fix

Snapshot: 2026-10-31. Written when the owner handed this work to a teammate.

| Path | Who it's for | Language |
|---|---|---|
| [`human-th/README.md`](human-th/README.md) | Teammates: what happened, what's left, what to watch out for | Thai |
| [`ai-instructions/README.md`](ai-instructions/README.md) | An AI agent continuing the work: rules, exact state, exact next steps | English |
| [`ai-instructions/backlog.md`](ai-instructions/backlog.md) | Possible security and feature work after this fix | English |
| [`ai-instructions/spec/`](ai-instructions/spec/) | Copy of the spec: `bugfix.md` (requirements), `design.md`, `tasks.md`, `exploration-results.md` (test evidence) | English |

## Status at a glance

- The fix is done and committed: `4bda181`, on top of `b52f483`.
- 379 offline tests. The last full run passed.
- Not done: deploy, post-deploy smoke check, migrating stored data. AWS and MongoDB were offline at handover.
- The final verification steps (tasks 3.19 and 4) were not finished as formal steps.

## สรุปสั้น

แก้ช่องโหว่ที่ทำให้ใครก็ปลอม token เป็นแอดมินหรือเป็นผู้ใช้คนอื่นได้ เสร็จแล้วและมี test ครอบคลุม แต่ยังไม่ได้ deploy และยังไม่ได้ย้ายข้อมูลเก่า อ่านต่อที่ [`human-th/README.md`](human-th/README.md)
