import { Check, ChevronDown, Layers3 } from "lucide-react";
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import type { ModelCatalog, ModelProfile } from "@/lib/workbench-api";
import "./ModelSelect.css";

export function ModelSelect({
  catalog,
  value,
  onChange,
}: {
  catalog?: ModelCatalog;
  value: string;
  onChange: (profile: ModelProfile) => void;
}) {
  const profiles = catalog?.groups.flatMap((group) => group.models) || [];
  const selected = profiles.find((model) => model.profile_id === value && model.available)
    || profiles.find((model) => model.available);
  if (!selected) return null;
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>
        <button type="button" className="ws-model-trigger" aria-label={`模型：${selected.label}`}>
          <Layers3 size={15} aria-hidden="true" />
          <span>{selected.label}</span>
          <ChevronDown size={13} className="ws-model-chevron" aria-hidden="true" />
        </button>
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content className="ws-menu ws-model-menu" side="top" align="start" sideOffset={8} collisionPadding={12}>
          {catalog?.groups.map((group) => {
            const availableModels = group.models.filter(model => model.available);
            if (!availableModels.length) return null;
            return (
            <Dropdown.Group key={group.family}>
              <Dropdown.Label className="ws-model-group-label">{group.label}</Dropdown.Label>
              {availableModels.map((model) => (
                <Dropdown.Item
                  key={model.profile_id}
                  className="ws-model-option"
                  onSelect={() => onChange(model)}
                >
                  <span className="ws-model-option-copy">
                    <span>{model.label}</span>
                  </span>
                  {model.profile_id === selected.profile_id ? <Check size={14} aria-hidden="true" /> : null}
                </Dropdown.Item>
              ))}
            </Dropdown.Group>
            );
          })}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}
