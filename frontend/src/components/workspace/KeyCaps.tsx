const isApple = typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent || "");

type Key = "mod" | "shift" | "enter" | (string & {});

function label(key: Key): string {
  if (key === "mod") return isApple ? "⌘" : "Ctrl";
  if (key === "shift") return isApple ? "⇧" : "Shift";
  if (key === "enter") return "Enter";
  return key;
}

/* 一个键一个键帽，只按当前系统显示一种写法，不写“Ctrl / ⌘”。 */
export function KeyCaps({ keys }: { keys: Key[] }) {
  return (
    <span className="zx-keys">
      {keys.map(key => <kbd key={key}>{label(key)}</kbd>)}
    </span>
  );
}
