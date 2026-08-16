"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isAIMessage, isToolMessage, type BaseMessage } from "@langchain/core/messages";
import type {
  ActionRequest,
  ApprovalDecision,
  PendingApproval,
  ReviewConfig,
} from "@/app/types/types";

// Minimal structural views of the two interrupt sources (see below), so tests
// don't need SDK types.
interface RootInterruptLike {
  id?: string;
  value?: unknown;
}

interface ThreadInterruptLike {
  interruptId: string;
  payload: unknown;
  namespace?: string[];
}

interface UseInterruptApprovalsInput {
  /**
   * `stream.interrupts` — root-namespace interrupts live, and ALL active
   * interrupts after thread hydration (the SDK syncs them from
   * `state.tasks[].interrupts`, which carries no namespace).
   */
  interrupts: RootInterruptLike[];
  /**
   * Accessor for `stream.getThread()?.interrupts` — the ThreadStream record
   * of every `input.requested` seen live at ANY depth. This is the only live
   * source for subagent (subgraph) interrupts; the SDK's own respond()
   * resolution walks the same list.
   */
  getThreadInterrupts: () => ThreadInterruptLike[] | undefined;
  isLoading: boolean;
  messages: BaseMessage[];
  resumeInterrupt: (
    value: unknown,
    target?: { interruptId?: string; namespace?: string[] }
  ) => void;
}

/** Message text for the queued chip while sibling approvals are undecided. */
export function approvalQueueNote(
  approval: PendingApproval,
  queuedDecisions: Map<string, ApprovalDecision>
): string | undefined {
  if (approval.total <= 1) return undefined;
  let decided = 0;
  for (let i = 0; i < approval.total; i += 1) {
    if (queuedDecisions.has(`${approval.interruptId}:${i}`)) decided += 1;
  }
  const remaining = approval.total - decided;
  if (remaining <= 0) return undefined;
  return `Waiting on ${remaining} more ${remaining === 1 ? "decision" : "decisions"}`;
}

function isActionRequest(value: unknown): value is ActionRequest {
  if (!value || typeof value !== "object") return false;
  const request = value as Record<string, unknown>;
  return typeof request.name === "string" && !!request.args && typeof request.args === "object";
}

function parseApprovals(candidate: {
  id: string;
  value: unknown;
  namespace?: string[];
}): PendingApproval[] {
  const value = candidate.value as Record<string, unknown> | null | undefined;
  const requests = Array.isArray(value?.action_requests) ? value.action_requests : [];
  const configs = Array.isArray(value?.review_configs) ? value.review_configs : [];
  const actionRequests = requests.filter(isActionRequest);
  // Non-HITL interrupt payloads (no action_requests) are not ours to render.
  if (actionRequests.length === 0 || actionRequests.length !== requests.length) return [];
  return actionRequests.map((actionRequest, index) => ({
    key: `${candidate.id}:${index}`,
    interruptId: candidate.id,
    namespace: candidate.namespace,
    index,
    total: actionRequests.length,
    actionRequest,
    // review_configs is index-aligned with action_requests (NOT name-keyed —
    // parallel same-name calls each get their own entry).
    reviewConfig: configs[index] as ReviewConfig | undefined,
  }));
}

/**
 * Single owner of HITL approval state for a thread.
 *
 * Derives the pending approvals from both interrupt sources, assigns each to
 * the root tool call it reviews (by id — parallel same-name calls stay
 * distinct), and accumulates per-action decisions so ONE resume carries the
 * full ordered `decisions` array the middleware demands
 * (`len(decisions) == gated call count`, else the backend rejects the resume).
 */
