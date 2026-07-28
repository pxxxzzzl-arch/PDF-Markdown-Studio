import type {
  ConversionOptions,
  EngineStatus,
  Job,
  QualityReport,
  RuntimeConfig,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export async function getEngines(): Promise<EngineStatus[]> {
  return request<EngineStatus[]>("/api/engines");
}

export async function getConfig(): Promise<RuntimeConfig> {
  return request<RuntimeConfig>("/api/config");
}

export async function getJobs(limit = 50): Promise<Job[]> {
  const safeLimit = Math.max(1, Math.min(100, Math.trunc(limit)));
  return request<Job[]>(`/api/jobs?limit=${safeLimit}`);
}

export async function getJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}`);
}

export async function createJob(file: File, options: ConversionOptions): Promise<Job> {
  const body = new FormData();
  body.append("file", file);
  body.append("options_json", JSON.stringify(options));
  return request<Job>("/api/jobs", { method: "POST", body });
}

export async function getMarkdown(jobId: string): Promise<string> {
  const response = await fetch(`/api/jobs/${jobId}/markdown`);
  if (!response.ok) throw new Error("无法读取 Markdown 结果");
  return response.text();
}

export async function getQuality(jobId: string): Promise<QualityReport> {
  return request<QualityReport>(`/api/jobs/${jobId}/quality`);
}

export async function deleteJob(jobId: string): Promise<void> {
  const response = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
  if (!response.ok) {
    let message = `删除失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
}

export async function downloadJobArtifact(
  jobId: string,
  artifact: "archive" | "markdown",
  fallbackFilename: string,
): Promise<void> {
  triggerDownload(`/api/jobs/${jobId}/${artifact}`, fallbackFilename);
}

export async function downloadBatchArtifacts(
  jobIds: string[],
  fallbackFilename: string,
): Promise<void> {
  const params = new URLSearchParams();
  jobIds.forEach((jobId) => params.append("job_id", jobId));
  triggerDownload(`/api/jobs/archive?${params.toString()}`, fallbackFilename);
}

function triggerDownload(url: string, fallbackFilename: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.download = fallbackFilename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  window.setTimeout(() => link.remove(), 0);
}
