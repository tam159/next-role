"use client";

import { createContext, useContext, useMemo, ReactNode } from "react";
import { Client } from "@langchain/langgraph-sdk";

interface ClientContextValue {
  client: Client;
}

const ClientContext = createContext<ClientContextValue | null>(null);

interface ClientProviderProps {
  children: ReactNode;
  deploymentUrl: string;
  apiKey: string;
}

export function ClientProvider({ children, deploymentUrl, apiKey }: ClientProviderProps) {
  const client = useMemo(() => {
    return new Client({
      apiUrl: deploymentUrl,
      defaultHeaders: {
        "Content-Type": "application/json",
        "X-Api-Key": apiKey,
      },
      // langgraph-api 0.8.1 rejects stream_mode "tools"; the JS SDK 1.8.9
      // auto-tracks it from internal getter access. Strip it on the way out.
      onRequest: (_url, init) => {
        if (typeof init.body !== "string") return init;
        try {
          const body = JSON.parse(init.body);
          if (!Array.isArray(body?.stream_mode)) return init;
          if (!body.stream_mode.includes("tools")) return init;
          const filtered = body.stream_mode.filter((m: string) => m !== "tools");
          return {
            ...init,
            body: JSON.stringify({ ...body, stream_mode: filtered }),
          };
        } catch {
          return init;
        }
      },
    });
  }, [deploymentUrl, apiKey]);

  const value = useMemo(() => ({ client }), [client]);

  return <ClientContext.Provider value={value}>{children}</ClientContext.Provider>;
}

export function useClient(): Client {
  const context = useContext(ClientContext);

  if (!context) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return context.client;
}
