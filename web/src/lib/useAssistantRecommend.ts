// Shared state for "Suggest with AI" on the format pickers.
//
// Both the library file dialog and the Convert page offer the same affordance
// over different inputs (`file_id` vs. a source format), so the request, the
// loading state, and the honest failure state live here once instead of twice.

import { useCallback, useState } from "react";
import { useAuth } from "@/auth/AuthContext";
import type { AssistantRecommendation, AssistantRecommendRequest } from "@/api/types";

export type RecommendState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; recommendations: AssistantRecommendation[] }
  /** The call failed; the manual picker underneath is the fallback. */
  | { status: "failed" };

export function useAssistantRecommend() {
  const { api: client } = useAuth();
  const [state, setState] = useState<RecommendState>({ status: "idle" });

  const reset = useCallback(() => setState({ status: "idle" }), []);

  const recommend = useCallback(
    async (body: AssistantRecommendRequest) => {
      setState({ status: "loading" });
      try {
        const res = await client.assistantRecommend(body);
        setState({ status: "ready", recommendations: res.recommendations });
      } catch {
        setState({ status: "failed" });
      }
    },
    [client],
  );

  return { state, recommend, reset };
}
