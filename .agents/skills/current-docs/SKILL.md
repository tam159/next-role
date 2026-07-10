---
name: current-docs
description: Fetch current documentation before answering library, framework, SDK, API, CLI, cloud-service, version-migration, setup, or configuration questions. Use when the user asks about external technical docs, package APIs, framework behavior, or dependency upgrades; do not use for pure repo refactors, business-logic debugging, or general programming concepts.
---

# Current Docs

Use the repo's current-documentation workflow, adapted from `.cursor/rules/context7.mdc`.

## Workflow

1. Use Context7 for general current library, framework, SDK, API, CLI, and cloud docs.
2. Start with `resolve-library-id` unless the user provides an exact `/org/project` Context7 ID.
3. Choose the best library match by exact name, relevance, snippet count, source reputation, and benchmark score.
4. Query docs with the selected library ID and the user's actual question.
5. Query again with a narrower topic or research/deeper mode when available if the first result is thin.
6. Answer from the fetched docs and cite the tool/source used.

## Repo-Specific Docs

- For LangChain, LangGraph, or LangSmith tasks, use `docs-langchain` in addition to Context7.
- For LlamaIndex, LlamaCloud, LlamaParse, LlamaExtract, LlamaSplit, or LlamaClassify tasks, use `llama-index-docs` in addition to Context7.
- Prefer MCP docs over web search for technical documentation unless the needed MCP server is unavailable or the docs are incomplete.

## Boundaries

Do not use this skill for ordinary refactors, writing local scripts from scratch, reviewing local code, or debugging business logic when no external API/library behavior is in question.
