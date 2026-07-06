import { render, renderHook } from "@testing-library/react";
import { Client } from "@langchain/langgraph-sdk";
import { ClientProvider, useClient } from "@/providers/ClientProvider";

vi.mock("@langchain/langgraph-sdk", () => ({ Client: vi.fn() }));

const ClientMock = vi.mocked(Client);

let seenClients: unknown[] = [];

function Probe() {
  seenClients.push(useClient());
  return null;
}

beforeEach(() => {
  vi.clearAllMocks();
  seenClients = [];
});

describe("ClientProvider", () => {
  it("constructs the SDK client with the deployment url and api-key headers", () => {
    render(
      <ClientProvider deploymentUrl="http://backend:2024" apiKey="sk-test">
        <Probe />
      </ClientProvider>
    );

    expect(ClientMock).toHaveBeenCalledTimes(1);
    expect(ClientMock).toHaveBeenCalledWith({
      apiUrl: "http://backend:2024",
      defaultHeaders: {
        "Content-Type": "application/json",
        "X-Api-Key": "sk-test",
      },
    });
    expect(seenClients[0]).toBe(ClientMock.mock.instances[0]);
  });

  it("keeps one identity-stable client across rerenders with unchanged props", () => {
    const { rerender } = render(
      <ClientProvider deploymentUrl="http://backend:2024" apiKey="sk-test">
        <Probe />
      </ClientProvider>
    );
    rerender(
      <ClientProvider deploymentUrl="http://backend:2024" apiKey="sk-test">
        <Probe />
      </ClientProvider>
    );

    expect(ClientMock).toHaveBeenCalledTimes(1);
    expect(seenClients.length).toBeGreaterThanOrEqual(2);
    expect(seenClients.at(-1)).toBe(seenClients[0]);
  });

  it("constructs a new client when apiKey changes", () => {
    const { rerender } = render(
      <ClientProvider deploymentUrl="http://backend:2024" apiKey="sk-test">
        <Probe />
      </ClientProvider>
    );
    rerender(
      <ClientProvider deploymentUrl="http://backend:2024" apiKey="sk-other">
        <Probe />
      </ClientProvider>
    );

    expect(ClientMock).toHaveBeenCalledTimes(2);
    expect(ClientMock).toHaveBeenLastCalledWith({
      apiUrl: "http://backend:2024",
      defaultHeaders: {
        "Content-Type": "application/json",
        "X-Api-Key": "sk-other",
      },
    });
    expect(seenClients.at(-1)).not.toBe(seenClients[0]);
  });
});

describe("useClient", () => {
  it("throws its exact error when used outside a ClientProvider", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useClient())).toThrow(
      "useClient must be used within a ClientProvider"
    );
    errSpy.mockRestore();
  });
});
