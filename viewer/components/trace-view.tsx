"use client";

import { useMemo, useState } from "react";

import { CodeBlock } from "@/components/ai-elements/code-block";
import { Conversation, ConversationContent, ConversationScrollButton } from "@/components/ai-elements/conversation";
import { Message, MessageContent, MessageResponse } from "@/components/ai-elements/message";
import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { Tool, ToolContent, ToolHeader, ToolInput } from "@/components/ai-elements/tool";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  agentSeconds,
  finalMeasurements,
  formatMoney,
  formatSeconds,
  rewardValue,
  sampledTurns,
  traceCompany,
  traceOutcome,
  traceRole,
  traceToMessages,
  type ViewerMessage,
  traceMeta,
  type Trajectory,
} from "@/lib/trace";

const OUTPUT_PREVIEW = 6000;

function looksLikeJson(text: string): boolean {
  const t = text.trim();
  return (t.startsWith("{") && t.endsWith("}")) || (t.startsWith("[") && t.endsWith("]"));
}

function ToolResult({ output }: { output: unknown }) {
  const [full, setFull] = useState(false);
  const text = typeof output === "string" ? output : JSON.stringify(output, null, 2);
  const truncated = !full && text.length > OUTPUT_PREVIEW;
  const shown = truncated ? text.slice(0, OUTPUT_PREVIEW) + "\n…" : text;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="font-medium text-muted-foreground text-xs uppercase tracking-wide">Result · {text.length.toLocaleString()} chars</h4>
        {text.length > OUTPUT_PREVIEW && (
          <Button size="sm" variant="ghost" onClick={() => setFull((v) => !v)}>
            {full ? "show less" : "show all"}
          </Button>
        )}
      </div>
      <CodeBlock code={shown} language={looksLikeJson(text) ? "json" : "bash"} />
    </div>
  );
}

function toolTitle(input: unknown, toolName: string): string {
  if (input && typeof input === "object") {
    const record = input as Record<string, unknown>;
    if (typeof record.command === "string") return record.command.length > 110 ? record.command.slice(0, 110) + "…" : record.command;
    if (typeof record.filePath === "string") return `${toolName} ${record.filePath}`;
    if (typeof record.description === "string") return `${toolName}: ${record.description}`;
  }
  return toolName;
}

function Stats({ message }: { message: ViewerMessage }) {
  const s = message.stats;
  if (!s) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-muted-foreground text-xs">
      <span>turn {message.turn}</span>
      {s.promptTokens !== undefined && <span>{s.promptTokens.toLocaleString()} in</span>}
      {s.completionTokens !== undefined && <span>{s.completionTokens.toLocaleString()} out</span>}
      {s.reasoningTokens ? <span>{s.reasoningTokens.toLocaleString()} reasoning</span> : null}
      {s.seconds !== undefined && <span>{s.seconds.toFixed(1)} s</span>}
      {s.finish && <span>· {s.finish}</span>}
    </div>
  );
}

function PromptBlock({ label, text, defaultOpen }: { label: string; text: string; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-md border">
      <button className="flex w-full items-center justify-between p-3 text-left text-sm" onClick={() => setOpen((v) => !v)}>
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground text-xs">{text.length.toLocaleString()} chars · {open ? "hide" : "show"}</span>
      </button>
      {open && (
        <div className="border-t p-4 text-sm">
          <MessageResponse>{text}</MessageResponse>
        </div>
      )}
    </div>
  );
}

function AssistantTurn({ message }: { message: ViewerMessage }) {
  return (
    <Message from="assistant" className="max-w-full">
      <MessageContent className="w-full max-w-full">
        <div className="flex flex-wrap items-center gap-2">
          <Stats message={message} />
          {message.gameDate && (
            <Badge variant="outline" className="font-mono text-xs">
              game date {message.gameDate}
            </Badge>
          )}
        </div>
        {message.parts.map((part, index) => {
          if (part.type === "reasoning") {
            return (
              <Reasoning key={index} defaultOpen={false} isStreaming={false} duration={undefined}>
                <ReasoningTrigger />
                <ReasoningContent>{part.text}</ReasoningContent>
              </Reasoning>
            );
          }
          if (part.type === "text") {
            return <MessageResponse key={index}>{part.text}</MessageResponse>;
          }
          if (part.type === "dynamic-tool") {
            return (
              <Tool key={part.toolCallId} defaultOpen={false}>
                <ToolHeader className="[&_span]:break-all [&_span]:text-left" type="dynamic-tool" toolName={part.toolName} state={part.state} title={toolTitle(part.input, part.toolName)} />
                <ToolContent>
                  <ToolInput input={part.input} />
                  {part.state === "output-available" && <ToolResult output={part.output} />}
                </ToolContent>
              </Tool>
            );
          }
          return null;
        })}
      </MessageContent>
    </Message>
  );
}

