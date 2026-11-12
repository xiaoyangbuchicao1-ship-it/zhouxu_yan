import { FileText } from "lucide-react";
import type { Material } from "@/lib/workbench-api";

function fileDescription(material: Material) {
  const extension = material.filename.split(".").at(-1)?.toUpperCase() || "FILE";
  const bytes = material.size_bytes;
  return bytes == null ? extension : `${extension} · ${bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`}`;
}

export function MaterialDescription({ material }: { material: Material }) {
  return <>{fileDescription(material)}</>;
}

export function MaterialCards({ materials = [] }: { materials?: Material[] }) {
  if (!materials.length) return null;
  return <ul className="ws-sent-materials" aria-label="本条消息的附件">{materials.map(m => <li key={m.id}>
    <span className="ws-file-icon" aria-hidden="true"><FileText size={20} /></span>
    <span className="ws-file-description"><strong>{m.filename}</strong><small>{fileDescription(m)}</small></span>
  </li>)}</ul>;
}
