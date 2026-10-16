/** Tracks blob: URLs created for one signed-in scope so sign-out can revoke all of them. */
export class ObjectUrls {
  private urls = new Set<string>()
  create(blob: Blob): string { const url = URL.createObjectURL(blob); this.urls.add(url); return url }
  release(url: string) { if (this.urls.delete(url)) URL.revokeObjectURL(url) }
  releaseAll() { for (const url of this.urls) URL.revokeObjectURL(url); this.urls.clear() }
  get size() { return this.urls.size }
}