function Header({ trace, onBack }: { trace: Trajectory; onBack?: () => void }) {
  const meta = traceMeta(trace);
  const outcome = traceOutcome(trace);
  const final = finalMeasurements(trace);
  const reward = rewardValue(trace);
  const info = meta.info ?? {};
  const cfg = meta.agent?.config;
  const [showTail, setShowTail] = useState(false);
  const tail = (info.harness_output_tail ?? info.opencode_output_tail) as string | undefined;
  const save = info.openttd_save as { host_path?: string; error?: string } | undefined;
  return (
    <div className="space-y-3 border-b p-3 md:p-4">
      <div className="flex flex-wrap items-center gap-2">
        {onBack && (
          <Button size="sm" variant="outline" className="md:hidden" onClick={onBack}>
            ‹ runs
          </Button>
        )}
        <h2 className="font-semibold text-base md:text-lg">{meta.task?.data?.name ?? trace.agent.name}</h2>
        <Badge variant="secondary">{traceRole(trace)}{traceCompany(trace) !== null ? ` · company ${traceCompany(trace)}` : ""}</Badge>
        {trace.agent.model_name && <Badge variant="outline">{trace.agent.model_name}</Badge>}
        {cfg?.harness?.id && <Badge variant="outline">{cfg.harness.id}{cfg.harness.user ? ` as ${cfg.harness.user}` : ""}</Badge>}
        {cfg?.runtime?.type && <Badge variant="outline">{cfg.runtime.type}</Badge>}
        {meta.ok !== undefined && <Badge variant={meta.ok ? "secondary" : "destructive"}>{meta.ok ? "ok" : "failed"}</Badge>}
        {meta.stop_condition && <Badge variant="outline">stop: {meta.stop_condition}</Badge>}
        <Badge variant="outline">{trace.schema_version}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 break-words text-xs sm:gap-x-6 sm:gap-y-1 sm:text-sm lg:grid-cols-4">
        <div><span className="text-muted-foreground">reward</span> <strong>{reward === null ? "–" : reward.toFixed(3)}</strong></div>
        <div><span className="text-muted-foreground">agent steps</span> <strong>{sampledTurns(trace)}</strong></div>
        <div><span className="text-muted-foreground">agent time</span> <strong>{formatSeconds(agentSeconds(trace))}</strong></div>
        <div><span className="text-muted-foreground">final date</span> <strong>{(final?.final_date as string) ?? "–"}</strong></div>
        {outcome && Object.entries(outcome.terms).map(([name, value]) => (
          <div key={name}><span className="text-muted-foreground">{name}</span> <strong>{value.toFixed(3)}</strong> <span className="text-muted-foreground">({String(outcome.values[name])})</span></div>
        ))}
        {final && (
          <>
            <div><span className="text-muted-foreground">operating profit</span> <strong>{formatMoney(final.operating_profit)}</strong></div>
            <div><span className="text-muted-foreground">company value</span> <strong>{formatMoney(final.company_value)}</strong></div>
            <div><span className="text-muted-foreground">vehicles</span> <strong>{JSON.stringify(final.vehicles ?? {})}</strong></div>
            <div><span className="text-muted-foreground">stations / rail / road</span> <strong>{String(final.stations ?? "–")} / {String(final.rail_pieces ?? "–")} / {String(final.road_pieces ?? "–")}</strong></div>
            <div className="sm:col-span-2"><span className="text-muted-foreground">delivered</span> <strong>{JSON.stringify(final.cargo_delivered ?? {})}</strong></div>
          </>
        )}
        {save?.host_path && <div className="truncate sm:col-span-2"><span className="text-muted-foreground">save</span> <span className="font-mono text-xs">{save.host_path}</span></div>}
      </div>
      {meta.errors && meta.errors.length > 0 && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs">
          {meta.errors.map((e, i) => <div key={i}><strong>{e.type}</strong>: {e.message}</div>)}
        </div>
      )}
      {tail && (
        <div>
          <Button size="sm" variant="ghost" onClick={() => setShowTail((v) => !v)}>{showTail ? "hide" : "show"} harness output tail</Button>
          {showTail && <CodeBlock code={tail} language="bash" />}
        </div>
      )}
    </div>
  );
}

export function TraceView({ trace, onBack }: { trace: Trajectory; onBack?: () => void }) {
  const [showContext, setShowContext] = useState(false);
  const [limit, setLimit] = useState(100);
  const messages = useMemo(() => traceToMessages(trace, showContext), [trace, showContext]);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <Header trace={trace} onBack={onBack} />
      <label className="border-b px-4 py-1 text-xs text-muted-foreground">
        <input type="checkbox" checked={showContext} onChange={e => setShowContext(e.target.checked)} /> Show copied context · {messages.length} messages
      </label>
      <Conversation className="min-h-0 flex-1" initial={false}>
        <ConversationContent className="gap-4 p-2 md:p-4">
          {messages.slice(0, limit).map((message) => {
            if (message.role === "system") {
              const text = message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
              return <PromptBlock key={message.id} label="system prompt (harness)" text={text} defaultOpen={false} />;
            }
            if (message.role === "user") {
              const text = message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
              return <PromptBlock key={message.id} label="task prompt" text={text} defaultOpen={false} />;
            }
            return <AssistantTurn key={message.id} message={message} />;
          })}
          {limit < messages.length && <Button variant="outline" onClick={() => setLimit(n => n + 100)}>Show next 100 messages ({messages.length - limit} remaining)</Button>}
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>
    </div>
  );
}
