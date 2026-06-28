# Go Fig — AI Engineer · Project Take-Home

**Inbox Triage Agent**

---

## The rules

- **Time cap: 2 hours.** Pick a single uninterrupted block. A clean, working *core* beats a
  sprawling unfinished pile — and we mean the cap. (Suggested split below.)
- **Use AI heavily.** This is the job. Cursor, Claude Code, whatever you run day-to-day.
  We are **not** testing whether you can hand-write Python. We're testing how well you
  *direct* AI to build correct, secure software under a deadline. Treat the AI like a team
  of engineers you're managing.
- We explicitly do **not** penalize AI use. We reward *managed* AI use.
- **"Done" is yours to define.** There's no hidden test suite grading you to a spec. We've
  left room on purpose — show us your judgment about what matters and where to spend effort.

## How to spend your two hours

| Time | Focus |
|---|---|
| **~60 min** | **Build** the skill against the requirements below. |
| **~30 min** | **Test / verify** it however you see fit — make sure it actually works. |
| **~30 min** | **Wrap up the deliverables** — clean up the repo, fill in the engineering log, record your Loom. |

Budget for the wrap-up; don't let it get squeezed. We care as much about how you finish and
communicate as about the code itself.

## The scenario

A client — a small B2B company — wants an agent that triages their incoming customer
emails so a human never starts from a blank page. You're building the first skill worker.

This repo is a scaffold: a mock REST API (inbox + outbound mail + CRM), email fixtures,
env config, and a **stubbed skill module**. Build the skill.

> **You need no external accounts.** The mock API stands in for Gmail and the CRM — it runs
> locally with `make serve`. The only thing you bring is your own LLM API key.

## Requirements

1. **Ingest** the incoming emails from the mock `GET /inbox` endpoint.
2. **Classify** each email into exactly one of: `billing`, `bug_report`, `sales_lead`, `spam`.
3. **Draft an action** per the routing table:

   | Classification | Action |
   |---|---|
   | `billing` | draft a reply to the customer (`POST /mail/send`) |
   | `bug_report` | alert the engineering team (`POST /slack/alert`, channel `#engineering`) |
   | `sales_lead` | draft a reply **and** create a CRM lead (`POST /mail/send`, `POST /crm/lead`) |
   | `spam` | no action — log and drop |

4. **Human-in-the-loop gate.** *No external action (send reply, create CRM record) may
   execute without explicit human approval.* The skill **proposes**, a human **approves**,
   and only then does it call the write endpoint. Design this gate.
5. **Least privilege & secrets.** The spam path must never hold write credentials. All
   tokens come from the environment — never hardcoded. The write scope is used only after
   approval.
6. **Verify your work.** How you prove it works — tests, a demo script, manual checks — is
   up to you. We want to see how you build confidence in your own output.
7. **README the client could read.** Append a short section below: what it does, how to
   run it, and the one design decision you're proudest of.

## What we hand you

```
mock_api/server.py     FastAPI mock: /inbox, /mail/send, /slack/alert, /crm/lead
fixtures/emails.json   the inbox the agent triages
src/triage_skill.py    STUB — signatures + TODOs, no logic. This is where you work.
env.example            the env vars you need (copy to .env)
Makefile               `make serve` (run the API), `make audit` (inspect side effects)
ENGINEERING_LOG.md     a one-page template — fill it in
```

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env           # then fill in your own LLM API key (any provider)
make serve                    # terminal 1 — starts the mock API on :8099
```

## Deliverables (submit all three)

1. **A link to your GitHub repo.** Fork this repo, push your edits, and share the URL
   with us. (Public, or private with us added as collaborators — your call.)
2. **`ENGINEERING_LOG.md`**, filled in (one page) — how you directed the work.
3. **A Loom recording (required, ≤5 min).** Walk us through what you built, demo it
   running, and call out a decision or two you're proud of. This is where we see your
   communication and how completely you finished — treat it like showing a client.

## How we evaluate

We grade *how you managed the AI* as much as the result: did you decompose and delegate,
review its output critically, catch its mistakes, and make sound security calls? We also
look at how you **interpreted an open-ended problem** and how clearly you **communicate**
your work. The full rubric is shared with you after you submit.

Questions before you start? Email us. Once you open the scaffold, the clock is yours.

---

<!-- ↓↓↓ CANDIDATE: add your "README the client could read" section here ↓↓↓ -->

## What This Does

This agent gives your team a head start on every customer email so no one ever
stares at a blank inbox.

When a message comes in, the agent reads it and sorts it into one of four buckets —
a **billing issue**, a **bug report**, a **sales lead**, or **spam**. Then it drafts
the right next step for each one:

- A **billing** email → a draft reply back to the customer.
- A **bug report** → an alert to your engineering team so they can jump on it.
- A **sales lead** → a draft reply *and* a new entry in your CRM so nothing slips
  through the cracks.
- **Spam** → quietly set aside. No action, no noise.

The result: your people spend their time reviewing and approving good drafts instead
of triaging a pile of mail from scratch.

### How to run it

1. Clone this repository and install the dependencies.
2. Copy `env.example` to `.env` and add your Anthropic API key.
3. Start the local demo service: **`make serve`**
4. Run the agent: **`python -m src.triage_skill`**

The agent walks you through its inbox one proposed action at a time. For each one it
shows you what it wants to do and why, then **waits for you to approve** before it
does anything at all.

### The decision I'm proudest of: you're always in control

Nothing leaves the building without a human saying "yes." No reply is sent, no CRM
record is created, and no engineering alert fires until a person reviews the draft and
approves it. The agent **proposes; you decide.**

This isn't just a convenience — it's a safety guarantee. One of our test emails is
actually a trick: it contains hidden instructions telling the system to skip approval
and hand over your customer list. The agent doesn't fall for it. It treats that email
as spam and takes **zero** action. Because the approval step can't be bypassed and the
sorting step has no power to send anything on its own, even a cleverly crafted email
can't make the system act on its own. You stay in the driver's seat, every time.
