import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { buildAgentFolderMap } from "@/lib/mcpFolderAccess";

/**
 * The folders an active AI agent may reach, as a `folder_id -> client_name` map.
 *
 * Fail-soft by design: this backs a quiet decoration on the Files page, so any
 * failure — a 404 on a deployment without the endpoint, a network error, a
 * malformed body — yields an empty map and leaves the page exactly as it would
 * be without the feature. Nothing is surfaced to the user.
 *
 * Fetched once per mount: a binding changes only when someone re-consents on
 * another screen, and the Files page is reloaded to see it.
 */
export function useAgentFolderAccess(): Map<string, string> {
  const [agents, setAgents] = useState<Map<string, string>>(() => new Map());

  useEffect(() => {
    let active = true;
    api
      .mcpFolderAccess()
      .then((res) => {
        if (active) setAgents(buildAgentFolderMap(res.folders));
      })
      .catch(() => {
        // Silent by contract: see the doc comment. The page renders unchanged,
        // and no toast is raised for a decoration the user never asked for.
      });
    return () => {
      active = false;
    };
  }, []);

  return agents;
}
