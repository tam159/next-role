"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { AlertCircle, Check, Clock3, X, Pencil } from "lucide-react";
import type { ActionRequest, ApprovalDecision, ReviewConfig } from "@/app/types/types";

interface ToolApprovalInterruptProps {
  actionRequest: ActionRequest;
  reviewConfig?: ReviewConfig;
  /**
   * Emits ONE decision for THIS action. The owner (useInterruptApprovals)
   * batches sibling decisions and submits the ordered resume payload once
   * every action of the interrupt is decided — the card never talks to the
   * stream itself.
   */
  onDecide: (decision: ApprovalDecision) => void;
  /** Set once this action's decision is queued (awaiting siblings). */
  queuedDecision?: ApprovalDecision;
  /** e.g. "Waiting on 1 more decision" — shown next to the queued chip. */
  queueNote?: string;
  isLoading?: boolean;
}

const QUEUED_LABEL: Record<ApprovalDecision["type"], string> = {
  approve: "Approved",
  edit: "Edited",
  reject: "Rejected",
};

export function ToolApprovalInterrupt({
  actionRequest,
  reviewConfig,
  onDecide,
  queuedDecision,
  queueNote,
  isLoading,
}: ToolApprovalInterruptProps) {
  const [rejectionMessage, setRejectionMessage] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [editedArgs, setEditedArgs] = useState<Record<string, unknown>>({});
  const [showRejectionInput, setShowRejectionInput] = useState(false);

  const allowedDecisions = reviewConfig?.allowed_decisions ?? ["approve", "reject", "edit"];
  const disabled = !!isLoading || !!queuedDecision;

  const handleApprove = () => {
    onDecide({ type: "approve" });
  };

  const handleReject = () => {
    if (showRejectionInput) {
      handleRejectConfirm();
    } else {
      setShowRejectionInput(true);
    }
  };

  const handleRejectConfirm = () => {
    onDecide({
      type: "reject",
      message: rejectionMessage.trim(),
    });
  };

  const handleEdit = () => {
    if (isEditing) {
      onDecide({
        type: "edit",
        edited_action: {
          name: actionRequest.name,
          args: editedArgs,
        },
      });
      setIsEditing(false);
      setEditedArgs({});
    }
  };

  const startEditing = () => {
    setIsEditing(true);
    setEditedArgs(JSON.parse(JSON.stringify(actionRequest.args)));
    setShowRejectionInput(false);
  };

  const cancelEditing = () => {
    setIsEditing(false);
    setEditedArgs({});
  };

  const updateEditedArg = (key: string, value: string) => {
    try {
      const parsedValue =
        value.trim().startsWith("{") || value.trim().startsWith("[") ? JSON.parse(value) : value;
      setEditedArgs((prev) => ({ ...prev, [key]: parsedValue }));
    } catch {
      setEditedArgs((prev) => ({ ...prev, [key]: value }));
    }
  };

  return (
    <div className="w-full rounded-md border border-border bg-muted/30 p-4">
      {/* Header */}
      <div className="mb-3 flex items-center gap-2 text-foreground">
        <AlertCircle size={16} className="text-warning" />
        <span className="text-xs font-semibold tracking-wider uppercase">Approval Required</span>
      </div>

      {/* Description */}
      {actionRequest.description && (
        <p className="mb-3 text-sm text-muted-foreground">{actionRequest.description}</p>
      )}

      {/* Tool Info Card */}
      <div className="mb-4 rounded-sm border border-border bg-background p-3">
        <div className="mb-2">
          <span className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
            Tool
          </span>
          <p className="mt-1 font-mono text-sm font-medium text-foreground">{actionRequest.name}</p>
        </div>

        {isEditing ? (
          <div>
            <span className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
              Edit Arguments
            </span>
            <div className="mt-2 space-y-3">
              {Object.entries(actionRequest.args).map(([key, value]) => (
                <div key={key}>
                  <label className="mb-1 block text-xs font-medium text-foreground">{key}</label>
                  <Textarea
                    value={
                      editedArgs[key] !== undefined
                        ? typeof editedArgs[key] === "string"
                          ? (editedArgs[key] as string)
                          : JSON.stringify(editedArgs[key], null, 2)
                        : typeof value === "string"
                          ? value
                          : JSON.stringify(value, null, 2)
                    }
                    onChange={(e) => updateEditedArg(key, e.target.value)}
                    className="font-mono text-xs"
                    rows={typeof value === "string" && value.length < 100 ? 2 : 4}
                    disabled={disabled}
                  />
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div>
            <span className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
              Arguments
            </span>
            <pre className="mt-2 overflow-x-auto rounded-sm border border-border bg-muted/40 p-2 font-mono text-xs break-all whitespace-pre-wrap text-foreground">
              {JSON.stringify(actionRequest.args, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Rejection Message Input */}
      {showRejectionInput && !isEditing && !queuedDecision && (
        <div className="mb-4">
          <label className="mb-2 block text-xs font-medium text-foreground">
            Rejection Message (optional)
          </label>
          <Textarea
            value={rejectionMessage}
            onChange={(e) => setRejectionMessage(e.target.value)}
            placeholder="Explain why you're rejecting this action..."
            className="text-sm"
            rows={2}
            disabled={disabled}
          />
        </div>
      )}

      {/* Actions */}
      {queuedDecision ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/50 px-2.5 py-1 text-xs font-medium text-foreground">
            <Clock3 size={12} className="text-warning" />
            {QUEUED_LABEL[queuedDecision.type]} — queued
          </span>
          {queueNote && <span className="text-xs text-muted-foreground">{queueNote}</span>}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {isEditing ? (
            <>
              <Button variant="outline" size="sm" onClick={cancelEditing} disabled={disabled}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" onClick={handleEdit} disabled={disabled}>
                <Check size={14} />
                {isLoading ? "Saving..." : "Save & Approve"}
              </Button>
            </>
          ) : showRejectionInput ? (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setShowRejectionInput(false);
                  setRejectionMessage("");
                }}
                disabled={disabled}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={handleRejectConfirm}
                disabled={disabled}
              >
                {isLoading ? "Rejecting..." : "Confirm Reject"}
              </Button>
            </>
          ) : (
            <>
              {allowedDecisions.includes("reject") && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleReject}
                  disabled={disabled}
                  className="text-destructive hover:bg-destructive/10"
                >
                  <X size={14} />
                  Reject
                </Button>
              )}
              {allowedDecisions.includes("edit") && (
                <Button variant="outline" size="sm" onClick={startEditing} disabled={disabled}>
                  <Pencil size={14} />
                  Edit
                </Button>
              )}
              {allowedDecisions.includes("approve") && (
                <Button variant="primary" size="sm" onClick={handleApprove} disabled={disabled}>
                  <Check size={14} />
                  {isLoading ? "Approving..." : "Approve"}
                </Button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
