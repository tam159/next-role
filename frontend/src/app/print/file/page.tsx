"use client";

import React, { useEffect, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import { MarkdownContent } from "@/app/components/MarkdownContent";

export type PrintKind = "markdown" | "code" | "docx";

export type PrintPayload = {
  path: string;
  content: string;
  kind: PrintKind;
  language?: string;
};

export const PRINT_FILE_STORAGE_KEY = "nextrole:print-file";

function parsePayload(raw: string | null): PrintPayload | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<PrintPayload>;
    if (
      typeof parsed?.path === "string" &&
      typeof parsed?.content === "string" &&
      (parsed.kind === "markdown" || parsed.kind === "code" || parsed.kind === "docx")
    ) {
      return parsed as PrintPayload;
    }
    return null;
  } catch {
    return null;
  }
}

function basenameWithoutExtension(path: string): string {
  const base = path.split("/").pop() || path;
  return base.replace(/\.[^.]+$/, "");
}

const PRINT_STYLES = `
@page { margin: 0.75in; size: letter; }

.print-root {
  color-scheme: light;
  background: #ffffff;

  /* App-specific color variables — light theme, mirroring globals.css :root */
  --color-primary: #1f6f68;
  --color-user-message: #174d49;
  --color-user-message-bg: #e6f3ef;
  --color-avatar-bg: #dfe9e6;
  --color-secondary: #365f5b;
  --color-success: #15956d;
  --color-warning: #c47a16;
  --color-error: #d14b4b;
  --color-background: #f5f2eb;
  --color-canvas: #fbfaf6;
  --color-subagent-hover: #e4efeb;
  --color-surface: #ffffff;
  --color-surface-raised: #fffdf8;
  --color-tool-surface: #f6f4ed;
  --color-tool-surface-hover: #efebe1;
  --color-border: #ded8cc;
  --color-border-light: #eee8dc;
  --color-text-primary: #202622;
  --color-text-secondary: #66706b;
  --color-text-tertiary: #8d9690;

  color: var(--color-text-primary);
}

@media print {
  body { background: #ffffff !important; }
  [data-sonner-toaster] { display: none !important; }
  a { color: inherit !important; text-decoration: underline; }
  pre, code { white-space: pre-wrap !important; word-break: break-word !important; }
  pre, blockquote, table { break-inside: avoid; }
  h1, h2, h3 { break-after: avoid; }
  img { max-width: 100% !important; page-break-inside: avoid; }
}
`;

export default function PrintFilePage(): React.JSX.Element {
  const [payload, setPayload] = useState<PrintPayload | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    const raw = sessionStorage.getItem(PRINT_FILE_STORAGE_KEY);
    sessionStorage.removeItem(PRINT_FILE_STORAGE_KEY);
    const parsed = parsePayload(raw);
    if (parsed) {
      setPayload(parsed);
      document.title = basenameWithoutExtension(parsed.path);
    } else {
      setMissing(true);
    }
  }, []);

  useEffect(() => {
    if (!payload) return;
    let cancelled = false;
    const trigger = async () => {
      try {
        await document.fonts?.ready;
      } catch {
        // older browsers without fonts API — proceed anyway
      }
      if (cancelled) return;
      requestAnimationFrame(() => {
        if (!cancelled) window.print();
      });
    };
    const onAfterPrint = () => window.close();
    window.addEventListener("afterprint", onAfterPrint);
    void trigger();
    return () => {
      cancelled = true;
      window.removeEventListener("afterprint", onAfterPrint);
    };
  }, [payload]);

  return (
    <>
      <style>{PRINT_STYLES}</style>
      <div className="print-root mx-auto max-w-[7in] p-6">
        {missing ? (
          <p className="text-sm text-(--color-text-secondary)">
            No file to print. Open a file from the Workspace and click <em>Save as PDF</em>.
          </p>
        ) : payload ? (
          <PrintBody payload={payload} />
        ) : null}
      </div>
    </>
  );
}

function PrintBody({ payload }: { payload: PrintPayload }): React.JSX.Element {
  if (payload.kind === "markdown") {
    return <MarkdownContent content={payload.content} />;
  }
  if (payload.kind === "docx") {
    return (
      <div
        className="prose prose-sm max-w-none text-black"
        // mammoth produces well-formed HTML from a user-provided .docx the user
        // just uploaded. Risk surface = their own document.
        dangerouslySetInnerHTML={{ __html: payload.content }}
      />
    );
  }
  return (
    <SyntaxHighlighter
      language={payload.language || "text"}
      style={oneLight}
      showLineNumbers
      wrapLongLines
      customStyle={{
        margin: 0,
        borderRadius: "0.5rem",
        fontSize: "0.875rem",
        background: "#ffffff",
      }}
      lineProps={{ style: { whiteSpace: "pre-wrap", wordBreak: "break-word" } }}
    >
      {payload.content}
    </SyntaxHighlighter>
  );
}