export function useInterruptApprovals({
  interrupts,
  getThreadInterrupts,
  isLoading,
  messages,
  resumeInterrupt,
}: UseInterruptApprovalsInput) {
  // Decisions queued per approval key, and interrupts we've already resumed.
  // Refs mirror the state for synchronous reads in `decide` (state setters
  // are async; a double-click or two quick sibling decisions must not
  // double-submit). State versions drive re-renders.
  const [queuedDecisions, setQueuedDecisions] = useState<Map<string, ApprovalDecision>>(
    () => new Map()
  );
  const queuedDecisionsRef = useRef(queuedDecisions);
  // Interrupt ids this session has resumed. Never pruned: the ThreadStream
  // record can replay a resolved interrupt's `input.requested` long after the
  // resume, and a forgotten marker would resurrect it as a phantom card. Ids
  // are globally unique (namespace hashes), so keeping them is harmless.
  const respondedIdsRef = useRef<Set<string>>(new Set());
  // Bumped on every resume so the derivation below re-runs immediately (its
  // other inputs only change when the server reacts).
  const [respondedVersion, setRespondedVersion] = useState(0);

  // Identity discipline: `messages` gets a new reference on every streamed
  // token, but while nothing is pending the derived result is always the
  // same empty shape — reuse one instance so the `approvals` bundle stays
  // referentially stable and React.memo keeps holding downstream (ChatMessage
  // re-renders would otherwise cascade per token). A non-empty result only
  // exists while the run is paused, where re-derivation is rare and cheap.
  const emptyDerivedRef = useRef<{
    pendingApprovals: PendingApproval[];
    approvalByToolCallId: Map<string, PendingApproval>;
    unassignedApprovals: PendingApproval[];
    interruptedToolCallIds: Set<string>;
  } | null>(null);

  const derived = useMemo(() => {
    // Root tool calls of the LAST AI message that no ToolMessage has answered
    // yet — the only calls a pending interrupt can be reviewing.
    const answered = new Set<string>();
    for (const message of messages) {
      if (isToolMessage(message) && message.tool_call_id) answered.add(message.tool_call_id);
    }
    let lastAI: BaseMessage | undefined;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (isAIMessage(messages[i])) {
        lastAI = messages[i];
        break;
      }
    }
    const openCalls = (lastAI && isAIMessage(lastAI) ? (lastAI.tool_calls ?? []) : []).filter(
      (toolCall) => !!toolCall.id && !answered.has(toolCall.id)
    );

    // Candidate pending interrupts. `interrupts` (root store) is
    // authoritative; the ThreadStream record fills the live-subagent gap but
    // can also REPLAY interrupts that were already resolved (the SSE pump
    // replays a finished run's events on reattach), so those entries are
    // admitted only while the thread is actually paused: run idle AND the
    // last AI turn still has unanswered tool calls.
    const candidates: { id: string; value: unknown; namespace?: string[] }[] = [];
    for (const interrupt of interrupts) {
      if (interrupt?.id && interrupt.value !== undefined) {
        candidates.push({ id: interrupt.id, value: interrupt.value });
      }
    }
    if (!isLoading && openCalls.length > 0) {
      for (const entry of getThreadInterrupts() ?? []) {
        if (!entry?.interruptId) continue;
        if (candidates.some((candidate) => candidate.id === entry.interruptId)) continue;
        candidates.push({
          id: entry.interruptId,
          value: entry.payload,
          namespace: entry.namespace,
        });
      }
    }

    const pendingApprovals = candidates
      .filter((candidate) => !respondedIdsRef.current.has(candidate.id))
      .flatMap(parseApprovals);

    if (pendingApprovals.length === 0) {
      emptyDerivedRef.current ??= {
        pendingApprovals: [],
        approvalByToolCallId: new Map<string, PendingApproval>(),
        unassignedApprovals: [],
        interruptedToolCallIds: new Set<string>(),
      };
      return emptyDerivedRef.current;
    }

    // Assign approvals to the open root calls in order: the middleware built
    // action_requests from those same calls in order, so first-unclaimed
    // matching is exact. Args equality first (two parallel same-name calls),
    // then name alone (args serialization drift), else unassigned — that's a
    // subagent interrupt (the root call in flight is `task`) or a
    // reloaded-thread approval with no rendered call.
    const claimed = new Set<string>();
    const approvalByToolCallId = new Map<string, PendingApproval>();
    const unassignedApprovals: PendingApproval[] = [];
    for (const approval of pendingApprovals) {
      const wantedArgs = JSON.stringify(approval.actionRequest.args ?? {});
      const byArgs = openCalls.find(
        (toolCall) =>
          !claimed.has(toolCall.id!) &&
          toolCall.name === approval.actionRequest.name &&
          JSON.stringify(toolCall.args ?? {}) === wantedArgs
      );
      const match =
        byArgs ??
        openCalls.find(
          (toolCall) => !claimed.has(toolCall.id!) && toolCall.name === approval.actionRequest.name
        );
      if (match?.id) {
        claimed.add(match.id);
        approvalByToolCallId.set(match.id, approval);
      } else {
        unassignedApprovals.push(approval);
      }
    }

    return {
      pendingApprovals,
      approvalByToolCallId,
      unassignedApprovals,
      interruptedToolCallIds: new Set(approvalByToolCallId.keys()),
    };
    // getThreadInterrupts reads mutable SDK state (keep its identity stable in
    // the caller); interrupts/isLoading/messages flip on every event that can
    // change it (run end, hydration, new turn), so they version the read.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- respondedVersion versions respondedIdsRef.
  }, [interrupts, isLoading, messages, getThreadInterrupts, respondedVersion]);

  // Drop queued decisions / responded markers for interrupts that are no
  // longer pending (resume landed, new run started, thread switched).
  const pendingIdsKey = useMemo(
    () =>
      Array.from(new Set(derived.pendingApprovals.map((approval) => approval.interruptId)))
        .sort()
        .join(","),
    [derived.pendingApprovals]
  );
  useEffect(() => {
    const alive = new Set(pendingIdsKey ? pendingIdsKey.split(",") : []);
    let changed = false;
    const nextQueued = new Map(queuedDecisionsRef.current);
    for (const key of nextQueued.keys()) {
      if (!alive.has(key.slice(0, key.lastIndexOf(":")))) {
        nextQueued.delete(key);
        changed = true;
      }
    }
    if (changed) {
      queuedDecisionsRef.current = nextQueued;
      setQueuedDecisions(nextQueued);
    }
  }, [pendingIdsKey]);

  const decide = useCallback(
    (approval: PendingApproval, decision: ApprovalDecision) => {
      if (respondedIdsRef.current.has(approval.interruptId)) return;
      const next = new Map(queuedDecisionsRef.current).set(approval.key, decision);
      queuedDecisionsRef.current = next;
      setQueuedDecisions(next);

      const keys = Array.from(
        { length: approval.total },
        (_, index) => `${approval.interruptId}:${index}`
      );
      if (!keys.every((key) => next.has(key))) return;

      // All sibling actions decided — submit ONE ordered resume for this
      // interrupt and mark it resolved locally so a re-render (or replayed
      // event) can't re-open or double-submit it.
      respondedIdsRef.current = new Set(respondedIdsRef.current).add(approval.interruptId);
      setRespondedVersion((version) => version + 1);
      resumeInterrupt(
        { decisions: keys.map((key) => next.get(key)!) },
        { interruptId: approval.interruptId, namespace: approval.namespace }
      );
    },
    [resumeInterrupt]
  );

  // Subagent cards report which approval keys they render, so the fallback
  // block doesn't duplicate them. Compare-before-set keeps effect loops out
  // (same pattern as handleSubagentSources in Workspace).
  const [claimsBySubagent, setClaimsBySubagent] = useState<Map<string, string[]>>(() => new Map());
  const registerSubagentClaims = useCallback((subagentId: string, keys: string[]) => {
    setClaimsBySubagent((prev) => {
      const existing = prev.get(subagentId) ?? [];
      if (existing.length === keys.length && existing.every((key, i) => key === keys[i])) {
        return prev;
      }
      const next = new Map(prev);
      if (keys.length > 0) next.set(subagentId, keys);
      else next.delete(subagentId);
      return next;
    });
  }, []);
  const claimedKeySet = useMemo(
    () => new Set(Array.from(claimsBySubagent.values()).flat()),
    [claimsBySubagent]
  );

  // One referentially-stable bundle per state change — this object is a
  // React.memo prop on every ChatMessage, so its identity must not churn on
  // unrelated renders.
  return useMemo(
    () => ({
      ...derived,
      queuedDecisions,
      decide,
      claimedKeySet,
      registerSubagentClaims,
    }),
    [derived, queuedDecisions, decide, claimedKeySet, registerSubagentClaims]
  );
}

export type ApprovalsBundle = ReturnType<typeof useInterruptApprovals>;
