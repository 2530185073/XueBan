import { useEffect, useState } from 'react'
import type { StudentClient } from '../core'

/** A blob: URL owned by the client registry, so sign-out revokes it even if this component lingers. */
export function useObjectUrl(client: StudentClient, blob: Blob | null) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!blob) { setUrl(null); return }
    const value = client.objectUrl(blob); setUrl(value)
    return () => client.releaseObjectUrl(value)
  }, [blob, client])
  return url
}

/** Loads one private asset for the signed-in student; failures stay local to the caller. */
export function useAssetBlob(client: StudentClient, assetId: string | undefined, studentId: string | null) {
  const [state, setState] = useState<{ blob: Blob | null; loading: boolean; failed: boolean }>({ blob: null, loading: !!assetId, failed: false })
  useEffect(() => {
    let live = true
    if (!assetId) { setState({ blob: null, loading: false, failed: false }); return }
    setState({ blob: null, loading: true, failed: false })
    client.assetBlob(assetId).then(blob => { if (live) setState({ blob, loading: false, failed: false }) }, () => { if (live) setState({ blob: null, loading: false, failed: true }) })
    return () => { live = false }
  }, [assetId, client, studentId])
  return state
}
