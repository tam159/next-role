import { toAgentAccess } from "@/app/hooks/useAgentAvailability";

describe("toAgentAccess", () => {
  it("marks an agent the deployment does not register as not deployed", () => {
    // Production registers only the career agent.
    expect(toAgentAccess({ agents: [{ graph_id: "career_agent", allowed: true }] })).toEqual({
      career_agent: "allowed",
      analytics_agent: "not_deployed",
    });
  });

  it("marks a registered agent the caller may not run as admin only", () => {
    expect(
      toAgentAccess({
        agents: [
          { graph_id: "career_agent", allowed: true },
          { graph_id: "analytics_agent", allowed: false },
        ],
      })
    ).toEqual({ career_agent: "allowed", analytics_agent: "admin_only" });
  });

  it("ignores graphs the picker does not offer", () => {
    expect(
      toAgentAccess({
        agents: [
          { graph_id: "career_agent", allowed: true },
          { graph_id: "analytics_agent", allowed: true },
          { graph_id: "internal_agent", allowed: true },
        ],
      })
    ).toEqual({ career_agent: "allowed", analytics_agent: "allowed" });
  });
});
