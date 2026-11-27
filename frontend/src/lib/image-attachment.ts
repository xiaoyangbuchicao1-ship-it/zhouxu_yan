const IMAGE_EXTENSION_RE = /\.(png|jpe?g|webp|gif|bmp|svg)$/i;

export function isImageAttachment(filename: string, mediaType?: string | null): boolean {
  if (typeof mediaType === "string" && mediaType.toLowerCase().startsWith("image/")) {
    return true;
  }
  return IMAGE_EXTENSION_RE.test(filename.trim());
}
