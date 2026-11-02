// Saving a file the API sent and reading a chosen file's bytes: one copy for every screen.

/** Saves a file the API sent (never a direct storage link, rule 4). Does nothing where the browser has no object addresses. */
export function saveBlob(blob: Blob, name: string): void {
  if (typeof URL.createObjectURL !== "function") return;
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  document.body.appendChild(link);
  try {
    link.click();
  } catch {
    // jsdom has no navigation; the browser saves the file
  }
  link.remove();
  URL.revokeObjectURL(href);
}

/** A file's bytes (older test browsers have no `Blob.arrayBuffer`). */
export async function bytesOf(file: Blob): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}
