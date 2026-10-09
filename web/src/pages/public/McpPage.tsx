import { Link } from "react-router-dom";
import {
  ArrowDown,
  ArrowRight,
  CheckCircle,
  Files,
  FolderOpen,
  MagnifyingGlass,
  PlugsConnected,
  Robot,
  ShieldCheck,
  Sparkle,
  Trash,
  User,
  Warning,
} from "@phosphor-icons/react";
import { API_ORIGIN } from "@/api/client";
import { Button } from "@/components/ui";
import { CodeBlock } from "@/components/CodeBlock";
import { Eyebrow, Section, SectionHeading } from "@/components/marketing/Section";
import { SeoHead } from "@/components/SeoHead";
import { MCP_AGENT_PROMPT } from "@/lib/mcpAgentPrompt";
import { Item, Reveal, Stagger } from "@/lib/motion";
import { breadcrumbJsonLd, faqJsonLd } from "@/lib/seo";

/**
 * The public MCP landing page.
 *
 * Grounded in the shipped implementation: a stateless Streamable-HTTP MCP
 * server at `<api>/mcp`, seven tools, four document scopes, and an OAuth 2.1
 * consent screen the user approves in the app. Scope names, tool names, and the
 * revocation behaviour are copied from the server, not invented.
 */

const MCP_TOOLS = [
  { name: "get_supported_conversions", scope: "read", kind: "read" },
  { name: "list_files", scope: "read", kind: "read" },
  { name: "get_file", scope: "read", kind: "read" },
  { name: "get_conversion_status", scope: "read", kind: "read" },
  { name: "convert_file", scope: "documents.convert", kind: "convert" },
  { name: "save_file", scope: "documents.write", kind: "write" },
  { name: "delete_file", scope: "documents.delete", kind: "destructive" },
];

const SCOPES = [
  {
    scope: "documents.read",
    label: "Read documents",
    body: "Find files in your Drive and read their metadata.",
    icon: MagnifyingGlass,
  },
  {
    scope: "documents.convert",
    label: "Convert documents",
    body: "Start conversions on files you can already read.",
    icon: Sparkle,
  },
  {
    scope: "documents.write",
    label: "Save documents",
    body: "Create and save converted files back to your Drive.",
    icon: FolderOpen,
  },
  {
    scope: "documents.delete",
    label: "Delete documents",
    body: "Remove files. Never granted by default — you must opt in.",
    icon: Trash,
  },
];

const WORKFLOW = [
  { actor: "You", text: "\"Find the latest version of my resume and convert it to PDF.\"" },
  { actor: "Agent", text: "Calls list_files in your Transform Drive." },
  { actor: "MCP", text: "Transform finds resume_v3.docx and reports it back." },
  { actor: "Agent", text: "Calls convert_file with target_format: pdf." },
  { actor: "MCP", text: "Transform converts it and saves the PDF." },
  { actor: "Agent", text: "\"Done — resume_v3.pdf is in your Drive.\"" },
];

const FAQ = [
  {
    question: "What is MCP?",
    answer:
      "The Model Context Protocol is an open standard that lets AI applications call tools on a remote service. Transform exposes document tools over MCP so an assistant can act on your files.",
  },
  {
    question: "Does an agent get access to all my documents automatically?",
    answer:
      "No. When you connect a client you approve a specific set of scopes. Delete access is never granted by default and must be selected explicitly.",
  },
  {
    question: "How do I revoke access?",
    answer:
      "Open Settings → AI apps and revoke the connection. Revocation takes effect on the agent's next request — there is no expiry window to wait out.",
  },
  {
    question: "Where do I connect a client?",
    answer:
      "Point an MCP-compatible client at the Transform MCP endpoint. When it asks to authorise, Transform shows a consent screen listing exactly which scopes it is requesting.",
  },
];

