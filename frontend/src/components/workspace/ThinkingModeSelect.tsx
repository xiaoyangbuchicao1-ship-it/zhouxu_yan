import { useState } from "react";
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Brain, Check, ChevronDown, Sparkles, Zap } from "lucide-react";
import { THINKING_MODES, type ThinkingMode } from "@/lib/thinking-modes";
import "./ThinkingModeSelect.css";

const icons = { none: Zap, low: Sparkles, high: Brain };

export function ThinkingModeSelect({ value, onChange }: {
  value: ThinkingMode;
  onChange: (value: ThinkingMode) => void;
}) {
  const [open, setOpen] = useState(false);
  const [keyboard, setKeyboard] = useState(false);
  // Keep the menu policy stable while open so Radix can finish its exit animation.
  const [menuKeyboard, setMenuKeyboard] = useState(false);
  const prefersReducedMotion = useReducedMotion();
  const reduced = prefersReducedMotion || keyboard;
  const selected = THINKING_MODES.find(mode => mode.value === value)!;
  const Icon = icons[value];
  return (
    <Dropdown.Root open={open} onOpenChange={setOpen}>
      <Dropdown.Trigger asChild>
        <button
          type="button"
          className="ws-thinking-trigger"
          data-mode={value}
          data-motion={reduced ? "reduced" : "full"}
          aria-label={`思考档位：${selected.label}`}
          title="选择思考程度"
          onPointerDown={() => { setKeyboard(false); setMenuKeyboard(false); }}
          onKeyDown={() => { setKeyboard(true); setMenuKeyboard(true); }}
        >
          <span className="ws-thinking-trigger-content">
            <AnimatePresence initial={false} mode="popLayout">
              <motion.span
                key={value}
                className="ws-thinking-trigger-choice"
                initial={reduced ? false : { opacity: 0, transform: "translateY(5px) scale(.96)" }}
                animate={{ opacity: 1, transform: "translateY(0) scale(1)" }}
                exit={{ opacity: 0, transform: reduced ? "none" : "translateY(-4px) scale(.96)" }}
                transition={{ duration: reduced ? 0 : 0.16, ease: [0.23, 1, 0.32, 1] }}
              >
                <Icon size={16} strokeWidth={1.7} aria-hidden="true" />
                <span>{selected.label}</span>
              </motion.span>
            </AnimatePresence>
          </span>
          <ChevronDown size={13} className="ws-thinking-chevron" aria-hidden="true" />
        </button>
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content
          className="ws-menu ws-thinking-menu"
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          data-motion={prefersReducedMotion || menuKeyboard ? "reduced" : "full"}
          aria-label="思考档位"
          onKeyDownCapture={() => setKeyboard(true)}
          onPointerMove={() => setKeyboard(false)}
        >
          <Dropdown.RadioGroup value={value} onValueChange={next => onChange(next as ThinkingMode)}>
            {THINKING_MODES.map(mode => {
              const ModeIcon = icons[mode.value];
              return (
                <Dropdown.RadioItem
                  key={mode.value}
                  value={mode.value}
                  textValue={mode.label}
                  className="ws-thinking-option"
                  data-mode={mode.value}
                >
                  <ModeIcon size={16} strokeWidth={1.7} className="ws-thinking-option-icon" aria-hidden="true" />
                  <span className="ws-thinking-label">{mode.label}</span>
                  <span className="ws-thinking-check" aria-hidden="true">
                    <Dropdown.ItemIndicator forceMount><Check size={14} strokeWidth={1.8} /></Dropdown.ItemIndicator>
                  </span>
                </Dropdown.RadioItem>
              );
            })}
          </Dropdown.RadioGroup>
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}
