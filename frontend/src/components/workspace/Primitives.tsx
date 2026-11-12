import {
  forwardRef,
  useRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { X, AlertCircle } from "lucide-react";
import { ActivityIndicator } from "./ActivityIndicator";

export const IconButton = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { label: string }
>(function IconButton({ label, children, ...props }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      className="ws-icon-button"
      title={label}
      aria-label={label}
      {...props}
    >
      {children}
    </button>
  );
});

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  side,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  side?: "left" | "right";
}) {
  const returnFocus = useRef<HTMLElement | null>(null);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="ws-overlay" />
        <Dialog.Content
          className={`ws-dialog ${side ? `ws-sheet ws-sheet-${side}` : ""}`}
          onOpenAutoFocus={() => {
            returnFocus.current =
              document.activeElement instanceof HTMLElement
                ? document.activeElement
                : null;
          }}
          onCloseAutoFocus={(event) => {
            if (returnFocus.current?.isConnected) {
              event.preventDefault();
              returnFocus.current.focus({ preventScroll: true });
            }
          }}
        >
          <div className="ws-dialog-heading">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close asChild>
              <IconButton label="关闭">
                <X size={18} />
              </IconButton>
            </Dialog.Close>
          </div>
          {description ? (
            <Dialog.Description className="ws-dialog-description">
              {description}
            </Dialog.Description>
          ) : (
            <Dialog.Description className="ws-sr-only">
              {title}面板，可按 Escape 关闭。
            </Dialog.Description>
          )}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Menu({
  trigger,
  children,
  side = "bottom",
  align = "end",
  className,
}: {
  trigger: ReactNode;
  children: ReactNode;
  side?: "bottom" | "top";
  align?: "start" | "end";
  className?: string;
}) {
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>{trigger}</Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content
          className={className ? `ws-menu ${className}` : "ws-menu"}
          side={side}
          align={align}
          sideOffset={8}
          collisionPadding={12}
        >
          {children}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}

export function MenuItem({
  children,
  onSelect,
  danger,
  disabled,
}: {
  children: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <Dropdown.Item
      className={`ws-menu-item ${danger ? "ws-danger" : ""}`}
      onSelect={onSelect}
      disabled={disabled}
    >
      {children}
    </Dropdown.Item>
  );
}

export function Loading({ label = "正在载入…" }: { label?: string }) {
  return (
    <div className="ws-loading" role="status">
      <ActivityIndicator size={18} />
      <span>{label}</span>
    </div>
  );
}
export function LoadError({
  retry,
  label = "暂时无法载入，请检查连接后重试。",
}: {
  retry: () => void;
  label?: string;
}) {
  return (
    <div className="ws-load-error" role="alert">
      <AlertCircle size={18} />
      <p>{label}</p>
      <button className="ws-text-button" onClick={retry}>
        重新载入
      </button>
    </div>
  );
}
