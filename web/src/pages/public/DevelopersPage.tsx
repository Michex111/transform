import { Link } from "react-router-dom";
import {
  ArrowRight,
  BracketsCurly,
  CloudArrowUp,
  Key,
  Robot,
  ShieldCheck,
  Sparkle,
} from "@phosphor-icons/react";
import { API_ORIGIN } from "@/api/client";
import { Button } from "@/components/ui";
import { CodeBlock } from "@/components/CodeBlock";
import { Eyebrow, Section, SectionHeading } from "@/components/marketing/Section";
import { SeoHead } from "@/components/SeoHead";
import { Item, Reveal, Stagger } from "@/lib/motion";
import { breadcrumbJsonLd, softwareApplicationJsonLd } from "@/lib/seo";
import { DEFAULT_DESCRIPTION } from "@/lib/siteMeta";

/**
 * The public developer surface.
 *
 * Every claim here is checked against the running API. Anything not yet shipped
 * (the official SDKs) is labelled "Planned" and its samples are marked
 * illustrative — a developer page that invents an install command is worse than
 * one that admits the gap.
 */

const JOB_CREATE_SAMPLE = `curl -X POST ${API_ORIGIN}/api/conversions/jobs \\
  -H "Authorization: Bearer $TRANSFORM_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"file_id": "file_123", "target_format": "pdf"}'`;

const DOWNLOAD_SAMPLE = `curl -X GET ${API_ORIGIN}/api/conversions/jobs/JOB_ID/download \\
  -H "Authorization: Bearer $TRANSFORM_API_KEY"`;

const PY_SDK_SAMPLE = `from transform import Transform

client = Transform(api_key="tr_...")

job = client.files.convert(file_id="file_123", target_format="pdf")
job.wait()
job.save_to_drive()`;

const ENDPOINT_GROUPS = [
  {
    title: "Conversions",
    endpoints: [
      "POST /api/conversions/jobs",
      "GET /api/conversions/jobs/{job_id}",
      "GET /api/conversions/jobs/{job_id}/download",
      "GET /api/conversions/supported",
      "GET /api/conversions/history",
    ],
  },
  {
    title: "Drive & files",
    endpoints: [
      "GET /api/v1/files",
      "POST /api/v1/files/folders",
      "POST /api/v1/files/urls",
      "GET /api/v1/files/{file_id}/download",
      "PATCH /api/v1/files/{file_id}",
      "DELETE /api/v1/files/{file_id}",
    ],
  },
  {
    title: "Account & usage",
    endpoints: [
      "GET /api/v1/credits/balance",
      "GET /api/v1/subscription/status",
      "GET /api/v1/subscription/plans",
      "POST /api/v1/api-keys",
      "GET /api/v1/api-keys",
    ],
  },
];

const CAPABILITIES = [
  {
    icon: CloudArrowUp,
    title: "Upload and convert",
    body: "Create an upload session, push the file, then start a conversion job and poll it to completion.",
  },
  {
    icon: BracketsCurly,
    title: "Manage a Drive",
    body: "List, move, favourite, and download the files and folders a user has stored with Transform.",
  },
  {
    icon: Sparkle,
    title: "Use Transform AI",
    body: "Summarise documents, recommend conversions, and drive the assistant over the same authenticated API.",
  },
  {
    icon: ShieldCheck,
    title: "Scoped credentials",
    body: "Issue API keys that act as the user, and authorise AI agents with explicit document scopes.",
  },
];

