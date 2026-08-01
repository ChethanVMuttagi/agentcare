# AgentCare Frontend

The AgentCare web application: a Next.js 16 (App Router) staff/admin UI for
the FastAPI backend in [`../backend`](../backend), covering patient records,
scheduling, documents, human-in-the-loop approvals, the AI Assistant, and
real-time visibility into the multi-agent workflow engine.

See the [repository root README](../README.md) for the overall project
(vision, backend feature set, security policy) and
[`../CONTRIBUTING.md`](../CONTRIBUTING.md) for the contribution workflow.
This document covers the frontend specifically.

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 16 (App Router only — no `pages/` directory) |
| UI library | React 19 |
| Language | TypeScript, `strict: true`, no `any` in the codebase |
| Styling | Tailwind CSS v4 (CSS-first config via `app/globals.css`'s `@theme inline`, no `tailwind.config.js`) |
| Component primitives | Hand-rolled in `components/ui/` — no Radix/shadcn/MUI |
| Data fetching | Plain `fetch` (`lib/api-fetch.ts`) from Server Components/Server Actions — no React Query/SWR |
| Markdown rendering | `react-markdown` + `remark-gfm` (AI Assistant replies only) |
| Icons | `lucide-react` |
| Charts/diagrams | Hand-rolled, dependency-free SVG (`components/ui/bar-chart.tsx`, `trend-chart.tsx`) and CSS (`features/workflows/workflow-graph.tsx`) — no chart/graph library |

`package.json`'s dependency list is intentionally small; before adding a new
one, check whether the existing hand-rolled primitives already cover the
need.

## Architecture: Backend-for-Frontend

The FastAPI backend has **no CORS middleware** — by design, the browser
never talks to it directly. Almost every backend call happens from a Next.js
**Server Component or Server Action**, which runs on the Next.js server and
calls the backend server-to-server (no CORS involved, and the JWT never
reaches the browser).

The one exception is `app/api/backend/[...path]/route.ts`: a thin,
session-authenticated proxy under `/api/backend/*` for the handful of
interactions that genuinely need a client-side `fetch`:
- The AI Assistant chat's typing indicator / streaming-reveal UX
- The practitioner available-times lookup while booking an appointment
- The workflow event Server-Sent Events stream (the browser's native
  `EventSource` can't attach an `Authorization` header itself, so it must go
  through a same-origin, cookie-authenticated route)

The proxy reads the session cookie, attaches the real bearer token
server-side, and relays the request/response as-is — the browser only ever
talks to same-origin `/api/backend/*`, never the backend directly.

### Session model

- On login, the backend-issued JWT is stored in an `httpOnly`, `sameSite:
  lax` cookie (`secure` in production) — see `lib/session.ts`. It is never
  readable from client-side JavaScript.
- A non-sensitive `agentcare_last_org` cookie remembers the last-used
  organization, and `agentcare_role_hint` remembers a UI role hint — both
  are **UI convenience only**; the backend always re-derives the real role
  from the database on every request, never trusts these.
- `lib/require-session.ts` is the standard guard for any Server Component
  page that needs an authenticated session; it redirects to `/login`
  otherwise.

## Directory Structure

```
frontend/
├── app/                        # Next.js App Router
│   ├── layout.tsx, page.tsx, error.tsx, not-found.tsx
│   ├── login/                  # Login page
│   ├── api/backend/[...path]/  # Authenticated proxy — see "Architecture" above
│   └── org/[organizationId]/   # Every org-scoped page lives under here:
│       ├── dashboard/          #   role-aware summary cards
│       ├── patients/           #   list, detail, create
│       ├── appointments/       #   list, detail, book (with availability lookup)
│       ├── documents/          #   list, per-patient upload/download
│       ├── workflows/          #   list, detail (live AI trace: graph + timeline)
│       ├── approvals/          #   list, detail (approve/reject)
│       ├── assistant/          #   AI Assistant chat + live execution panel
│       ├── analytics/          #   organization-wide metrics and charts
│       ├── demo/                #   one-click scenario runner (see below)
│       └── architecture/        #   static system/agent-topology diagrams
├── components/ui/              # Design-system primitives (Button, Card, Badge,
│                                #   Table, Pagination, EmptyState, ErrorState,
│                                #   Markdown, PanZoom, BarChart, TrendChart, ...)
├── components/layout/          # AppShell, Sidebar, Topbar, NotificationCenter
├── features/<domain>/          # One folder per domain (assistant, workflows,
│                                #   patients, appointments, documents, approvals,
│                                #   demo, dashboard, architecture, auth):
│                                #   `actions.ts` (Server Actions), `action-state.ts`
│                                #   (typed `useActionState` shapes), and the
│                                #   feature's client/server components
├── services/                   # One thin file per backend resource — every
│                                #   function wraps `lib/api-fetch.ts`'s `apiFetch`,
│                                #   nothing else talks to the backend directly
├── hooks/                      # `use-workflow-event-stream` (SSE), `use-notifications`
│                                #   (polling), `use-typewriter`/`use-staged-reveal`
│                                #   (client-side reveal animations)
├── lib/                        # `config.ts`, `session.ts`, `require-session.ts`,
│                                #   `api-fetch.ts`, `errors.ts`, `utils.ts`,
│                                #   `duration.ts`, `event-category.ts`, `agent-icons.tsx`
└── types/api.ts                # Hand-maintained TypeScript mirror of the backend's
                                 #   Pydantic schemas — the backend is always the
                                 #   source of truth; this file follows it, never leads
```

## Local Development

Requires **Node.js 22** (matches `.github/workflows/ci.yml`) and a running
backend (see [`../backend`](../backend)'s setup instructions in the root
README).

```bash
cd frontend
npm install
cp .env.local.example .env.local   # both variables already have working
                                    # local-dev defaults — see that file
npm run dev
```

The app runs at `http://localhost:3000`. Log in, then pick or create an
organization to reach the org-scoped pages listed above.

### Environment variables

See [`.env.local.example`](.env.local.example) for the two variables the
frontend reads (`API_BASE_URL`, `SESSION_COOKIE_NAME`) — both already
default to values that work against a locally-running backend, so this file
exists for discoverability and overriding, not because the app requires it
to start.

### Available scripts

```bash
npm run dev      # start the dev server (Turbopack)
npm run build    # production build — also runs in CI
npm run start    # run a production build locally
npm run lint      # ESLint (eslint-config-next)
```

There is no `npm test` script yet — see "Known Limitations" below.

CI (`.github/workflows/ci.yml`) runs `eslint`, `tsc --noEmit`, and
`next build` on every push/PR to `main`; a red build blocks merge.

## The AI Assistant & Live Execution

Two pages are worth understanding together:

- **`org/[organizationId]/assistant`**: a chat interface (`features/assistant/`)
  that calls the backend's single-shot `POST /agent/execute` endpoint via a
  Server Action, then layers a client-side typing indicator and a
  progressive ("streaming-style") text/card reveal on top of the complete
  response — the backend call itself is not token-streamed, so this is
  presentation, not a claim about the wire protocol. See the comments in
  `hooks/use-typewriter.ts` for the full rationale.
- **`org/[organizationId]/workflows/[workflowId]`**: a live trace of any
  workflow run — a graph of the Coordinator/specialist agents (with
  pan/zoom, `components/ui/pan-zoom.tsx`) and a chronological timeline,
  both driven by `hooks/use-workflow-event-stream.ts`'s real Server-Sent
  Events connection to the backend (through the `/api/backend` proxy — see
  "Architecture" above).
- **`org/[organizationId]/demo`**: runs the exact same real backend
  orchestration as the Assistant (no mocked data), then plays back the
  resulting trace with a staged, animated reveal (`hooks/use-staged-reveal.ts`)
  so the already-complete run still reads as "watching the agents work."

## Type Safety & the Backend Contract

`types/api.ts` is a single, hand-maintained file mirroring the backend's
Pydantic request/response schemas. It is kept in sync by hand, not by
codegen — when changing a backend schema, update this file in the same
change. There is currently no automated check (OpenAPI diff, generated
types, or runtime schema validation at the fetch boundary) that would catch
drift between the two; treat any suspicious `undefined` in the UI as a
possible sign this file is out of date.

## Known Limitations

- **No automated frontend tests exist yet** — no unit, component, or
  end-to-end test suite. CI currently verifies lint/typecheck/build only.
  This is a real gap, not an oversight to ignore: treat any nontrivial
  change to `lib/session.ts`, the `/api/backend` proxy, or a Server Action
  as needing careful manual verification until test infrastructure exists.
- `types/api.ts` sync — see above.
- No client-side error reporting/monitoring integration; runtime errors in
  production are only visible via `console.error` in the error boundaries
  (`app/error.tsx`, `app/org/[organizationId]/error.tsx`).
