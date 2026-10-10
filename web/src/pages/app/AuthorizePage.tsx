import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { LinkBreak, Robot, ShieldCheck } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Skeleton } from "@/components/ui";
import { McpConsentOutcomeDialog } from "@/components/developer/McpConsentOutcomeDialog";
import { denialUrl, type ConsentOutcome } from "@/lib/mcpConsent";
import type { McpConsentRequestResponse } from "@/api/types";

/** The account's connected-applications section. */
const SETTINGS_URL = "/app/settings?tab=connected-apps";

/**
 * The OAuth consent screen an AI application sends the browser to.
 *
 * WHY this lives in the SPA rather than on the API: the signed-in session is a
 * bearer token held by the SPA, so a top-level navigation to the API could not
 * carry it. The API stays authoritative — this page renders what
 * `GET /v1/mcp/authorize` returns and posts the decision back, and the API
 * re-validates every value (registered client, exact redirect URI, resource,
 * PKCE challenge) before it mints a code. Nothing here is trusted.
 *
 * The scopes are shown for ALL four permissions, not just the requested ones,
 * so a user can see that "delete your files" is *not* being asked for. The
 * destructive scope is never pre-selected.
 */
export function AuthorizePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { error: errorToast } = useToast();

  const clientId = params.get("client_id") ?? "";
  const redirectUri = params.get("redirect_uri") ?? "";
  const scope = params.get("scope") ?? "";
  const codeChallenge = params.get("code_challenge") ?? "";
  const resource = params.get("resource") ?? "";
  const state = params.get("state");

  const [request, setRequest] = useState<McpConsentRequestResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  /** Set once the visitor has decided; the handoff happens from the dialog. */
  const [outcome, setOutcome] = useState<ConsentOutcome | null>(null);

  useEffect(() => {
    if (!clientId || !redirectUri || !codeChallenge) {
      setFailed("This connection link is incomplete. Ask the application to start again.");
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    api
      .mcpConsentRequest({
        client_id: clientId,
        redirect_uri: redirectUri,
        scope,
        resource: resource || undefined,
      })
      .then((res) => {
        if (!active) return;
        setRequest(res);
        // Read and convert are pre-selected. A permission the server marks as
        // destructive (deleting files) is deliberately left unchecked, and the
        // flag comes from the API rather than a scope-name string here, so the
        // rule cannot drift from the domain's list of destructive scopes.
        setSelected(
          new Set(
            res.scopes.filter((s) => s.requested && !s.destructive).map((s) => s.scope),
          ),
        );
      })
      .catch((err) => {
        if (active) {
          setFailed(err instanceof Error ? err.message : "This connection request is not valid.");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [clientId, redirectUri, scope, resource, codeChallenge]);

  function toggle(scopeName: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(scopeName)) next.delete(scopeName);
      else next.add(scopeName);
      return next;
    });
  }

  async function approve() {
    if (!request) return;
    setSubmitting(true);
    try {
      const res = await api.approveMcpConsent({
        client_id: clientId,
        redirect_uri: redirectUri,
        code_challenge: codeChallenge,
        scope,
        resource: resource || null,
        state,
        approved_scopes: [...selected],
      });
      // The server validated this URL against the client's registered redirect
      // URIs before minting the code, and the code is useless without the PKCE
      // verifier the application holds. See `Outcome` for why the handoff waits
      // for the visitor rather than happening here.
      setOutcome({
        kind: "approved",
        clientName: request.client_name,
        redirectUrl: res.redirect_url,
      });
    } catch (err) {
      setSubmitting(false);
      errorToast(err instanceof Error ? err.message : "Could not complete the connection");
    }
  }

  function deny() {
    // Hand the application a refusal instead of stranding the browser here.
    const url = denialUrl(redirectUri, state);
    if (!url) {
      navigate(SETTINGS_URL);
      return;
    }
    setOutcome({
      kind: "denied",
      clientName: request?.client_name ?? "The application",
      redirectUrl: url,
    });
  }

  if (loading) {
    return (
      <Card className="mx-auto max-w-lg p-6">
        <Skeleton className="mb-4 h-6 w-40" />
        <Skeleton className="mb-2 h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </Card>
    );
  }

  if (failed || !request) {
    return (
      <Card className="mx-auto max-w-lg p-6">
        <h1 className="mb-2 font-display text-lg font-semibold">Connection could not be started</h1>
        <p className="mb-4 text-sm text-muted">
          {failed ?? "This connection request is not valid."}
        </p>
        <Button variant="secondary" onClick={() => navigate(SETTINGS_URL)}>
          Back to settings
        </Button>
      </Card>
    );
  }

  return (
    <Card className="mx-auto max-w-lg p-6">
      <div className="mb-1 flex items-center gap-3">
        <Robot size={24} className="text-primary" aria-hidden="true" />
        <h1 className="font-display text-lg font-semibold">
          Allow {request.client_name} to use your account?
        </h1>
      </div>
      <p className="mb-5 text-sm text-muted">
        It is asking for permission to work with your Transform files. You can disconnect it at any
        time from Settings → AI apps, without changing your password.
      </p>

      <ul className="mb-5 divide-y divide-outline">
        {request.scopes.map((s) => {
          const requested = s.requested;
          return (
            <li key={s.scope} className="flex items-start gap-3 py-3">
              <input
                type="checkbox"
                id={`scope-${s.scope}`}
                checked={selected.has(s.scope)}
                disabled={!requested}
                onChange={() => toggle(s.scope)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-primary)] disabled:opacity-40"
              />
              <label
                htmlFor={`scope-${s.scope}`}
                className={requested ? "text-sm" : "text-sm text-muted"}
              >
                <span className="block text-on-background">{s.description}</span>
                <span className="block font-mono text-xs text-muted">
                  {s.scope}
                  {!requested && " · not requested"}
                  {s.already_granted && " · already allowed"}
                </span>
              </label>
            </li>
          );
        })}
      </ul>

      <div className="mb-5 flex items-start gap-2 rounded-lg border border-outline-strong bg-surface-variant p-3">
        <ShieldCheck size={16} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
        <p className="text-xs text-muted">
          It will only ever see your own files. You can also revoke its access later.
        </p>
      </div>

      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={deny} disabled={submitting}>
          Cancel
        </Button>
        <Button onClick={approve} disabled={submitting || selected.size === 0}>
          {submitting ? "Connecting…" : "Allow access"}
        </Button>
      </div>

      <p className="mt-4 flex items-center gap-1 text-xs text-muted">
        <LinkBreak size={12} aria-hidden="true" />
        Sending you to {request.redirect_uri}
      </p>

      {/* The application's browser handoff. Rendered as a confirmation step
          rather than performed on approval — see `McpConsentOutcomeDialog` for
          why the visitor completes it. */}
      {outcome && (
        <McpConsentOutcomeDialog
          outcome={outcome}
          onReturn={() => window.location.assign(outcome.redirectUrl)}
          onManage={() => navigate(SETTINGS_URL)}
        />
      )}
    </Card>
  );
}