export function DevelopersPage() {
  return (
    <div className="format-glyph-field">
      <SeoHead
        meta={{
          title: "Developers — Build with Transform",
          description:
            "Transform's REST API lets you convert files and manage documents programmatically. Authenticate with an API key, list and convert Drive files, and connect AI agents over MCP.",
          path: "/developers",
        }}
        jsonLd={[
          softwareApplicationJsonLd(DEFAULT_DESCRIPTION),
          breadcrumbJsonLd([
            { name: "Home", path: "/" },
            { name: "Developers", path: "/developers" },
          ]),
        ]}
      />

      {/* Hero */}
      <Section className="pb-6">
        <Reveal>
          <Eyebrow>Developers</Eyebrow>
          <h1 className="max-w-3xl font-display text-4xl font-semibold tracking-tight sm:text-5xl">
            Build with Transform.
          </h1>
          <p className="mt-4 max-w-2xl text-lg text-muted">
            Convert and manage documents programmatically. One authenticated API for
            conversions, Drive storage, credits, and the AI assistant — plus MCP so AI
            agents can work with the same documents, with the user's permission.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <a href={`${API_ORIGIN}/docs`} target="_blank" rel="noreferrer">
              <Button size="lg">
                Open API reference <ArrowRight size={18} />
              </Button>
            </a>
            <Link to="/mcp">
              <Button size="lg" variant="secondary">
                <Robot size={18} /> Explore MCP
              </Button>
            </Link>
          </div>
          <p className="mt-4 font-mono text-xs text-muted break-all">
            Base URL <span className="text-on-background">{API_ORIGIN}</span> · OpenAPI at{" "}
            <a
              className="text-link hover:underline"
              href={`${API_ORIGIN}/openapi.json`}
              target="_blank"
              rel="noreferrer"
            >
              /openapi.json
            </a>
          </p>
        </Reveal>
      </Section>

      {/* REST API */}
      <Section>
        <SectionHeading
          eyebrow="REST API"
          title="Convert a file in one request"
          intro="Authenticate with an API key, create a job for a file that is already in the user's Drive, then download the result. Job status is available on the same resource."
        />
        <div className="mt-8 grid gap-6 lg:grid-cols-2">
          <div className="min-w-0 space-y-4">
            <CodeBlock code={JOB_CREATE_SAMPLE} language="bash" label="Create a job" />
            <CodeBlock code={DOWNLOAD_SAMPLE} language="bash" label="Download the result" />
          </div>
          <Stagger className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
            {ENDPOINT_GROUPS.map((group) => (
              <Item key={group.title} as="div">
                <div className="h-full rounded-lg border border-outline bg-surface p-5">
                  <p className="mb-3 font-display text-sm font-semibold">{group.title}</p>
                  <ul className="space-y-1.5">
                    {group.endpoints.map((endpoint) => (
                      <li key={endpoint} className="break-all font-mono text-xs text-muted">
                        {endpoint}
                      </li>
                    ))}
                  </ul>
                </div>
              </Item>
            ))}
          </Stagger>
        </div>
      </Section>

      {/* Authentication */}
      <Section>
        <div className="grid gap-8 lg:grid-cols-2">
          <div>
            <Eyebrow>Authentication</Eyebrow>
            <h2 className="font-display text-2xl font-semibold tracking-tight">
              API keys that act as the user
            </h2>
            <p className="mt-3 text-muted">
              Send your key as a bearer token. Keys can be scoped to a single account, are
              hashed at rest, and are shown only once at creation.
            </p>
            <ul className="mt-5 space-y-3 text-sm text-muted">
              <li className="flex items-start gap-3">
                <Key size={18} weight="duotone" className="mt-0.5 shrink-0 text-primary" aria-hidden />
                <span>
                  Create and revoke keys in{" "}
                  <Link to="/app/settings" className="text-link hover:underline">
                    Settings → API keys
                  </Link>
                  . The plaintext is displayed once.
                </span>
              </li>
              <li className="flex items-start gap-3">
                <ShieldCheck
                  size={18}
                  weight="duotone"
                  className="mt-0.5 shrink-0 text-primary"
                  aria-hidden
                />
                <span>
                  Requests are rate-limited per key and checked against record ownership, so a key
                  only ever reaches its own account's files.
                </span>
              </li>
            </ul>
          </div>
          <CodeBlock
            language="bash"
            label="Authorization header"
            code={`Authorization: Bearer tr_xxxxxxxxxxxxxxxxxxxxxxxx

# or
X-API-Key: tr_xxxxxxxxxxxxxxxxxxxxxxxx`}
          />
        </div>
      </Section>

      {/* Capabilities */}
      <Section>
        <SectionHeading
          eyebrow="What you can build"
          title="The whole platform, over HTTP"
          intro="The API surface is the same one the web app uses — nothing is reserved for internal callers."
        />
        <Stagger className="mt-8 grid gap-4 sm:grid-cols-2">
          {CAPABILITIES.map(({ icon: Icon, title, body }) => (
            <Item key={title} as="div">
              <div className="flex h-full flex-col gap-3 rounded-xl border border-outline bg-surface p-6 transition-colors hover:border-primary/40">
                <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                  <Icon size={20} weight="duotone" aria-hidden />
                </span>
                <h3 className="font-display text-base font-semibold">{title}</h3>
                <p className="text-sm text-muted">{body}</p>
              </div>
            </Item>
          ))}
        </Stagger>
      </Section>

      {/* MCP */}
      <Section>
        <Reveal>
          <div className="flex flex-col items-start gap-4 rounded-2xl border border-outline bg-surface p-8 sm:flex-row sm:items-center sm:justify-between">
            <div className="max-w-xl">
              <Eyebrow>AI apps &amp; agents</Eyebrow>
              <h2 className="font-display text-2xl font-semibold tracking-tight">
                Give AI agents document capabilities
              </h2>
              <p className="mt-2 text-muted">
                Transform runs a remote MCP server so assistants like Claude and other
                MCP-compatible clients can find, convert, and save documents — with explicit,
                revocable permissions.
              </p>
            </div>
            <Link to="/mcp" className="shrink-0">
              <Button size="lg">
                <Robot size={18} /> Explore MCP
              </Button>
            </Link>
          </div>
        </Reveal>
      </Section>

      {/* SDKs — planned, clearly labelled */}
      <Section>
        <SectionHeading
          eyebrow="SDKs"
          title="Official SDKs are on the way"
          intro="There is no installable Transform SDK yet. Until then, any HTTP client works against the API above. The snippet below shows the planned interface and is illustrative only."
        />
        <div className="mt-8 grid gap-6 lg:grid-cols-2">
          <div className="min-w-0 rounded-xl border border-dashed border-outline-strong bg-surface p-6">
            <div className="mb-4 flex items-center justify-between gap-2">
              <p className="font-display text-base font-semibold">Python SDK</p>
              <span className="rounded-full border border-outline-strong px-2.5 py-0.5 text-xs font-medium text-muted">
                Planned
              </span>
            </div>
            <CodeBlock code={PY_SDK_SAMPLE} language="python" label="Illustrative" />
          </div>
          <div className="min-w-0 rounded-xl border border-dashed border-outline-strong bg-surface p-6">
            <div className="mb-4 flex items-center justify-between gap-2">
              <p className="font-display text-base font-semibold">TypeScript SDK</p>
              <span className="rounded-full border border-outline-strong px-2.5 py-0.5 text-xs font-medium text-muted">
                Planned
              </span>
            </div>
            <p className="text-sm text-muted">
              A typed client for Node and the browser is planned alongside the Python SDK. Both
              will be generated from the same OpenAPI schema that already powers the API
              reference, so they cannot drift from the server.
            </p>
          </div>
        </div>
      </Section>

      {/* CTA */}
      <Section>
        <Reveal>
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-outline bg-surface p-10 text-center">
            <h2 className="font-display text-3xl font-semibold">Start building</h2>
            <p className="max-w-md text-muted">
              Create an account, issue an API key, and make your first conversion request in
              minutes.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <a href={`${API_ORIGIN}/docs`} target="_blank" rel="noreferrer">
                <Button size="lg" variant="secondary">
                  Read the API reference <ArrowRight size={18} />
                </Button>
              </a>
              <Link to="/register">
                <Button size="lg">Create a free account</Button>
              </Link>
            </div>
          </div>
        </Reveal>
      </Section>
    </div>
  );
}
