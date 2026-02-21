# 📐 TELEGRAM_MESSAGE_CANONICAL_SPEC.md

Version: 1.0  
Status: Canonical  
Scope: All outbound Telegram messages from RevFirst_Social  
Last Updated: 2026-02-21

⸻

## 1️⃣ Purpose

Define a unified structural standard for all Telegram messages sent by the system to ensure:

- Operational clarity
- Fast visual scanning
- Reduced cognitive load
- Deterministic formatting
- Consistency across modules
- Future compatibility with editorial + emoji workflows

This document governs ALL Telegram output.

⸻

## 2️⃣ Core Design Principles

1. One logical topic per line
2. Blank line between logical sections
3. Maximize scanability
4. No dense paragraphs
5. Minimal but functional emoji usage
6. Deterministic structure
7. No decorative noise
8. IDs displayed in short format (8 chars)
9. Always use UTC for time references
10. Always prioritize operational actions first

⸻

## 3️⃣ Message Categories

All messages must belong to one of the following categories:

1. SYSTEM STATUS
2. EDITORIAL ITEM
3. ACTION CONFIRMATION
4. ALERT / ERROR
5. REPORT

Each category has its own required structure.

⸻

## 4️⃣ SYSTEM STATUS FORMAT

Used for:

- `/status`
- Partial health summaries

Structure:

```text
🔎 SYSTEM STATUS
----------------

Mode:
semi_autonomous

Scheduler:
healthy

Publishing:
enabled

Queue:
Pending Review: 2
Approved Scheduled: 3

Next Window:
16:30 UTC

Coverage:
1.0 days

Risk Level:
LOW
```

Rules:

- Each metric on its own line
- Blank line between sections
- No inline compact formatting
- Always include Mode and Risk Level

⸻

## 5️⃣ EDITORIAL ITEM FORMAT

Used for:

- `/queue`
- Preview messages
- Resent previews after edit/image regeneration

Structure:

```text
📝 POST
ID: `1e741595`

Copy:
<max 300 chars>
...

Imagem:
Sem imagem
OR
https://...

Status:
Pending Review

Ações principais:
/approve 1e741595
/reject 1e741595

Ações avançadas:
/preview 1e741595
/approve_now 1e741595
```

Rules:

- ID must be short (first 8 chars)
- Full UUID still accepted in commands internally
- Copy truncated safely
- Always show status
- Always separate main vs advanced actions
- Blank line between sections
- Blank line between items

⸻

## 6️⃣ ACTION CONFIRMATION FORMAT

Used for:

- Approve
- Reject
- Publish success
- Reschedule
- Regenerate image

Structure:

```text
✅ APPROVED
ID: `1e741595`

Scheduled For:
16:30 UTC

Status:
Approved Scheduled

Next Window:
16:30 UTC
```

Reject example:

```text
❌ REJECTED
ID: `1e741595`

Status:
Rejected

Replacement Draft:
Generated
```

Rules:

- Clear header
- ID always present
- Always show resulting status
- Always show scheduling info if applicable

⸻

## 7️⃣ ALERT / ERROR FORMAT

Used for:

- Rate limit
- Circuit breaker
- Stability containment
- Plan limit block

Structure:

```text
🚨 ALERT
Type:
Rate Limit

Workspace:
revfirst

Action:
Publishing paused

Required:
/override publish
```

Rules:

- Must include explicit action if required
- Never hide risk state
- Never mix alert with other message types

⸻

## 8️⃣ REPORT FORMAT (DAILY REPORT)

Used for:

- Daily Operational Report

Structure:

```text
📊 DAILY OPERATIONAL REPORT
----------------------------

Date:
2026-02-21 (UTC)

Mode:
semi_autonomous

Publishing:
Attempts: 14
Success: 11
Failures: 3
Success Rate: 79%

Editorial Stock:
Pending Review: 2
Approved Scheduled: 3
Next Window: 16:30 UTC
Coverage: 1.0 days

Stability:
Critical: 0
High: 1
Containments: 1

Risk Assessment:
HIGH
```

Rules:

- Separate blocks
- No compressed inline data
- Coverage always included after editorial upgrade

⸻

## 9️⃣ Formatting Rules (Technical)

1. Use plain text (avoid heavy markdown).
2. Only use:
   - backticks for IDs
   - simple separators
3. Avoid multi-line inline code blocks.
4. Avoid nested formatting.
5. Ensure escaping of special characters in copy.
6. Ensure safe truncation of text.
7. No emoji repetition beyond header indicator.

⸻

## 🔟 ID Rules

Display format:

- First 8 characters of UUID

Example:
`1e741595`

Internal behavior:

- Accept full UUID
- Accept short UUID if unique within queue
- If short ID ambiguous → return clarification error

⸻

## 1️⃣1️⃣ Time Rules

- All timestamps displayed in UTC
- Always append “UTC”
- Never mix BRT in system messages
- If local time is needed in future, display below UTC

⸻

## 1️⃣2️⃣ Forbidden Patterns

❌ Dense paragraphs  
❌ Inline multiple actions on same line  
❌ Multiple statuses on one line  
❌ No header  
❌ Overuse of emoji  
❌ Decorative symbols  
❌ Exposing internal errors

⸻

## 1️⃣3️⃣ Future Compatibility

This spec is compatible with:

- Emoji-based reactions
- Editorial stock system
- Scheduled publish windows
- Multi-channel expansion
- Autonomous mode expansion

⸻

## 1️⃣4️⃣ Migration Strategy

Apply in phases:

Phase 1:

- `/queue`
- `/status`

Phase 2:

- `/approve`
- `/reject`
- publish confirmations

Phase 3:

- `/stability`
- daily reporter

Never refactor all at once without tests.

⸻

## 1️⃣5️⃣ Governance

Any new Telegram message must:

1. Be mapped to one of the 5 categories.
2. Follow exact structural template.
3. Be reviewed against this spec before merge.

Deviation requires explicit justification.

⸻

## ✅ End of Canonical Spec
