// The agent-connection prompt.
//
// This text is *content*, not markup: if a fact in it is wrong, an agent fails
// to connect and the failure looks like a broken server rather than a bad
// instruction. So the assertions here are the things a connection cannot be made
// without — endpoint, discovery paths, PKCE, the public-client method, scope and
// tool names. A well-meaning edit that drops one fails the build instead of
// shipping instructions that cannot work.

import { describe, expect, it } from "vitest";
import { MCP_AGENT_PROMPT, MCP_ENDPOINT } from "@/lib/mcpAgentPrompt";

describe("MCP endpoint constant", () => {
  it("is the production MCP URL", () => {
    expect(MCP_ENDPOINT).toBe("https://transform-api-7b3g.onrender.com/mcp");
  });

  it("has no trailing slash, because it is also the OAuth resource indicator", () => {
    // MCP_RESOURCE_URL is the token audience. A trailing slash here would make
    // the `resource` parameter in the prompt disagree with the server's
    // metadata, and tokens minted for a different resource are rejected.
    expect(MCP_ENDPOINT.endsWith("/")).toBe(false);
    expect(MCP_AGENT_PROMPT).toContain(`resource=${MCP_ENDPOINT}`);
  });
});

describe("agent prompt completeness", () => {
  it("names the endpoint", () => {
    expect(MCP_AGENT_PROMPT).toContain(MCP_ENDPOINT);
  });

  it("points at both OAuth discovery documents", () => {
    expect(MCP_AGENT_PROMPT).toContain("/.well-known/oauth-protected-resource/mcp");
    expect(MCP_AGENT_PROMPT).toContain("/.well-known/oauth-authorization-server/mcp");
  });

  it("names every OAuth endpoint an agent must call", () => {
    for (const path of ["/mcp/register", "/mcp/authorize", "/mcp/token"]) {
      expect(MCP_AGENT_PROMPT).toContain(path);
    }
  });

  it("states the public-client registration shape", () => {
    // Without `token_endpoint_auth_method: "none"` an agent may wait for a client
    // secret that is never issued.
    expect(MCP_AGENT_PROMPT).toContain('"token_endpoint_auth_method": "none"');
    expect(MCP_AGENT_PROMPT).toContain("NO client secret");
  });

  it("requires PKCE S256", () => {
    expect(MCP_AGENT_PROMPT).toContain("code_challenge_method=S256");
  });

  it("lists every scope by its exact wire value", () => {
    for (const scope of [
      "documents.read",
      "documents.convert",
      "documents.write",
      "documents.delete",
    ]) {
      expect(MCP_AGENT_PROMPT).toContain(scope);
    }
  });

  it("lists every tool by its exact name", () => {
    // A hallucinated or renamed tool is an immediate failure at call time.
    for (const tool of [
      "get_supported_conversions",
      "list_files",
      "get_file",
      "get_conversion_status",
      "convert_file",
      "save_file",
      "delete_file",
    ]) {
      expect(MCP_AGENT_PROMPT).toContain(tool);
    }
  });

  it("tells the agent to poll, because conversions are asynchronous", () => {
    expect(MCP_AGENT_PROMPT).toMatch(/poll\s+`?get_conversion_status`?/i);
    expect(MCP_AGENT_PROMPT).toContain("job_id");
  });

  it("warns about the POST redirect, which breaks clients that ignore 307s", () => {
    expect(MCP_AGENT_PROMPT).toContain("307");
    expect(MCP_AGENT_PROMPT).toMatch(/follow redirects on POST/i);
  });

  it("says a human must approve, so an agent does not try to automate consent", () => {
    expect(MCP_AGENT_PROMPT).toMatch(/signed-in human/i);
  });

  it("tells the agent to reuse the refresh token rather than re-authorize", () => {
    expect(MCP_AGENT_PROMPT).toContain("grant_type=refresh_token");
  });

  it("is self-contained: it never assumes access to this repository", () => {
    // The prompt is handed to an agent that has never seen this codebase, so it
    // must not reference internal files or ask it to read one.
    expect(MCP_AGENT_PROMPT).not.toMatch(/see\s+`?src\//i);
    expect(MCP_AGENT_PROMPT).not.toMatch(/this repository/i);
  });
});
