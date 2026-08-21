# Transform — File Converter: Screen Design Spec

> Paste this entire document into Stitch (or any design AI) as the master prompt.
> Generate **one screen per `### Screen` section**, in order, keeping every screen
> in the same project so they share one design system.

---

## 0. Product & Audience

- **Product**: "Transform" — a file-conversion SaaS (PDF ⇄ DOCX, plus XLSX, images, audio, video).
- **Audience**: developers and business professionals who need dependable, transparent batch conversion.
- **Single job of the app**: move a file from one format to another with zero friction, and *watch it happen* in a live queue.
- **Backend**: FastAPI under `/api` (JWT auth, presigned-URL uploads, job polling + SSE progress, credits/subscription, file library, API keys).

---

## 1. Design System (apply to every screen)

### 1.1 Personality
Dependable, engineered, precise. A **conversion instrument**, not a toy. Dark, high-contrast "precision graphite" theme — deliberately *not* the generic black + acid-green SaaS default.

### 1.2 Color tokens

| Token | Hex | Use |
|---|---|---|
| `background` | `#121417` | App / page background |
| `surface` | `#1B1E23` | Cards, panels, sidebar |
| `surface-variant` | `#202429` | Hover / nested surface |
| `outline` | `#2A2E34` | Hairline borders |
| `outline-variant` | `#343940` | Stronger borders, focus-less dividers |
| `on-background` | `#ECEEF1` | Primary text |
| `on-surface-variant` | `#8A919B` | Muted / secondary text |
| `primary` | `#5A6BFF` | Brand (buttons, active nav, links) |
| `on-primary` | `#0B0D12` | Text on brand fills |
| `primary-container` | `#2A2F55` | Brand tint backgrounds |
| `on-primary-container` | `#D6DCFF` | Text on brand tint |
| `success` | `#22C55E` | COMPLETED, positive |
| `warning` | `#F5A623` | PENDING / queued |
| `error` | `#F43F5E` | FAILED, destructive |

**Format colors** (canonical, used on format chips + morph streams only):

| Format | Hex | Extensions |
|---|---|---|
| PDF | `#FF5A5F` | `.pdf` |
| Word | `#2B7CD3` | `.docx` `.doc` |
| Excel | `#1FA463` | `.xlsx` `.csv` |
| Image | `#E8549C` | `.png` `.jpg` `.webp` `.svg` |
| Audio | `#9B59D0` | `.mp3` `.wav` `.ogg` |
| Video | `#F97316` | `.mp4` `.webm` |
| Text | `#8A919B` | `.txt` `.md` |

### 1.3 Typography

| Role | Family | Weight | Notes |
|---|---|---|---|
| Display | **Space Grotesk** | 600 | Headlines, big numbers, logo |
| Body | **Inter** | 400 / 500 / 600 | All UI copy, forms, tables |
| Data / Code | **JetBrains Mono** | 400 | Extensions, job IDs, byte counts, sizes |

Scale: `display-lg` 48/52 (-1px), `headline-lg` 28/34, `title-lg` 20/26, `body-lg` 16/24, `body-md` 14/20, `label-md` 12/16 (600, +0.5px), `code-sm` 13/18.

### 1.4 Signature — the **Format Morph**
The atomic unit of the brand: **two format chips (source → target) joined by an animated gradient stream** that flows from the source format's color to the target's color. Use it in: the logo mark, the Convert hero, every Queue/History row, and empty states. A `PDF → DOCX` morph = red `#FF5A5F` flowing into blue `#2B7CD3`.

### 1.5 Motion rules
- **Background animation**: landing + convert screens use a slow ambient field of faint format glyphs (PDF, DOCX, XLSX, MP3, PNG) drifting/morphing at ~5% opacity. **Never** a generic radial mesh gradient. Honor `prefers-reduced-motion` (everything becomes static).
- **The morph stream** is the only ornamental motion elsewhere.
- **Status**: PENDING/PROCESSING pulse subtly; COMPLETED static emerald; FAILED static rose.
- **Progress bars** draw a source-color → target-color gradient (not a single accent).
- Stagger card reveals on load; keep micro-interactions subtle (scale/opacity, no layout shift).

### 1.6 Layout
- 12-column grid, 24px gutters. 8px baseline spacing rhythm.
- **App shell**: 256px left sidebar (collapsible to 72px icon rail) + fluid content. Mobile: single column, 16px margins, bottom nav.
- Dense tables: 8px row spacing. Forms/settings: 24–32px spacing.
- Border radius 8px (cards, buttons, inputs).

### 1.7 Copy voice (write ALL labels in this voice)
- Active voice, **sentence case** (never Title Case buttons), plain verbs.
- Name things by what people control: "Files", not "Object storage"; "API keys", not "Credentials".
- A control says exactly what it does: **"Start conversion"** (not "Submit"), **"Save changes"** (not "Update").
- Keep the same verb through a flow: the button "Publish" → toast "Published".
- Errors explain what happened + how to fix it, in the product voice, never vague, never apologizing.
- Empty states are an invitation to act: "Add your first file".

