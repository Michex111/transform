import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Skeleton } from "@/components/ui";
import { McpConsentOutcomeDialog } from "@/components/developer/McpConsentOutcomeDialog";
import { McpConsentScreen } from "@/components/developer/McpConsentScreen";
import { denialUrl, type ConsentOutcome } from "@/lib/mcpConsent";
import type { ConsentConfinementPayload } from "@/lib/mcpConsentRequest";
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

  async function approve(confinement: ConsentConfinementPayload) {
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
        // The confinement is spread last so a stray key in it can never be
        // overridden by, or override, the request identity fields above.
        ...confinement,
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
    <>
      <McpConsentScreen
        request={request}
        selectedScopes={selected}
        onToggleScope={toggle}
        submitting={submitting}
        onApprove={approve}
        onDeny={deny}
      />

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
    </>
  );
}
