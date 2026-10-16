import type { Job, JobStatus } from './api/contracts.generated'

export const jobLabels: Record<JobStatus, string> = {
  queued: '排队中', running: '正在识别新题', retryable_failed: '暂时失败，系统会自动重试', succeeded: '已完成',
  failed: '未能完成', superseded: '已被后续操作取代', cancelled: '已取消',
}

export const isTerminal = (status: JobStatus) => ['succeeded', 'failed', 'superseded', 'cancelled'].includes(status)

const wait = (ms: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  if (signal?.aborted) { reject(signal.reason); return }
  const timer = setTimeout(() => { signal?.removeEventListener('abort', stop); resolve() }, ms)
  const stop = () => { clearTimeout(timer); reject(signal!.reason) }
  signal?.addEventListener('abort', stop, { once: true })
})

export interface PollOptions {
  signal?: AbortSignal
  onUpdate?: (job: Job) => void
  intervalMs?: number
  timeoutMs?: number
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>
  now?: () => number
}

/** Poll until the job reaches a terminal status; returns the last seen job when the time budget runs out. */
export async function pollJob(read: (signal?: AbortSignal) => Promise<Job>, initial: Job, options: PollOptions = {}): Promise<Job> {
  const { signal, onUpdate, intervalMs = 1500, timeoutMs = 180_000, sleep = wait, now = Date.now } = options
  const deadline = now() + timeoutMs
  let job = initial
  while (!isTerminal(job.status) && now() < deadline) {
    await sleep(Math.max(intervalMs, (job.retry_after_seconds ?? 0) * 1000), signal)
    const next = await read(signal)
    if (next.id !== initial.id) throw new Error('Job identity changed')
    if (next.revision >= job.revision) { job = next; onUpdate?.(job) }
  }
  return job
}