---

## 2. Screen Inventory (12 screens)

Order: Landing, Pricing, Login, Register, Dashboard, Convert, Queue, History, Files, Billing, Settings, Support.

### Screen 1 — Landing (public, `/`)
Desktop marketing homepage.

- **Header**: left = Transform wordmark + Format Morph mark. Right nav: "Pricing", "Sign in" (outline), "Get started" (filled brand).
- **Hero (left-aligned)**:
  - Eyebrow: "File conversion, done right"
  - Headline (Space Grotesk): "Convert anything. Watch it happen."
  - Subcopy (Inter): "Move files between formats with a live queue, real-time progress, and zero friction."
  - CTAs: filled "Start converting" + outline "See pricing".
  - Trust line (JetBrains Mono, muted): "PDF · DOCX · XLSX · images · audio · video".
- **Hero visual (right)**: large live Format Morph (PDF → DOCX) with animated stream + faint drifting format glyphs.
- **Supported formats** section: grid of format chips in canonical colors (label + extension in JetBrains Mono).
- **How it works**: three steps — 1 Add a file, 2 Choose the target, 3 Download the result.
- **Final CTA band**: "Ready to transform?" + filled "Get started".
- **Footer**: minimal columns (Product, Company, Legal) in muted gray.

### Screen 2 — Pricing (public, `/pricing`)
Desktop marketing page. Same header as landing.

- Title centered: eyebrow "Pricing", headline "Simple plans. Real power." subcopy "Start free. Upgrade when the work demands it."
- Four cards (Free, Pro, Pro Plus, Enterprise):
  - **Free** $0/mo — 5 GB, 50 conversions/mo, 10 API calls/mo, community support. CTA outline "Start free".
  - **Pro** $9.99/mo — 50 GB, 500 conversions/mo, 100 API calls/mo, priority support. CTA filled "Upgrade to Pro".
  - **Pro Plus** $24.99/mo — 100 GB, 2000 conversions/mo, 1000 API calls/mo, priority processing, 24/7 support. Badge "Most popular". CTA filled "Upgrade to Pro Plus".
  - **Enterprise** Custom — custom storage, unlimited conversions, unlimited API, dedicated support, SLA, custom integrations. CTA outline "Contact sales".
- Price in Space Grotesk; numbers in JetBrains Mono; checkmarks in brand/green.
- FAQ (3 items): "Can I cancel anytime?", "Do unused credits roll over?", "Is my data encrypted?".
- Cards stagger-reveal on load.

### Screen 3 — Login (public, `/login`)
Centered card on dark background with faint drifting glyphs.

- Transform wordmark + morph mark at top.
- Headline "Welcome back" (Space Grotesk); subcopy "Sign in to keep converting." (Inter).
- Fields (top-aligned permanent labels, outlined, brand `#5A6BFF` focus ring): Email/username; Password with show/hide toggle.
- Filled full-width button "Sign in".
- Muted link "Forgot password?".
- Divider "or".
- Outline full-width button "Create an account".
- Footer link "Back to home".

### Screen 4 — Register (public, `/register`)
Same card treatment as Login.

- Headline "Create your account"; subcopy "Free to start. Convert in minutes."
- Fields: Username, Email, Password (with strength hint), Confirm password.
- Filled full-width button "Create account".
- Terms line: "By continuing you agree to the Terms and Privacy Policy."
- Divider "or".
- Outline button "Sign in".
- Footer link "Back to home".

### Screen 5 — Dashboard (auth, `/app/dashboard`)
App shell + overview.

- **Sidebar** (256px): wordmark + morph mark, then nav (icon + label): Dashboard (active), Convert, Queue, History, Files, Billing, Settings, Support. Active item = brand highlight. Bottom: avatar + username + settings icon, "Log out" text link.
- **Header row**: title "Dashboard" + filled "New conversion" button (→ Convert).
- **Four stat cards** (label + big Space Grotesk number + meta in JetBrains Mono):
  1. Total conversions — 1,284
  2. Success rate — 98.2%
  3. Credits remaining — 342 (with progress bar)
  4. Storage used — 18.4 GB of 50 GB (with progress bar)
- **Recent conversions** section: compact table of last 5 jobs — columns File (name + source→target morph chips), Status badge, Created, download icon button (completed only). Header has "View all" link (→ Queue).

### Screen 6 — Convert (auth, `/app/convert`) — **has an inline Queue section**
App shell (Convert active).

