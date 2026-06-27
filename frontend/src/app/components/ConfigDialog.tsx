"use client";

import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { AppearanceSettings } from "@/app/components/AppearanceSettings";
import { DEFAULT_CONFIG, StandaloneConfig } from "@/lib/config";

interface ConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (config: StandaloneConfig) => void;
  initialConfig?: StandaloneConfig;
}

const INIT_CHAT_MODEL_DOCS_URL =
  "https://reference.langchain.com/python/langchain/chat_models/base/init_chat_model";

const EYEBROW = "text-[11px] font-bold uppercase tracking-[0.08em] text-tertiary";

export function ConfigDialog({ open, onOpenChange, onSave, initialConfig }: ConfigDialogProps) {
  const formConfig = initialConfig ?? DEFAULT_CONFIG;
  const [deploymentUrl, setDeploymentUrl] = useState(formConfig?.deploymentUrl || "");
  const [assistantId, setAssistantId] = useState(formConfig?.assistantId || "");
  const [langsmithApiKey, setLangsmithApiKey] = useState(formConfig?.langsmithApiKey || "");
  const [mainAgentModel, setMainAgentModel] = useState(formConfig?.mainAgentModel || "");
  const [subagentModel, setSubagentModel] = useState(formConfig?.subagentModel || "");

  useEffect(() => {
    if (open && formConfig) {
      setDeploymentUrl(formConfig.deploymentUrl);
      setAssistantId(formConfig.assistantId);
      setLangsmithApiKey(formConfig.langsmithApiKey || "");
      setMainAgentModel(formConfig.mainAgentModel || "");
      setSubagentModel(formConfig.subagentModel || "");
    }
  }, [open, formConfig]);

  const handleSave = () => {
    if (!deploymentUrl || !assistantId) {
      alert("Please fill in all required fields");
      return;
    }

    onSave({
      deploymentUrl,
      assistantId,
      langsmithApiKey: langsmithApiKey || undefined,
      mainAgentModel: mainAgentModel.trim() || undefined,
      subagentModel: subagentModel.trim() || undefined,
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col gap-0 overflow-hidden bg-surface-raised p-0 sm:max-w-[560px]">
        <DialogHeader className="shrink-0 border-b border-primary px-6 py-4">
          <DialogTitle className="text-xl font-bold tracking-tight">Settings</DialogTitle>
        </DialogHeader>

        <div className="grid flex-1 gap-7 overflow-y-auto px-6 py-5">
          {/* Appearance — theme + accent */}
          <AppearanceSettings />

          {/* Model */}
          <div className="grid gap-3">
            <span className={EYEBROW}>Model</span>
            <div className="grid gap-2">
              <Label htmlFor="mainAgentModel" className="text-xs text-secondary">
                Main agent — <code className="font-mono">provider:model</code>
              </Label>
              <Input
                id="mainAgentModel"
                placeholder="anthropic:claude-sonnet-4-6"
                value={mainAgentModel}
                onChange={(e) => setMainAgentModel(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="subagentModel" className="text-xs text-secondary">
                Subagents
              </Label>
              <Input
                id="subagentModel"
                placeholder="anthropic:claude-haiku-4-5"
                value={subagentModel}
                onChange={(e) => setSubagentModel(e.target.value)}
              />
            </div>
            <p className="text-xs text-tertiary">
              Leave blank to use the assistant default.{" "}
              <a
                href={INIT_CHAT_MODEL_DOCS_URL}
                target="_blank"
                rel="noreferrer"
                className="text-brand-accent underline-offset-2 hover:underline"
              >
                Supported providers
              </a>
              .
            </p>
          </div>

          {/* Connection */}
          <div className="grid gap-3">
            <span className={EYEBROW}>Connection</span>
            <div className="grid gap-2">
              <Label htmlFor="deploymentUrl" className="text-xs text-secondary">
                Deployment URL
              </Label>
              <Input
                id="deploymentUrl"
                placeholder="https://<deployment-url>"
                value={deploymentUrl}
                onChange={(e) => setDeploymentUrl(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="assistantId" className="text-xs text-secondary">
                Assistant ID
              </Label>
              <Input
                id="assistantId"
                placeholder="<assistant-id>"
                value={assistantId}
                onChange={(e) => setAssistantId(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="langsmithApiKey" className="text-xs text-secondary">
                LangSmith API Key <span className="text-tertiary">(optional)</span>
              </Label>
              <Input
                id="langsmithApiKey"
                type="password"
                placeholder="lsv2_pt_..."
                value={langsmithApiKey}
                onChange={(e) => setLangsmithApiKey(e.target.value)}
              />
            </div>
          </div>
        </div>

        <DialogFooter className="shrink-0 border-t border-primary px-6 py-4">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={handleSave}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
