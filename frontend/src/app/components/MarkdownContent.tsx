"use client";

import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { cn } from "@/lib/utils";

interface MarkdownContentProps {
  content: string;
  className?: string;
}

const adaptiveCodeTheme = {
  'code[class*="language-"]': {
    color: "var(--color-text-primary)",
    background: "transparent",
    fontFamily: "var(--font-family-mono)",
    textShadow: "none",
  },
  'pre[class*="language-"]': {
    color: "var(--color-text-primary)",
    background: "var(--color-tool-surface)",
    fontFamily: "var(--font-family-mono)",
    textShadow: "none",
  },
  comment: { color: "var(--color-text-tertiary)" },
  prolog: { color: "var(--color-text-tertiary)" },
  doctype: { color: "var(--color-text-tertiary)" },
  cdata: { color: "var(--color-text-tertiary)" },
  punctuation: { color: "var(--color-text-secondary)" },
  property: { color: "var(--color-primary)" },
  tag: { color: "var(--color-error)" },
  boolean: { color: "var(--color-warning)" },
  number: { color: "var(--color-warning)" },
  constant: { color: "var(--color-warning)" },
  symbol: { color: "var(--color-warning)" },
  selector: { color: "var(--color-success)" },
  attrName: { color: "var(--color-success)" },
  string: { color: "var(--color-success)" },
  char: { color: "var(--color-success)" },
  builtin: { color: "var(--color-success)" },
  inserted: { color: "var(--color-success)" },
  operator: { color: "var(--color-primary)" },
  entity: { color: "var(--color-primary)" },
  url: { color: "var(--color-primary)" },
  variable: { color: "var(--color-text-primary)" },
  atrule: { color: "var(--color-primary)" },
  attrValue: { color: "var(--color-success)" },
  function: { color: "var(--color-primary)" },
  className: { color: "var(--color-warning)" },
  keyword: { color: "var(--color-primary)" },
  regex: { color: "var(--color-warning)" },
  important: { color: "var(--color-error)", fontWeight: "bold" },
  deleted: { color: "var(--color-error)" },
} as const;

export const MarkdownContent = React.memo<MarkdownContentProps>(({ content, className = "" }) => {
  return (
    <div
      className={cn(
        "prose min-w-0 max-w-full overflow-hidden break-words text-sm leading-relaxed text-inherit [&_h1:first-child]:mt-0 [&_h1]:mb-4 [&_h1]:mt-6 [&_h1]:font-semibold [&_h2:first-child]:mt-0 [&_h2]:mb-4 [&_h2]:mt-6 [&_h2]:font-semibold [&_h3:first-child]:mt-0 [&_h3]:mb-4 [&_h3]:mt-6 [&_h3]:font-semibold [&_h4:first-child]:mt-0 [&_h4]:mb-4 [&_h4]:mt-6 [&_h4]:font-semibold [&_h5:first-child]:mt-0 [&_h5]:mb-4 [&_h5]:mt-6 [&_h5]:font-semibold [&_h6:first-child]:mt-0 [&_h6]:mb-4 [&_h6]:mt-6 [&_h6]:font-semibold [&_p:last-child]:mb-0 [&_p]:mb-4",
        className
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({
            inline,
            className,
            children,
            ...props
          }: {
            inline?: boolean;
            className?: string;
            children?: React.ReactNode;
          }) {
            const match = /language-(\w+)/.exec(className || "");
            return !inline && match ? (
              <SyntaxHighlighter
                style={adaptiveCodeTheme}
                language={match[1]}
                PreTag="div"
                className="max-w-full rounded-xl border border-border text-sm"
                wrapLines={true}
                wrapLongLines={true}
                lineProps={{
                  style: {
                    wordBreak: "break-all",
                    whiteSpace: "pre-wrap",
                    overflowWrap: "break-word",
                  },
                }}
                customStyle={{
                  margin: 0,
                  maxWidth: "100%",
                  overflowX: "auto",
                  fontSize: "0.875rem",
                  borderRadius: "0.75rem",
                  padding: "1rem",
                }}
              >
                {String(children).replace(/\n$/, "")}
              </SyntaxHighlighter>
            ) : (
              <code
                className="rounded-md border border-border bg-tool-surface px-1.5 py-0.5 font-mono text-[0.9em] text-foreground"
                {...props}
              >
                {children}
              </code>
            );
          },
          pre({ children }: { children?: React.ReactNode }) {
            return <div className="my-4 max-w-full overflow-hidden last:mb-0">{children}</div>;
          },
          a({ href, children }: { href?: string; children?: React.ReactNode }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-primary no-underline underline-offset-4 hover:underline"
              >
                {children}
              </a>
            );
          },
          blockquote({ children }: { children?: React.ReactNode }) {
            return (
              <blockquote className="border-primary/30 my-4 rounded-r-xl border-l-4 bg-accent/40 py-2 pl-4 pr-3 text-muted-foreground">
                {children}
              </blockquote>
            );
          },
          ul({ children }: { children?: React.ReactNode }) {
            return <ul className="my-4 pl-6 [&>li:last-child]:mb-0 [&>li]:mb-1">{children}</ul>;
          },
          ol({ children }: { children?: React.ReactNode }) {
            return <ol className="my-4 pl-6 [&>li:last-child]:mb-0 [&>li]:mb-1">{children}</ol>;
          },
          table({ children }: { children?: React.ReactNode }) {
            return (
              <div className="my-4 overflow-x-auto">
                <table className="w-full border-separate border-spacing-0 overflow-hidden rounded-xl border border-border text-sm [&_td]:border-b [&_td]:border-border [&_td]:p-2 [&_th]:border-b [&_th]:border-border [&_th]:bg-tool-surface [&_th]:p-2 [&_th]:text-left [&_th]:font-semibold [&_tr:last-child_td]:border-b-0">
                  {children}
                </table>
              </div>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

MarkdownContent.displayName = "MarkdownContent";