export function McpPage() {
  return (
    <div className="format-glyph-field">
      <SeoHead
        meta={{
          title: "MCP — AI agents for your documents",
          description:
            "Connect Transform to MCP-compatible AI agents so they can securely find, convert, and save documents with user-authorised, revocable permissions.",
          path: "/mcp",
        }}
        jsonLd={[
          faqJsonLd(FAQ),
          breadcrumbJsonLd([
            { name: "Home", path: "/" },
            { name: "Developers", path: "/developers" },
            { name: "MCP", path: "/mcp" },
          ]),
        ]}
      />

      {/* Hero */}
      <Section className="pb-6">
        <Reveal>
          <Eyebrow>MCP</Eyebrow>
          <h1 className="max-w-3xl font-display text-4xl font-semibold tracking-tight sm:text-5xl">
            Give AI agents document capabilities.
          </h1>
          <p className="mt-4 max-w-2xl text-lg text-muted">
            Connect Transform to MCP-compatible agents so they can securely find, convert, and
            store documents — with user authorisation, explicit scopes, and one-click revocation.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Link to="/register">
              <Button size="lg">
                Get started <ArrowRight size={18} />
              </Button>
            </Link>
            <Link to="/developers">
              <Button size="lg" variant="secondary">
                Developer docs
              </Button>
            </Link>
          </div>
          <p className="mt-4 font-mono text-xs text-muted break-all">
            MCP endpoint <span className="text-on-background">{API_ORIGIN}/mcp</span> ·
            Streamable HTTP · OAuth 2.1
          </p>
        </Reveal>
      </Section>

      {/* Flow diagram */}
      <Section>
        <Reveal>
          <div className="rounded-2xl border border-outline bg-surface p-8">
            <div className="mx-auto flex max-w-md flex-col items-center gap-2 text-center">
              <FlowNode icon={User} label="You" sub="Ask in natural language" />
              <ArrowDown size={18} className="text-muted" aria-hidden />
              <FlowNode icon={Robot} label="AI agent" sub="Plans the steps" />
              <ArrowDown size={18} className="text-muted" aria-hidden />
              <FlowNode icon={PlugsConnected} label="MCP" sub="Authorised tool calls" />
              <ArrowDown size={18} className="text-muted" aria-hidden />
              <div className="w-full rounded-xl border border-primary/40 bg-primary-container/40 p-5">
                <p className="font-display text-sm font-semibold text-on-primary-container">
                  Transform
                </p>
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-muted sm:grid-cols-4">
                  {[
                    { icon: MagnifyingGlass, label: "Find files" },
                    { icon: Sparkle, label: "Convert files" },
                    { icon: FolderOpen, label: "Save files" },
                    { icon: Files, label: "Retrieve docs" },
                  ].map(({ icon: Icon, label }) => (
                    <span key={label} className="flex flex-col items-center gap-1.5">
                      <Icon size={18} weight="duotone" className="text-primary" aria-hidden />
                      {label}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </Reveal>
      </Section>

      {/* Example workflow */}
      <Section>
        <SectionHeading
          eyebrow="Example"
          title="One request, several tool calls"
          intro="The agent decides which tools to use; Transform enforces what it is allowed to do. Only the assistant's summary is shown to you — not the raw protocol."
        />
        <Stagger className="mt-8 space-y-3">
          {WORKFLOW.map((step, index) => (
            <Item key={index} as="div">
              <div className="flex items-start gap-3 rounded-lg border border-outline bg-surface p-4">
                <span className="mt-0.5 shrink-0 rounded-md bg-primary-container px-2 py-0.5 font-mono text-xs font-semibold text-on-primary-container">
                  {step.actor}
                </span>
                <p className="text-sm text-muted">{step.text}</p>
              </div>
            </Item>
          ))}
          <Item as="div">
            <p className="flex items-center gap-2 pt-2 text-sm text-success">
              <CheckCircle size={18} weight="fill" aria-hidden /> Located → converted → saved,
              without leaving the conversation.
            </p>
          </Item>
        </Stagger>
      </Section>

      {/* Tools */}
      <Section>
        <SectionHeading
          eyebrow="Tools"
          title="Seven tools, each gated by a scope"
          intro="The server exposes a small, auditable tool surface. A read-only tool cannot change anything, and the destructive tool is separately permissioned."
        />
        <div className="mt-8 overflow-x-auto rounded-lg border border-outline">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-raised text-xs uppercase tracking-wider text-muted">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium">
                  Tool
                </th>
                <th scope="col" className="px-4 py-3 font-medium">
                  Kind
                </th>
                <th scope="col" className="px-4 py-3 font-medium">
                  Required scope
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline">
              {MCP_TOOLS.map((tool) => (
                <tr key={tool.name} className="bg-surface">
                  <td className="px-4 py-3 font-mono text-xs text-on-background">{tool.name}</td>
                  <td className="px-4 py-3">
                    <ToolKindBadge kind={tool.kind} />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-muted">{tool.scope}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* Permissions */}
      <Section>
        <SectionHeading
          eyebrow="Permissions"
          title="You approve exactly what an agent can do"
          intro="Scopes are requested at connection time and shown on a consent screen. Delete access is opt-in and never bundled with the others."
        />
        <Stagger className="mt-8 grid gap-4 sm:grid-cols-2">
          {SCOPES.map(({ scope, label, body, icon: Icon }) => (
            <Item key={scope} as="div">
              <div
                className={`flex h-full flex-col gap-3 rounded-xl border p-6 ${
                  scope === "documents.delete"
                    ? "border-warning/40 bg-warning-container/30"
                    : "border-outline bg-surface"
                }`}
              >
                <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                  <Icon size={20} weight="duotone" aria-hidden />
                </span>
                <h3 className="font-display text-base font-semibold">{label}</h3>
                <p className="text-sm text-muted">{body}</p>
                <span className="mt-auto font-mono text-xs text-muted">{scope}</span>
              </div>
            </Item>
          ))}
        </Stagger>
        <Reveal className="mt-6">
          <p className="flex items-start gap-2 text-sm text-muted">
            <Warning size={18} weight="duotone" className="mt-0.5 shrink-0 text-warning" aria-hidden />
            Connections are visible and revocable at any time from{" "}
            <Link to="/app/settings" className="text-link hover:underline">
              Settings → AI apps
            </Link>
            . Revocation stops the agent on its next request.
          </p>
        </Reveal>
      </Section>

      {/* Connect */}
      <Section>
        <div className="grid gap-8 lg:grid-cols-2">
          <div>
            <Eyebrow>Connect</Eyebrow>
            <h2 className="font-display text-2xl font-semibold tracking-tight">
              Point a client at Transform
            </h2>
            <ol className="mt-5 space-y-4 text-sm text-muted">
              {[
                "Add the Transform MCP endpoint to your MCP-compatible client.",
                "The client opens a Transform consent screen and lists the scopes it wants.",
                "Approve the scopes you are comfortable with and the agent is connected.",
              ].map((step, index) => (
                <li key={step} className="flex gap-3">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-container font-display text-xs font-semibold text-on-primary-container">
                    {index + 1}
                  </span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
            <p className="mt-5 flex items-center gap-2 text-sm text-muted">
              <ShieldCheck size={18} weight="duotone" className="text-primary" aria-hidden />
              Tokens are bound to Transform and resolved on every request, so revoking access is
              immediate.
            </p>
          </div>
          <CodeBlock
            language="json"
            label="MCP endpoint"
            code={`{
  "mcpServers": {
    "transform": {
      "url": "${API_ORIGIN}/mcp"
    }
  }
}`}
          />
        </div>
      </Section>

      {/* Agent prompt */}
      <Section>
        <SectionHeading
          eyebrow="For agents"
          title="Hand your agent the instructions"
          intro="Some clients configure themselves from the endpoint alone. For any that need to be told what they are connecting to, copy this into the agent."
        />
        <div className="mt-8">
          {/* The prompt lives in `lib/mcpAgentPrompt.ts` and
              `docs/mcp-agent-prompt.md` deliberately points back at it rather
              than repeating it, so the instructions a visitor copies cannot
              drift from the ones the repository documents.
              `CodeBlock` supplies the copy control. */}
          <CodeBlock
            language="text"
            label="Agent prompt"
            code={MCP_AGENT_PROMPT}
            // The prompt is ~160 lines; uncapped it is a ~4000px column that
            // buries the rest of the page. Capped and scrollable, with the copy
            // control pinned in the header above it.
            maxHeightClass="max-h-[28rem]"
          />
        </div>
      </Section>

      {/* FAQ */}
      <Section>
        <SectionHeading eyebrow="FAQ" title="Questions about MCP connections" />
        <dl className="mt-8 grid gap-4 sm:grid-cols-2">
          {FAQ.map((item) => (
            <div key={item.question} className="rounded-xl border border-outline bg-surface p-6">
              <dt className="font-display text-base font-semibold">{item.question}</dt>
              <dd className="mt-2 text-sm text-muted">{item.answer}</dd>
            </div>
          ))}
        </dl>
      </Section>

      {/* CTA */}
      <Section>
        <Reveal>
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-outline bg-surface p-10 text-center">
            <h2 className="font-display text-3xl font-semibold">Connect your agent</h2>
            <p className="max-w-md text-muted">
              Create an account, then authorise an MCP client to work with your documents.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link to="/register">
                <Button size="lg">
                  Get started <ArrowRight size={18} />
                </Button>
              </Link>
              <Link to="/developers">
                <Button size="lg" variant="secondary">
                  Developer docs
                </Button>
              </Link>
            </div>
          </div>
        </Reveal>
      </Section>
    </div>
  );
}

function FlowNode({
  icon: Icon,
  label,
  sub,
}: {
  icon: React.ElementType;
  label: string;
  sub: string;
}) {
  return (
    <div className="flex w-full items-center gap-3 rounded-xl border border-outline bg-surface-raised p-4">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-surface-variant text-primary">
        <Icon size={20} weight="duotone" aria-hidden />
      </span>
      <span className="text-left">
        <span className="block font-display text-sm font-semibold">{label}</span>
        <span className="block text-xs text-muted">{sub}</span>
      </span>
    </div>
  );
}

function ToolKindBadge({ kind }: { kind: string }) {
  const styles: Record<string, string> = {
    read: "border-outline-strong text-muted",
    convert: "border-primary/40 text-on-primary-container bg-primary-container/40",
    write: "border-success/40 text-on-success-container bg-success-container/40",
    destructive: "border-warning/40 text-on-warning-container bg-warning-container/40",
  };
  return (
    <span
      className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${styles[kind] ?? styles.read}`}
    >
      {kind}
    </span>
  );
}