- Title "Convert" + subcopy "Move a file from one format to another."
- **Conversion panel (hero)**:
  1. **Format Morph selector**: two dropdowns ("From" / "To") with animated gradient arrow between them; source chip + target chip render in canonical format colors.
  2. **Drop zone**: large dashed area — "Drop your file here or browse" + upload icon + helper (JetBrains Mono) showing accepted extension + max size.
  3. Filled button **"Start conversion"** + note "You'll see it in the queue below."
- **Queue section (inline, required)**: compact table of in-flight jobs — columns File, From→To chips, Status badge, Progress (source→target gradient bar), Created. One row PROCESSING with pulsing bar, others PENDING. Section header has "View full queue" link (→ Queue).

### Screen 7 — Queue (auth, `/app/queue`) — **sortable**
App shell (Queue active).

- Title "Queue" + subcopy "Every conversion, in order."
- **Toolbar**: a **sort dropdown** (control) with options: Newest first, Oldest first, By status, By format, By size. Plus a live "n active" count in JetBrains Mono.
- **Table** (dense, 8px rows), columns:
  - File (name + source→target morph chips)
  - Status (badge)
  - Progress (gradient bar; COMPLETED = full emerald, FAILED = rose)
  - Created (JetBrains Mono)
  - Actions (download icon for completed; retry icon for failed; nothing for in-flight)
- Rows re-sort live when the dropdown changes. Status badges per §1.5.
- Row-level hover = `surface-variant` background, no layout shift.

### Screen 8 — History (auth, `/app/history`)
App shell (History active).

- Title "History" + subcopy "Everything you've converted."
- **Filter bar**: format filter chips (PDF, Word, Excel, …) + status filter + date range.
- **Table**: File, From→To, Status, Duration (JetBrains Mono), Credits used, Created, Actions (download).
- Completed rows show a "Download" button; failed rows show "Retry" (→ Convert, pre-filled).

### Screen 9 — Files (auth, `/app/files`)
App shell (Files active).

- Title "Files" + subcopy "Your library, in folders."
- **Toolbar**: "New folder" button, breadcrumb path, upload button.
- **Folder list + file list** (two-pane or combined list): folder cards/rows with name + item count; file rows with name, size (JetBrains Mono), type icon, modified, actions (⋮ menu: Download, Move, Delete).
- Empty state: centered illustration + "Add your first file" CTA.

### Screen 10 — Billing (auth, `/app/billing`)
App shell (Billing active).

- Title "Billing".
- **Current plan card**: tier name, status badge (Active / Cancelled), renewal date, "Manage subscription" (→ Pricing/checkout) + "Cancel subscription" (destructive text).
- **Credits card**: current balance + "Buy credits" button + credit pricing table (100/$10, 500/$40, 1000/$70, 5000/$300) + transaction history table (type, amount, reference, date).

### Screen 11 — Settings (auth, `/app/settings`)
App shell (Settings active).

- Title "Settings".
- **Profile section**: username, email, password change fields + "Save changes".
- **API keys section**: list of keys (name, prefix `tr_****`, status, created, last used, expires) + "Create API key" button → modal (name, expiry days, rate limit) → shows the full key **once** with a "Copy" button + "Done".
- **Danger zone**: "Delete account" (destructive).

### Screen 12 — Support (auth, `/app/support`)
App shell (Support active).

- Title "Support".
- Contact options (email, docs link, status page link).
- FAQ accordion (reuse pricing FAQ + "How do I cancel?", "Where is my file stored?", "Is my data encrypted?").
- "Report an issue" form (subject, message) + "Send" button → toast "Message sent".

---

## 3. Button / Navigation Screen Map

Every interactive control and where it goes.

