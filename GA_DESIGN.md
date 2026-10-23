# TriageGuard: GA Design (Live Vision)

> Ground your helpdesk in your docs. Reply only when the docs say so. Escalate the rest.

---

## 1. System at a glance

```
 CUSTOMER SIDE                                  TRIAGEGUARD (SaaS)
┌───────────────────┐                  ┌───────────────────────────────────────┐
│ End customers     │                  │ INGRESS                               │
│  email / form     │                  │  Connectors: Zendesk, Freshdesk,      │
│       │           │   ticket event   │  Email, Webhook                       │
│       ▼           │ ───────────────► │          │                            │
│ Helpdesk          │                  │          ▼                            │
│                   │                  │ ┌─────────────────┐   ┌─────────────┐ │
│                   │                  │ │ Queue + Workers │──►│ Core        │ │
│                   │                  │ └─────────────────┘   │ Pipeline    │ │
│                   │                  │                       └──────┬──────┘ │
│                   │                  │ ┌─────────────────┐          │        │
│                   │                  │ │ Knowledge Base  │◄─────────┤        │
│                   │                  │ │ (per tenant)    │          │        │
│                   │                  │ └────────▲────────┘          ▼        │
│                   │                  │          │            ┌─────────────┐ │
│                   │ ◄─────────────── │          │            │ Actions     │ │
│ reply / draft /   │  reply, draft,   │          │            │ reply/draft/│ │
│ "needs human"     │  tag, note       │          │            │ escalate    │ │
└───────────────────┘                  │          │            └─────────────┘ │
                                       │          │                            │
┌───────────────────┐   doc sync       │ ┌────────┴────────┐  ┌──────────────┐ │
│ Company docs      │ ───────────────► │ │ Doc Sync        │  │ Dashboard    │ │
│ help center,      │                  │ └─────────────────┘  │ + API        │ │
│ Notion, PDFs      │                  │                      └──────────────┘ │
└───────────────────┘                  └───────────────────────────────────────┘
```

