"use client";

import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { DEFAULT_CONFIG, StandaloneConfig } from "@/lib/config";

interface ConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (config: StandaloneConfig) => void;
  initialConfig?: StandaloneConfig;
}

const INIT_CHAT_MODEL_DOCS_URL =
  "https://reference.langchain.com/python/langchain/chat_models/base/init_chat_model";

const ModelsSectionHelp = () => (
  <p className="text-xs text-muted-foreground">
    Format <code>&lt;provider&gt;:&lt;model&gt;</code> — e.g.{" "}
    <code>anthropic:claude-sonnet-4.6</code>. Leave blank to use the default.{" "}
    <a
      href={INIT_CHAT_MODEL_DOCS_URL}
      target="_blank"
      rel="noreferrer"
      className="underline hover:text-foreground"
    >
      See all supported providers
    </a>
    .
  </p>
);

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
      <DialogContent className="sm:max-w-[525px]">
        <DialogHeader>
          <DialogTitle>Configuration</DialogTitle>
          <DialogDescription>
            Configure your LangGraph deployment settings. These settings are saved in your
            browser&apos;s local storage.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="deploymentUrl">Deployment URL</Label>
            <Input
              id="deploymentUrl"
              placeholder="https://<deployment-url>"
              value={deploymentUrl}
              onChange={(e) => setDeploymentUrl(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="assistantId">Assistant ID</Label>
            <Input
              id="assistantId"
              placeholder="<assistant-id>"
              value={assistantId}
              onChange={(e) => setAssistantId(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="langsmithApiKey">
              LangSmith API Key <span className="text-muted-foreground">(Optional)</span>
            </Label>
            <Input
              id="langsmithApiKey"
              type="password"
              placeholder="lsv2_pt_..."
              value={langsmithApiKey}
              onChange={(e) => setLangsmithApiKey(e.target.value)}
            />
          </div>
          <div className="grid gap-3 border-t pt-4">
            <div className="grid gap-1">
              <h3 className="text-sm font-semibold">
                Models <span className="font-normal text-muted-foreground">(Optional)</span>
              </h3>
              <ModelsSectionHelp />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="mainAgentModel">Main agent</Label>
              <Input
                id="mainAgentModel"
                placeholder="openai:gpt-5.4"
                value={mainAgentModel}
                onChange={(e) => setMainAgentModel(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="subagentModel">Subagents</Label>
              <Input
                id="subagentModel"
                placeholder="openai:gpt-5.4-mini"
                value={subagentModel}
                onChange={(e) => setSubagentModel(e.target.value)}
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
