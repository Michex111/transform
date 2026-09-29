import { motion, useReducedMotion } from "motion/react";
import { stageLabel, type AssistantToolActivity } from "@/lib/assistantChat";

/**
 * The "what is it doing" row shown while a turn streams.
 *
 * It is deliberately quiet: a pulsing dot and the current step, plus the
 * summary of any tool that has already finished. Nothing here is a spinner
 * overlay — the assistant's text streams into the transcript above it, so the
 * row accompanies the answer rather than replacing it.
 */
export function ToolActivity({
  tools,
  stage,
  streaming,
}: {
  tools: AssistantToolActivity[];
  stage: string | null;
  streaming: boolean;
}) {
  const reduce = useReducedMotion();
  if (!streaming) return null;

  const running = tools.find((tool) => tool.status === "running");
  const headline = running?.label ?? (tools.length ? "Working…" : stageLabel(stage));
  const completed = tools.filter((tool) => tool.status === "done" && tool.summary);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <motion.span
          aria-hidden
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary"
          animate={reduce ? undefined : { opacity: [0.3, 1, 0.3], scale: [0.85, 1.2, 0.85] }}
          transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
        />
        <span className="text-sm text-muted">{headline}</span>
      </div>
      {completed.map((tool) => (
        <p key={tool.name} className="pl-3.5 text-xs text-muted/80">
          {tool.summary}
        </p>
      ))}
    </div>
  );
}
