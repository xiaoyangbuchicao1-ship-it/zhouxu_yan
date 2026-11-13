import { BriefcaseBusiness, LockKeyhole, MessageCircle } from "lucide-react";
import { motion } from "framer-motion";
import type { ConversationMode } from "@/lib/workbench-api";

const modes: Array<{
  value: ConversationMode;
  label: string;
  description: string;
  Icon: typeof MessageCircle;
}> = [
  {
    value: "chat",
    label: "聊天",
    description: "问答、研究与材料讨论",
    Icon: MessageCircle,
  },
  {
    value: "work",
    label: "工作",
    description: "文件处理与可交付成果",
    Icon: BriefcaseBusiness,
  },
];

export default function ConversationModeSwitch({
  value,
  onChange,
  disabled = false,
  locked = false,
  transitioning = false,
}: {
  value: ConversationMode;
  onChange?: (mode: ConversationMode) => void;
  disabled?: boolean;
  locked?: boolean;
  transitioning?: boolean;
}) {
  const selected = modes.find(({ value: mode }) => mode === value) || modes[0];
  if (locked) {
    const Icon = selected.Icon;
    return (
      <div className="ws-mode-switch is-locked" role="group" aria-label="会话模式（已固定）">
        <span
          className="ws-mode-option is-selected ws-mode-locked"
          title="会话模式创建后固定；如需另一种模式，请新建会话。"
          aria-label={`${selected.label}模式，已固定`}
        >
          <Icon size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>{selected.label}</span>
          <LockKeyhole size={12} strokeWidth={1.8} aria-hidden="true" />
        </span>
      </div>
    );
  }
  return (
    <div
      className={`ws-mode-switch ${transitioning ? "is-transitioning" : ""}`}
      role="group"
      aria-label="会话模式"
    >
      {modes.map(({ value: mode, label, description, Icon }) => (
        <button
          key={mode}
          type="button"
          className={`ws-mode-option ${value === mode ? "is-selected" : ""}`}
          aria-pressed={value === mode}
          title={description}
          disabled={disabled || transitioning}
          onClick={() => onChange?.(mode)}
        >
          {value === mode && (
            <motion.span
              layoutId="ws-mode-option-active"
              className="ws-mode-option-active"
              transition={{ type: "spring", stiffness: 420, damping: 32 }}
              aria-hidden="true"
            />
          )}
          <Icon size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}