| # | Screen | Control | Type | Target / Behavior |
|---|---|---|---|---|
| 1 | Landing | Get started | Button | → `/register` |
| 1 | Landing | Sign in | Button | → `/login` |
| 1 | Landing | Start converting | Button | → `/register` (or `/app/convert` if authed) |
| 1 | Landing | See pricing | Button | → `/pricing` |
| 1 | Landing | Pricing (nav) | Link | → `/pricing` |
| 1 | Landing | How it works / CTA | n/a | in-page scroll |
| 2 | Pricing | Start free | Button | → `/register` |
| 2 | Pricing | Upgrade to Pro / Pro Plus | Button | → checkout (Stripe) |
| 2 | Pricing | Contact sales | Button | → `/app/support` |
| 3 | Login | Sign in | Button (submit) | → `/app/dashboard` |
| 3 | Login | Create an account | Button | → `/register` |
| 3 | Login | Forgot password? | Link | → `/register` (placeholder) |
| 3 | Login | Back to home | Link | → `/` |
| 4 | Register | Create account | Button (submit) | → `/login` (then `/app/dashboard`) |
| 4 | Register | Sign in | Button | → `/login` |
| 4 | Register | Back to home | Link | → `/` |
| 5 | Dashboard | New conversion | Button | → `/app/convert` |
| 5 | Dashboard | View all | Link | → `/app/queue` |
| 5 | Dashboard | Sidebar nav | Nav | Dashboard/Convert/Queue/History/Files/Billing/Settings/Support |
| 5 | Dashboard | Log out | Link | → `/login` (clear tokens) |
| 5 | Dashboard | Download (row) | Icon button | download completed output |
| 6 | Convert | From dropdown | Dropdown | select source format (canonical color chip) |
| 6 | Convert | To dropdown | Dropdown | select target format (canonical color chip) |
| 6 | Convert | Drop zone / Browse | File input | open file picker |
| 6 | Convert | Start conversion | Button (submit) | create job → upload → push to queue |
| 6 | Convert | View full queue | Link | → `/app/queue` |
| 7 | Queue | Sort dropdown | Dropdown | Newest/Oldest/By status/By format/By size |
| 7 | Queue | Download (row) | Icon button | download completed output |
| 7 | Queue | Retry (row) | Icon button | → `/app/convert` (pre-filled) |
| 8 | History | Filter chips | Chips (multi) | filter by format/status |
| 8 | History | Date range | Dropdown | filter range |
| 8 | History | Download | Button | download output |
| 8 | History | Retry | Button | → `/app/convert` (pre-filled) |
| 9 | Files | New folder | Button | inline create (name input) |
| 9 | Files | Upload | Button | file picker → upload |
| 9 | Files | Breadcrumb | Links | navigate folders |
| 9 | Files | Row ⋮ menu | Dropdown | Download / Move / Delete |
| 10 | Billing | Manage subscription | Button | → `/pricing` |
| 10 | Billing | Cancel subscription | Destructive | confirm modal |
| 10 | Billing | Buy credits | Button | → checkout |
| 11 | Settings | Save changes | Button | persist profile |
| 11 | Settings | Create API key | Button | modal → reveal key → Copy |
| 11 | Settings | Delete account | Destructive | confirm modal |
| 12 | Support | Send | Button | submit form → toast "Message sent" |

**Global nav rules**: public pages share the top header; authed pages share the sidebar shell. Sidebar active item always reflects the current route. Mobile (bottom nav) mirrors the same 8 destinations.

---

## 4. Import UI Rules (apply to every screen)

1. **No emojis as structural icons** — use vector icons (Phosphor `@phosphor-icons/react` preferred; Heroicons fallback), consistent stroke width, one filled-vs-outline style per hierarchy level.
2. **Vector-only assets** — SVG, no raster PNG icons. Crisp at every scale and in dark mode.
3. **Icon semantics by use** — decorative icons beside visible text get `aria-hidden="true"`; standalone icons need a text alternative; icon controls get an accessible name + `selected`/`pressed`/`expanded` state.
4. **Stable interaction states** — press states change color/opacity only; never shift layout or trigger jitter.
5. **Touch targets ≥ 44×44px** (expand hit area for small icons).
6. **Dark-mode contrast** — body text ≥ 4.5:1 on `#121417`/`#1B1E23`; large/display text ≥ 3:1; non-text UI (borders, icon controls) ≥ 3:1. Dividers visible in both themes.
7. **Token-driven theming** — no ad-hoc hex values; use the §1.2 tokens.
8. **8px spacing rhythm** — consistent padding/gap scale (8/16/24/32/48).
9. **Readable measure** — body text max ~65–75ch, never edge-to-edge on wide screens.
10. **Focus management** — visible keyboard focus ring (`#5A6BFF`) on all interactive elements; focus order matches visual order; modals trap focus.
11. **Form UX** — every field has a visible label, hint, and inline error; multi-error forms show a linked error summary; failed submits retain values.
12. **Errors are directional** — state what happened + how to fix; never vague; never apologetic. Map HTTP codes to friendly copy (400 invalid input, 401 session expired → sign in again, 404 not found, 409 conflict, 503 service unavailable → try again).
13. **Empty states are invitations** — action-oriented, centered, with a primary CTA.
14. **Reduced motion** — `prefers-reduced-motion` disables the morph stream, glyph drift, and pulse animations; content remains fully readable.
15. **Color is never the only indicator** — status badges pair color with a text label (Queued / Converting / Ready / Failed) and, for progress, a numeric percent.

---

## 5. Status Badge Map (shared component)

| Backend status | Label (UI) | Color | Motion |
|---|---|---|---|
| `AWAITING_UPLOAD` | "Waiting for file" | muted gray | static |
| `PENDING` | "Queued" | `warning` `#F5A623` | subtle pulse |
| `PROCESSING` | "Converting" | `primary` `#5A6BFF` | pulse + progress bar |
| `COMPLETED` | "Ready" | `success` `#22C55E` | static |
| `FAILED` | "Failed" | `error` `#F43F5E` | static |
