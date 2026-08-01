import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import {
  createJob,
  deleteJob,
  downloadBatchArtifacts,
  downloadJobArtifact,
  getConfig,
  getEngines,
  getJob,
  getJobs,
  getMarkdown,
  getQuality,
} from "./api";
import type {
  ConversionOptions,
  EngineStatus,
  Job,
  QualityIssue,
  QualityReport,
  RuntimeConfig,
} from "./types";

const defaultOptions: ConversionOptions = {
  primary_engine: "docling",
  fallback_engine: "paddleocr",
  ocr_mode: "auto",
  enable_code_enrichment: false,
  extract_images: true,
  preserve_page_markers: false,
  include_front_matter: true,
  enable_quality_fallback: true,
  minimum_quality_score: 72,
};

type ResultTab = "preview" | "source" | "quality";
type ResultMode = "job" | "batch";
type LocalSubmissionStatus = "ready" | "uploading" | "submit_failed";

interface BatchItem {
  localId: string;
  file: File;
  status: LocalSubmissionStatus;
  submitError?: string;
  job?: Job;
}

interface BatchSummary {
  total: number;
  totalBytes: number;
  submitted: number;
  ready: number;
  uploading: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  progress: number;
}

const BATCH_UPLOAD_CONCURRENCY = 2;

const defaultRuntimeConfig: RuntimeConfig = {
  max_file_size: 200 * 1024 * 1024,
  max_pages: 500,
  max_batch_files: 20,
  job_ttl_hours: 72,
};

export default function App() {
  const [batchItems, setBatchItems] = useState<BatchItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [options, setOptions] = useState(defaultOptions);
  const [engines, setEngines] = useState<EngineStatus[]>([]);
  const [runtimeConfig, setRuntimeConfig] = useState(defaultRuntimeConfig);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [quality, setQuality] = useState<QualityReport | null>(null);
  const [tab, setTab] = useState<ResultTab>("preview");
  const [resultMode, setResultMode] = useState<ResultMode>("job");
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [downloading, setDownloading] = useState<"archive" | "markdown" | null>(null);
  const [batchDownloading, setBatchDownloading] = useState(false);
  const [selectedBatchJobIds, setSelectedBatchJobIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [batchDownloadError, setBatchDownloadError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [batchPollError, setBatchPollError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [resultLoading, setResultLoading] = useState(false);
  const [resultError, setResultError] = useState<string | null>(null);
  const [wrapSource, setWrapSource] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const resultRequest = useRef(0);
  const submitLock = useRef(false);
  const batchItemsRef = useRef<BatchItem[]>([]);
  const activeJobRef = useRef<Job | null>(null);

  const refreshJobs = useCallback(async () => {
    const nextJobs = await getJobs();
    setJobs(nextJobs);
    setHistoryError(null);
    return nextJobs;
  }, []);

  const loadJobResult = useCallback(async (jobId: string) => {
    const requestId = ++resultRequest.current;
    setResultLoading(true);
    setResultError(null);
    const [markdownResult, qualityResult] = await Promise.allSettled([
      getMarkdown(jobId),
      getQuality(jobId),
    ]);
    if (requestId !== resultRequest.current) return;

    const failures: string[] = [];
    if (markdownResult.status === "fulfilled") setMarkdown(markdownResult.value);
    else failures.push(markdownResult.reason instanceof Error ? markdownResult.reason.message : "无法读取 Markdown");
    if (qualityResult.status === "fulfilled") setQuality(qualityResult.value);
    else failures.push(qualityResult.reason instanceof Error ? qualityResult.reason.message : "无法读取质量报告");
    setResultError(failures.length ? failures.join("；") : null);
    setResultLoading(false);
  }, []);

  useEffect(() => {
    batchItemsRef.current = batchItems;
  }, [batchItems]);

  useEffect(() => {
    activeJobRef.current = activeJob;
  }, [activeJob]);

  const trackedBatchJobIds = useMemo(
    () => batchItems.flatMap((item) => item.job ? [item.job.id] : []),
    [batchItems],
  );
  const trackedBatchJobKey = trackedBatchJobIds.join(",");

  useEffect(() => {
    void getEngines().then((statuses) => {
      setEngines(statuses);
      setOptions((current) => {
        const primaryReady = statuses.some(
          (engine) => engine.name === current.primary_engine && engine.available,
        );
        const fallbackReady = statuses.some(
          (engine) => engine.name === current.fallback_engine && engine.available,
        );
        return {
          ...current,
          primary_engine: primaryReady ? current.primary_engine : "native",
          enable_quality_fallback: fallbackReady && current.enable_quality_fallback,
        };
      });
    }).catch((reason: Error) => setError(reason.message));
    void getConfig().then(setRuntimeConfig).catch((reason: Error) => setError(reason.message));
    void refreshJobs().then((nextJobs) => {
      const resumable = nextJobs.find((job) => ["queued", "running"].includes(job.status));
      if (resumable) setActiveJob((current) => current || resumable);
    }).catch((reason: Error) => setHistoryError(reason.message));
  }, [refreshJobs]);

  const activeTrackedByBatch = Boolean(
    activeJob && trackedBatchJobIds.includes(activeJob.id),
  );

  useEffect(() => {
    if (
      !activeJob
      || activeTrackedByBatch
      || !["queued", "running"].includes(activeJob.status)
    ) return;
    const jobId = activeJob.id;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await getJob(jobId);
        if (cancelled) return;
        setActiveJob((current) => current?.id === jobId ? next : current);
        setError(null);
        if (next.status === "completed") {
          await loadJobResult(next.id);
          if (cancelled) return;
          setTab("preview");
          void refreshJobs().catch((reason: Error) => setHistoryError(reason.message));
        } else if (next.status === "failed") {
          setError(next.error || "转换失败");
          void refreshJobs().catch((reason: Error) => setHistoryError(reason.message));
        } else {
          timer = window.setTimeout(poll, 900);
        }
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "无法获取任务状态");
        timer = window.setTimeout(poll, 1800);
      }
    };
    timer = window.setTimeout(poll, 200);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [
    activeJob?.id,
    activeJob?.status,
    activeTrackedByBatch,
    loadJobResult,
    refreshJobs,
  ]);

  useEffect(() => {
    if (!trackedBatchJobKey) return;
    const jobIds = trackedBatchJobKey.split(",");
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      const currentItems = batchItemsRef.current;
      const workingItems = currentItems.filter(
        (item) => item.job && jobIds.includes(item.job.id)
          && ["queued", "running"].includes(item.job.status),
      );
      if (workingItems.length === 0) {
        setBatchPollError(null);
        void refreshJobs().catch((reason: Error) => setHistoryError(reason.message));
        return;
      }

      const settled = await Promise.allSettled(
        workingItems.map((item) => getJob(item.job!.id)),
      );
      if (cancelled) return;

      const updates = new Map<string, Job>();
      const pollFailures: string[] = [];
      settled.forEach((result, index) => {
        const item = workingItems[index];
        if (result.status === "fulfilled") updates.set(item.job!.id, result.value);
        else pollFailures.push(item.file.name);
      });

      const nextItems = currentItems.map((item) => {
        if (!item.job) return item;
        const nextJob = updates.get(item.job.id);
        return nextJob ? { ...item, job: nextJob } : item;
      });
      batchItemsRef.current = nextItems;
      setBatchItems(nextItems);
      setBatchPollError(
        pollFailures.length
          ? `${pollFailures.length} 个任务状态暂时无法刷新，正在重试`
          : null,
      );
      if (updates.size > 0) {
        setJobs((current) => mergeJobs(current, [...updates.values()]));
      }

      const currentActive = activeJobRef.current;
      const nextActive = currentActive ? updates.get(currentActive.id) : undefined;
      if (nextActive) {
        const justCompleted = currentActive?.status !== "completed"
          && nextActive.status === "completed";
        activeJobRef.current = nextActive;
        setActiveJob(nextActive);
        if (justCompleted) {
          await loadJobResult(nextActive.id);
          if (cancelled) return;
          setTab("preview");
        }
      }

      const stillWorking = nextItems.some(
        (item) => item.job && ["queued", "running"].includes(item.job.status),
      );
      if (stillWorking) {
        timer = window.setTimeout(poll, pollFailures.length ? 1800 : 900);
      } else {
        void refreshJobs().catch((reason: Error) => setHistoryError(reason.message));
      }
    };

    timer = window.setTimeout(poll, 200);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [trackedBatchJobKey, loadJobResult, refreshJobs]);

  const primary = engines.find((engine) => engine.name === options.primary_engine);
  const fallback = engines.find((engine) => engine.name === options.fallback_engine);
  const enabledAdvancedCount = [
    options.enable_code_enrichment,
    options.enable_quality_fallback,
    options.extract_images,
    options.preserve_page_markers,
  ].filter(Boolean).length;
  const isWorking = Boolean(activeJob && ["queued", "running"].includes(activeJob.status));
  const isCompleted = activeJob?.status === "completed";
  const batchSummary = useMemo(() => summarizeBatch(batchItems), [batchItems]);
  const completedBatchJobIds = useMemo(
    () => batchItems.flatMap(
      (item) => item.job?.status === "completed" ? [item.job.id] : [],
    ),
    [batchItems],
  );
  const completedBatchJobKey = completedBatchJobIds.join(",");
  const submittableItems = batchItems.filter(
    (item) => !item.job && ["ready", "submit_failed"].includes(item.status),
  );
  const batchHasWork = batchItems.some(
    (item) => item.status === "uploading"
      || (item.job && ["queued", "running"].includes(item.job.status)),
  );
  const hasSubmittedItems = batchItems.some((item) => item.job);

  useEffect(() => {
    const downloadable = new Set(completedBatchJobKey ? completedBatchJobKey.split(",") : []);
    setSelectedBatchJobIds((current) => {
      const next = new Set([...current].filter((jobId) => downloadable.has(jobId)));
      if (
        next.size === current.size
        && [...next].every((jobId) => current.has(jobId))
      ) {
        return current;
      }
      return next;
    });
  }, [completedBatchJobKey]);
  const engineAvailable = (name: string) => {
    const status = engines.find((engine) => engine.name === name);
    return status ? status.available : true;
  };

  const patchBatchItem = useCallback((localId: string, patch: Partial<BatchItem>) => {
    setBatchItems((current) => {
      const next = current.map((item) => item.localId === localId
        ? { ...item, ...patch }
        : item);
      batchItemsRef.current = next;
      return next;
    });
  }, []);

  const acceptFiles = useCallback((candidates: File[]) => {
    if (candidates.length === 0) return;
    const current = batchItemsRef.current;
    const hasSubmitted = current.some((item) => item.job);
    const hasUnfinished = current.some(
      (item) => item.status === "uploading"
        || (item.job && ["queued", "running"].includes(item.job.status)),
    );
    if (hasSubmitted && hasUnfinished) {
      setError("当前批次已经提交，请等待完成后再开始新批次");
      return;
    }

    const base = hasSubmitted ? [] : current;
    const seen = new Set(base.map((item) => fileFingerprint(item.file)));
    const additions: BatchItem[] = [];
    const invalid: string[] = [];
    const oversized: string[] = [];
    const duplicates: string[] = [];
    let overflow = 0;

    for (const candidate of candidates) {
      const isPdf = candidate.name.toLowerCase().endsWith(".pdf")
        || candidate.type === "application/pdf";
      if (!isPdf) {
        invalid.push(candidate.name);
        continue;
      }
      if (candidate.size > runtimeConfig.max_file_size) {
        oversized.push(candidate.name);
        continue;
      }
      const fingerprint = fileFingerprint(candidate);
      if (seen.has(fingerprint)) {
        duplicates.push(candidate.name);
        continue;
      }
      if (base.length + additions.length >= runtimeConfig.max_batch_files) {
        overflow += 1;
        continue;
      }
      seen.add(fingerprint);
      additions.push({
        localId: createLocalId(),
        file: candidate,
        status: "ready",
      });
    }

    if (additions.length > 0) {
      const next = [...base, ...additions];
      batchItemsRef.current = next;
      setBatchItems(next);
      if (hasSubmitted) {
        setSelectedBatchJobIds(new Set());
        setBatchDownloadError(null);
      }
      resultRequest.current += 1;
      activeJobRef.current = null;
      setActiveJob(null);
      setResultMode(next.length > 1 ? "batch" : "job");
      setConfirmingDelete(false);
      setMarkdown("");
      setQuality(null);
      setResultError(null);
      setResultLoading(false);
    }

    const messages = [
      invalid.length ? `${invalid.length} 份不是 PDF` : "",
      oversized.length
        ? `${oversized.length} 份超过 ${formatBytes(runtimeConfig.max_file_size)}`
        : "",
      duplicates.length ? `${duplicates.length} 份重复文件已忽略` : "",
      overflow ? `最多选择 ${runtimeConfig.max_batch_files} 份，已忽略 ${overflow} 份` : "",
    ].filter(Boolean);
    setError(messages.length ? messages.join("；") : null);
  }, [runtimeConfig.max_batch_files, runtimeConfig.max_file_size]);

  const removeBatchItem = (localId: string) => {
    const current = batchItemsRef.current;
    const target = current.find((item) => item.localId === localId);
    if (!target || target.job || target.status === "uploading") return;
    const next = current.filter((item) => item.localId !== localId);
    batchItemsRef.current = next;
    setBatchItems(next);
    if (next.length < 2 && resultMode === "batch") {
      setResultMode("job");
    }
    setError(null);
  };

  const startNewBatch = () => {
    if (batchHasWork || submitting) return;
    batchItemsRef.current = [];
    setBatchItems([]);
    resultRequest.current += 1;
    activeJobRef.current = null;
    setActiveJob(null);
    setResultMode("job");
    setMarkdown("");
    setQuality(null);
    setError(null);
    setBatchPollError(null);
    setSelectedBatchJobIds(new Set());
    setBatchDownloadError(null);
    setResultError(null);
    if (fileInput.current) fileInput.current.value = "";
  };

  const submit = async () => {
    const candidates = batchItemsRef.current.filter(
      (item) => !item.job && ["ready", "submit_failed"].includes(item.status),
    );
    if (candidates.length === 0 || submitLock.current) return;
    submitLock.current = true;
    setSubmitting(true);
    setError(null);
    setBatchPollError(null);
    resultRequest.current += 1;
    const optionsSnapshot = { ...options };
    const isSingleSubmission = batchItemsRef.current.length === 1;
    let cursor = 0;
    let succeeded = 0;
    const failedNames: string[] = [];

    if (batchItemsRef.current.length > 1) {
      activeJobRef.current = null;
      setActiveJob(null);
      setResultMode("batch");
    }
    setConfirmingDelete(false);
    setMarkdown("");
    setQuality(null);
    setResultError(null);

    const worker = async () => {
      while (cursor < candidates.length) {
        const item = candidates[cursor++];
        patchBatchItem(item.localId, { status: "uploading", submitError: undefined });
        try {
          const job = await createJob(item.file, optionsSnapshot);
          succeeded += 1;
          patchBatchItem(item.localId, {
            status: "ready",
            submitError: undefined,
            job,
          });
          setJobs((current) => mergeJobs(current, [job]));
          if (isSingleSubmission) {
            activeJobRef.current = job;
            setActiveJob(job);
            setResultMode("job");
          }
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : "提交失败";
          failedNames.push(item.file.name);
          patchBatchItem(item.localId, {
            status: "submit_failed",
            submitError: message,
          });
        }
      }
    };

    try {
      const workerCount = Math.min(BATCH_UPLOAD_CONCURRENCY, candidates.length);
      await Promise.all(Array.from({ length: workerCount }, () => worker()));
      await refreshJobs().catch((reason: Error) => setHistoryError(reason.message));
      if (failedNames.length) {
        setError(
          succeeded
            ? `${succeeded} 份已加入队列，${failedNames.length} 份提交失败，可单独重试`
            : `${failedNames.length} 份文件均未提交成功，请检查错误后重试`,
        );
      }
    } finally {
      submitLock.current = false;
      setSubmitting(false);
    }
  };

  const openHistoryJob = async (job: Job) => {
    resultRequest.current += 1;
    activeJobRef.current = job;
    setActiveJob(job);
    setResultMode("job");
    setConfirmingDelete(false);
    setError(job.status === "failed" ? job.error : null);
    setMarkdown("");
    setQuality(null);
    setResultError(null);
    setResultLoading(false);
    setTab("preview");
    if (job.status === "completed") {
      await loadJobResult(job.id);
    }
  };

  const removeActiveJob = async () => {
    if (!activeJob || isWorking || deleting) return;
    const jobId = activeJob.id;
    setDeleting(true);
    try {
      await deleteJob(jobId);
      resultRequest.current += 1;
      activeJobRef.current = null;
      setActiveJob(null);
      const nextItems = batchItemsRef.current.filter((item) => item.job?.id !== jobId);
      batchItemsRef.current = nextItems;
      setBatchItems(nextItems);
      setSelectedBatchJobIds((current) => {
        const next = new Set(current);
        next.delete(jobId);
        return next;
      });
      setResultMode(nextItems.length ? "batch" : "job");
      setConfirmingDelete(false);
      setMarkdown("");
      setQuality(null);
      setError(null);
      setResultError(null);
      await refreshJobs();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  };

  const download = async (artifact: "archive" | "markdown") => {
    if (!activeJob || downloading) return;
    setDownloading(artifact);
    setResultError(null);
    const basename = activeJob.filename.replace(/\.pdf$/i, "") || "document";
    try {
      await downloadJobArtifact(
        activeJob.id,
        artifact,
        artifact === "archive" ? `${basename}-markdown.zip` : `${basename}.md`,
      );
    } catch (reason) {
      setResultError(reason instanceof Error ? reason.message : "下载失败");
    } finally {
      setDownloading(null);
    }
  };

  const setBatchJobSelected = (jobId: string, selected: boolean) => {
    setSelectedBatchJobIds((current) => {
      const next = new Set(current);
      if (selected) next.add(jobId);
      else next.delete(jobId);
      return next;
    });
    setBatchDownloadError(null);
  };

  const setAllBatchJobsSelected = (selected: boolean) => {
    setSelectedBatchJobIds(selected ? new Set(completedBatchJobIds) : new Set());
    setBatchDownloadError(null);
  };

  const downloadSelectedBatch = async () => {
    if (batchDownloading) return;
    const selectedIds = completedBatchJobIds.filter((jobId) => selectedBatchJobIds.has(jobId));
    if (selectedIds.length === 0) {
      setBatchDownloadError("请至少选择一份已完成的结果");
      return;
    }
    setBatchDownloading(true);
    setBatchDownloadError(null);
    try {
      await downloadBatchArtifacts(
        selectedIds,
        `pdf-markdown-batch-${selectedIds.length}.zip`,
      );
    } catch (reason) {
      setBatchDownloadError(reason instanceof Error ? reason.message : "批量下载失败");
    } finally {
      setBatchDownloading(false);
    }
  };

  const retryResult = () => {
    if (activeJob?.status === "completed") void loadJobResult(activeJob.id);
  };

  const moveTab = (event: React.KeyboardEvent<HTMLButtonElement>, current: ResultTab) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const tabs: ResultTab[] = ["preview", "source", "quality"];
    const index = tabs.indexOf(current);
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    setTab(tabs[next]);
    window.requestAnimationFrame(() => document.getElementById(`result-tab-${tabs[next]}`)?.focus());
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">MD</span>
          <div>
            <strong>PDF Markdown Studio</strong>
            <span>Local document workspace</span>
          </div>
        </div>
        <div className="toolbar-path" aria-label="当前位置">
          <span>工作区</span>
          <i>/</i>
          <strong>PDF 转换</strong>
        </div>
        <div className="topbar-status">
          <div className={`engine-pill ${primary?.available ? "ready" : "fallback"}`}>
            <i />
            {primary
              ? `${engineDisplayName(primary.name)}${primary.available ? " 已就绪" : " 不可用"}`
              : "正在检测引擎"}
          </div>
          <div className="privacy-pill"><i /> 文件仅在本机处理</div>
          <span className="version-pill">{__APP_VERSION_LABEL__}</span>
        </div>
      </header>

      <main className="app-main">
        <aside className="app-sidebar" aria-label="应用导航与最近任务">
          <button
            type="button"
            className="sidebar-new"
            disabled={batchHasWork || submitting}
            onClick={startNewBatch}
          >
            <span aria-hidden="true">＋</span>
            <strong>新建转换</strong>
          </button>

          <nav className="sidebar-nav" aria-label="工作区导航">
            <button
              type="button"
              className={resultMode === "job" ? "active" : ""}
              aria-current={resultMode === "job" ? "page" : undefined}
              onClick={() => setResultMode("job")}
            >
              <span aria-hidden="true">◆</span>
              <strong>转换工作台</strong>
              {batchItems.length > 0 && <em>{batchItems.length}</em>}
            </button>
            <button
              type="button"
              className={resultMode === "batch" ? "active" : ""}
              aria-current={resultMode === "batch" ? "page" : undefined}
              disabled={!hasSubmittedItems}
              onClick={() => setResultMode("batch")}
            >
              <span aria-hidden="true">▦</span>
              <strong>批次监控</strong>
              {hasSubmittedItems && <em>{batchSummary.completed}/{batchSummary.total}</em>}
            </button>
          </nav>

          <section className="sidebar-history-section">
            <div className="sidebar-section-heading">
              <span>最近任务</span>
              <button
                type="button"
                aria-label="刷新最近任务"
                title="刷新最近任务"
                onClick={() => void refreshJobs().catch((reason: Error) => setHistoryError(reason.message))}
              >↻</button>
            </div>
            {jobs.length > 0 ? (
              <div className="sidebar-history-list">
                {jobs.map((job) => (
                  <button
                    type="button"
                    key={job.id}
                    className={resultMode === "job" && activeJob?.id === job.id ? "active" : ""}
                    aria-current={resultMode === "job" && activeJob?.id === job.id ? "true" : undefined}
                    onClick={() => void openHistoryJob(job)}
                  >
                    <span className={`status-dot ${job.status}`} />
                    <span>
                      <strong title={job.filename}>{job.filename}</strong>
                      <small>{formatDate(job.created_at)} · {job.stage}</small>
                    </span>
                    <em aria-label={job.quality_score == null
                      ? `进度 ${job.progress}%`
                      : `质量分 ${job.quality_score.toFixed(0)}`}
                    >
                      {job.quality_score == null ? `${job.progress}%` : job.quality_score.toFixed(0)}
                    </em>
                  </button>
                ))}
              </div>
            ) : (
              <div className="sidebar-empty">
                <span aria-hidden="true">◎</span>
                <p>完成转换后，任务会保存在这里</p>
              </div>
            )}
            {historyError && (
              <div className="sidebar-history-error" role="status">
                暂时无法读取。<button type="button" onClick={() => void refreshJobs().catch((reason: Error) => setHistoryError(reason.message))}>重试</button>
              </div>
            )}
          </section>

          <div className="sidebar-local-note">
            <span aria-hidden="true">●</span>
            <div><strong>本地模式</strong><small>文件与模型均在本机运行</small></div>
          </div>
        </aside>

        <section className="workspace-shell">
          <div className="workspace-toolbar">
            <div>
              <p className="eyebrow">CONVERSION WORKSPACE</p>
              <h1>PDF 转 Markdown</h1>
              <span>
                {batchItems.length
                  ? `${batchStatusSummary(batchSummary)} · ${formatBytes(batchSummary.totalBytes)}`
                  : "导入 PDF，检查解析设置，然后在右侧查看结果"}
              </span>
            </div>
            <div className={`workspace-state ${batchHasWork ? "working" : batchItems.length ? "ready" : ""}`}>
              <i />
              <span>{batchHasWork ? "正在处理" : batchItems.length ? "准备就绪" : "等待文件"}</span>
            </div>
          </div>

          <section className="workspace-grid">
          <div className="control-panel panel">
            <div className="panel-heading upload-heading">
              <span className="step">01</span>
              <div>
                <h2>选择一个或多个 PDF</h2>
                <p>单份最大 {formatBytes(runtimeConfig.max_file_size)}，最多 {runtimeConfig.max_pages} 页</p>
              </div>
              <span className="batch-capability">批量 · 最多 {runtimeConfig.max_batch_files} 份</span>
            </div>

            <div
              className={`drop-zone ${dragging ? "dragging" : ""} ${batchItems.length ? "has-file" : ""} ${batchHasWork || submitting ? "locked" : ""}`}
              onDragOver={(event) => {
                event.preventDefault();
                if (!batchHasWork && !submitting) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                if (!batchHasWork && !submitting) {
                  acceptFiles(Array.from(event.dataTransfer.files));
                }
              }}
              onClick={() => {
                if (!batchHasWork && !submitting) fileInput.current?.click();
              }}
              role="button"
              tabIndex={batchHasWork || submitting ? -1 : 0}
              aria-disabled={batchHasWork || submitting}
              aria-label={batchItems.length
                ? `已选择 ${batchItems.length} 份 PDF，按回车或空格继续添加`
                : "选择一个或多个 PDF 文件"}
              onKeyDown={(event) => {
                if (
                  !batchHasWork
                  && !submitting
                  && (event.key === "Enter" || event.key === " ")
                ) {
                  event.preventDefault();
                  fileInput.current?.click();
                }
              }}
            >
              <input
                ref={fileInput}
                type="file"
                accept="application/pdf,.pdf"
                multiple
                hidden
                disabled={batchHasWork || submitting}
                onChange={(event) => {
                  acceptFiles(Array.from(event.currentTarget.files || []));
                  event.currentTarget.value = "";
                }}
              />
              <span className="upload-icon">↥</span>
              {batchHasWork ? (
                <>
                  <strong>当前批次正在转换</strong>
                  <span>任务会在本机依次处理，可在右侧查看总体进度</span>
                </>
              ) : batchItems.length ? (
                <>
                  <strong>{hasSubmittedItems ? "选择文件开始新批次" : "继续添加 PDF"}</strong>
                  <span>当前 {batchItems.length} 份 · {formatBytes(batchSummary.totalBytes)}</span>
                </>
              ) : (
                <>
                  <strong>拖入一组 PDF，或点击多选</strong>
                  <span>支持单文件与批量转换，失败任务互不影响</span>
                </>
              )}
            </div>

            {batchItems.length > 0 && (
              <div className="file-queue">
                <div className="file-queue-header">
                  <div>
                    <strong>本批次 · {batchItems.length} 份</strong>
                    <span>{formatBytes(batchSummary.totalBytes)} · {batchStatusSummary(batchSummary)}</span>
                  </div>
                  <div className="file-queue-header-actions">
                    {hasSubmittedItems && (
                      <button
                        type="button"
                        onClick={() => {
                          setResultMode("batch");
                          setError(null);
                        }}
                      >
                        批次概览
                      </button>
                    )}
                    {!batchHasWork && (
                      <button type="button" onClick={startNewBatch}>
                        {hasSubmittedItems ? "新建一批" : "清空"}
                      </button>
                    )}
                  </div>
                </div>
                <ul aria-label="批量转换文件队列">
                  {batchItems.map((item, index) => {
                    const itemStatus = getBatchItemStatus(item);
                    const progress = item.job?.progress ?? (item.status === "uploading" ? 8 : 0);
                    return (
                      <li key={item.localId} className={itemStatus.tone}>
                        <span className="file-order">{String(index + 1).padStart(2, "0")}</span>
                        <div className="file-queue-info">
                          <strong title={item.file.name}>{item.file.name}</strong>
                          <span>{formatBytes(item.file.size)} · {itemStatus.label}</span>
                          {item.submitError && <small>{item.submitError}</small>}
                          {(item.status === "uploading"
                            || (item.job && ["queued", "running"].includes(item.job.status))) && (
                            <span
                              className="queue-progress"
                              role="progressbar"
                              aria-label={`${item.file.name} 转换进度`}
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-valuenow={progress}
                            ><i style={{ width: `${progress}%` }} /></span>
                          )}
                        </div>
                        <div className="file-queue-actions">
                          {item.job && (
                            <button
                              type="button"
                              onClick={() => void openHistoryJob(item.job!)}
                            >查看</button>
                          )}
                          {!item.job && item.status !== "uploading" && (
                            <button
                              type="button"
                              className="remove-file"
                              aria-label={`移除 ${item.file.name}`}
                              onClick={() => removeBatchItem(item.localId)}
                            >移除</button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            <div className="panel-heading settings-heading">
              <span className="step">02</span>
              <div><h2>转换设置</h2><p>推荐保持自动模式</p></div>
            </div>

            <div className="settings-grid">
              <label>
                <span>主解析引擎</span>
                <select
                  value={options.primary_engine}
                  onChange={(event) => setOptions({ ...options, primary_engine: event.target.value })}
                >
                  <option value="docling" disabled={!engineAvailable("docling")}>Docling</option>
                  <option value="native" disabled={!engineAvailable("native")}>Native（轻量）</option>
                  <option value="paddleocr" disabled={!engineAvailable("paddleocr")}>PaddleOCR</option>
                </select>
                <EngineHint engine={primary} />
              </label>
              <label>
                <span>OCR 策略</span>
                <select
                  value={options.ocr_mode}
                  onChange={(event) => setOptions({
                    ...options,
                    ocr_mode: event.target.value as ConversionOptions["ocr_mode"],
                  })}
                >
                  <option value="auto">自动识别</option>
                  <option value="always">强制 OCR</option>
                  <option value="never">禁用 OCR</option>
                </select>
                <small>正常文本页不会重复 OCR</small>
              </label>
            </div>

            <details className="advanced-settings">
              <summary>
                <span>
                  <strong>高级设置</strong>
                  <small>图片、分页、代码增强与质量兜底</small>
                </span>
                <em>{enabledAdvancedCount} 项已启用</em>
              </summary>
              <div className="toggle-list">
                <Toggle
                  label="视觉代码增强（实验性）"
                  detail="适合扫描代码；原生文字课件建议关闭"
                  checked={options.enable_code_enrichment}
                  disabled={options.primary_engine !== "docling"}
                  onChange={(checked) => setOptions({ ...options, enable_code_enrichment: checked })}
                />
                <Toggle
                  label="低质量页面自动兜底"
                  detail={fallback?.available ? `使用 ${fallback.name}` : "兜底引擎尚未安装"}
                  checked={options.enable_quality_fallback}
                  disabled={fallback ? !fallback.available : false}
                  onChange={(checked) => setOptions({ ...options, enable_quality_fallback: checked })}
                />
                <Toggle
                  label="提取文档图片"
                  detail="保存到 assets 目录并使用相对路径"
                  checked={options.extract_images}
                  onChange={(checked) => setOptions({ ...options, extract_images: checked })}
                />
                <Toggle
                  label="保留分页标记"
                  detail="写入 HTML 注释，不影响 Markdown 渲染"
                  checked={options.preserve_page_markers}
                  onChange={(checked) => setOptions({ ...options, preserve_page_markers: checked })}
                />
              </div>
            </details>

            <button
              className="primary-button"
              disabled={submittableItems.length === 0 || submitting}
              onClick={submit}
            >
              {submitting
                ? `正在加入队列 · ${batchSummary.submitted}/${batchItems.length}`
                : submittableItems.some((item) => item.status === "submit_failed")
                  ? `重试失败项（${submittableItems.length}）`
                  : batchItems.length > 1
                    ? `开始批量转换（${batchItems.length}）`
                    : "开始转换"}
              <span>→</span>
            </button>
            {batchItems.length > 1 && !submitting && (
              <p className="submit-note">
                最多同时上传 {BATCH_UPLOAD_CONCURRENCY} 份，转换任务由本机安全排队
              </p>
            )}
            {error && <div className="error-banner" role="alert"><strong>处理未完成</strong>{error}</div>}
            {batchPollError && (
              <div className="warning-banner" role="status">{batchPollError}</div>
            )}
          </div>

          <div className="result-panel panel">
            <div className="result-header">
              <div>
                <p className="eyebrow">
                  {resultMode === "batch" ? "BATCH MONITOR" : "LIVE RESULT"}
                </p>
                <h2>
                  {resultMode === "batch"
                    ? `批量转换 · ${batchItems.length} 份`
                    : activeJob
                      ? activeJob.filename
                      : "转换结果"}
                </h2>
                {resultMode === "batch"
                  ? <p className="batch-header-status">{batchStatusSummary(batchSummary)}</p>
                  : quality && <QualityStatus report={quality} />}
              </div>
              {resultMode === "batch" ? (
                <div className="batch-progress-badge">
                  <strong>{batchSummary.progress.toFixed(0)}%</strong>
                  <span>总体进度</span>
                </div>
              ) : quality && <ScoreBadge score={quality.score} passed={quality.passed} />}
            </div>

            {resultMode === "batch" && batchItems.length > 0 ? (
              <BatchOverview
                items={batchItems}
                summary={batchSummary}
                submitting={submitting}
                onOpenJob={openHistoryJob}
                onRetry={() => void submit()}
                onNewBatch={startNewBatch}
                selectedJobIds={selectedBatchJobIds}
                downloading={batchDownloading}
                downloadError={batchDownloadError}
                onSelectionChange={setBatchJobSelected}
                onSelectAll={setAllBatchJobsSelected}
                onDownload={() => void downloadSelectedBatch()}
              />
            ) : activeJob && isWorking ? (
              <ProgressView job={activeJob} />
            ) : isCompleted && activeJob ? (
              <>
                <div className="result-navigation">
                  <div className="result-tabs" role="tablist">
                    {(["preview", "source", "quality"] as ResultTab[]).map((item) => (
                      <button
                        key={item}
                        id={`result-tab-${item}`}
                        role="tab"
                        aria-selected={tab === item}
                        aria-controls="result-panel"
                        tabIndex={tab === item ? 0 : -1}
                        className={tab === item ? "active" : ""}
                        onClick={() => setTab(item)}
                        onKeyDown={(event) => moveTab(event, item)}
                      >
                        {{ preview: "预览", source: "源码", quality: "质量" }[item]}
                      </button>
                    ))}
                  </div>
                  <div className="result-actions">
                    <button className="download-link" onClick={() => void download("archive")} disabled={Boolean(downloading)}>
                      {downloading === "archive" ? "正在下载…" : "下载结果包 ↓"}
                    </button>
                    {confirmingDelete ? (
                      <span className="delete-confirmation">
                        <span>删除全部本地结果？</span>
                        <button className="confirm-delete" disabled={deleting} onClick={() => void removeActiveJob()}>{deleting ? "删除中…" : "确认删除"}</button>
                        <button disabled={deleting} onClick={() => setConfirmingDelete(false)}>取消</button>
                      </span>
                    ) : (
                      <button className="delete-link" onClick={() => setConfirmingDelete(true)}>删除</button>
                    )}
                  </div>
                </div>
                {resultError && (
                  <div className="result-error" role="alert">
                    <span>{resultError}</span>
                    <button onClick={retryResult} disabled={resultLoading}>重新读取</button>
                  </div>
                )}
                <div
                  className="result-body"
                  id="result-panel"
                  role="tabpanel"
                  aria-labelledby={`result-tab-${tab}`}
                  aria-busy={resultLoading}
                  tabIndex={0}
                >
                  {tab === "preview" && (
                    resultLoading && !markdown
                      ? <ResultLoading />
                      : markdown
                        ? <MarkdownPreview markdown={markdown} jobId={activeJob.id} />
                        : <EmptyResult kind="preview" onRetry={retryResult} />
                  )}
                  {tab === "source" && (
                    <SourceView
                      markdown={markdown}
                      loading={resultLoading}
                      wrap={wrapSource}
                      onWrapChange={setWrapSource}
                      onDownload={() => void download("markdown")}
                      downloading={downloading === "markdown"}
                      onRetry={retryResult}
                    />
                  )}
                  {tab === "quality" && (
                    resultLoading && !quality
                      ? <ResultLoading />
                      : quality
                        ? <QualityView report={quality} />
                        : <EmptyResult kind="quality" onRetry={retryResult} />
                  )}
                </div>
              </>
            ) : activeJob?.status === "failed" ? (
              <div className="empty-state">
                <span>!</span><h3>转换失败</h3><p>{activeJob.error}</p>
                {confirmingDelete ? (
                  <div className="delete-failed-actions">
                    <button className="secondary-button" disabled={deleting} onClick={() => void removeActiveJob()}>{deleting ? "删除中…" : "确认删除"}</button>
                    <button className="secondary-button" disabled={deleting} onClick={() => setConfirmingDelete(false)}>取消</button>
                  </div>
                ) : (
                  <button className="secondary-button" onClick={() => setConfirmingDelete(true)}>删除此任务</button>
                )}
              </div>
            ) : (
              <div className="empty-state">
                <span>⌁</span>
                <h3>结果会在这里出现</h3>
                <p>上传 PDF 后可以预览 Markdown、检查逐页质量并下载完整结果包。</p>
              </div>
            )}
          </div>
          </section>
        </section>
      </main>

      <footer className="statusbar">
        <span>本地服务已连接</span>
        <span>最多 {runtimeConfig.max_batch_files} 份 / 批</span>
        <span>Markdown · JSON · Quality Report</span>
      </footer>
    </div>
  );
}

function BatchOverview({
  items,
  summary,
  submitting,
  onOpenJob,
  onRetry,
  onNewBatch,
  selectedJobIds,
  downloading,
  downloadError,
  onSelectionChange,
  onSelectAll,
  onDownload,
}: {
  items: BatchItem[];
  summary: BatchSummary;
  submitting: boolean;
  onOpenJob: (job: Job) => Promise<void>;
  onRetry: () => void;
  onNewBatch: () => void;
  selectedJobIds: Set<string>;
  downloading: boolean;
  downloadError: string | null;
  onSelectionChange: (jobId: string, selected: boolean) => void;
  onSelectAll: (selected: boolean) => void;
  onDownload: () => void;
}) {
  const hasWork = summary.uploading + summary.queued + summary.running > 0;
  const hasSubmitFailures = items.some((item) => item.status === "submit_failed");
  const hasSubmitted = summary.submitted > 0;
  const completedJobs = items.flatMap(
    (item) => item.job?.status === "completed" ? [item.job] : [],
  );
  const selectedCount = completedJobs.filter((job) => selectedJobIds.has(job.id)).length;
  const allSelected = completedJobs.length > 0 && selectedCount === completedJobs.length;
  const partiallySelected = selectedCount > 0 && !allSelected;
  const selectAllRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = partiallySelected;
  }, [partiallySelected]);

  return (
    <div className="batch-overview">
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        已完成 {summary.completed} / {summary.total} 份，失败 {summary.failed} 份，
        已选择下载 {selectedCount} 份
      </p>
      <div className="batch-summary-grid">
        <div><span>本批次</span><strong>{summary.total}</strong><small>份 PDF</small></div>
        <div><span>已完成</span><strong>{summary.completed}</strong><small>可查看结果</small></div>
        <div><span>处理中</span><strong>{summary.uploading + summary.running}</strong><small>{summary.queued} 份排队</small></div>
        <div className={summary.failed ? "has-failures" : ""}>
          <span>失败</span><strong>{summary.failed}</strong><small>互不影响</small>
        </div>
      </div>

      <div className="batch-overall">
        <div>
          <strong>{batchStatusSummary(summary)}</strong>
          <span>
            {hasWork
              ? "任务正在本机后台依次处理"
              : hasSubmitted
                ? "本批次处理结束，可逐项查看或合并下载"
                : "在左侧确认转换设置后开始提交"}
          </span>
        </div>
        <div
          className="batch-overall-track"
          role="progressbar"
          aria-label="批量转换总体进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(summary.progress)}
        ><i style={{ width: `${summary.progress}%` }} /></div>
      </div>

      {completedJobs.length > 0 && (
        <div className="batch-download-toolbar">
          <label className="batch-selection">
            <input
              ref={selectAllRef}
              type="checkbox"
              checked={allSelected}
              disabled={downloading}
              onChange={(event) => onSelectAll(event.target.checked)}
            />
            <span>
              <strong>全选可下载结果</strong>
              <small>已选 {selectedCount} / {completedJobs.length} 份</small>
            </span>
          </label>
          <button
            type="button"
            disabled={selectedCount === 0 || downloading}
            aria-busy={downloading}
            onClick={onDownload}
          >
            {downloading
              ? "正在合并打包…"
              : allSelected
                ? `下载全部结果包（${selectedCount}）`
                : selectedCount > 0
                  ? `下载所选结果包（${selectedCount}）`
                  : "选择后批量下载"}
          </button>
        </div>
      )}
      {downloadError && <div className="batch-download-error" role="alert">{downloadError}</div>}

      <div className="batch-task-list" role="list" aria-label="批量任务状态">
        {items.map((item, index) => {
          const status = getBatchItemStatus(item);
          const downloadable = item.job?.status === "completed";
          const selected = Boolean(item.job && selectedJobIds.has(item.job.id));
          return (
            <div className={`batch-task-row ${status.tone}`} role="listitem" key={item.localId}>
              <label className={`batch-task-checkbox ${downloadable ? "" : "disabled"}`}>
                <input
                  type="checkbox"
                  checked={selected}
                  disabled={!downloadable || downloading}
                  aria-label={downloadable
                    ? `选择 ${item.file.name} 下载`
                    : `${item.file.name} 尚不可下载`}
                  onChange={(event) => {
                    if (item.job) onSelectionChange(item.job.id, event.target.checked);
                  }}
                />
              </label>
              <span className="task-index">{String(index + 1).padStart(2, "0")}</span>
              <span className={`status-dot ${status.tone}`} aria-hidden="true" />
              <div>
                <strong title={item.file.name}>{item.file.name}</strong>
                <small>
                  {status.label}
                  {item.job?.quality_score != null
                    ? ` · 质量 ${item.job.quality_score.toFixed(0)}`
                    : ""}
                </small>
                {item.job && ["queued", "running"].includes(item.job.status) && (
                  <span
                    className="batch-row-progress"
                    role="progressbar"
                    aria-label={`${item.file.name} 转换进度`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={item.job.progress}
                  >
                    <i><b style={{ width: `${item.job.progress}%` }} /></i>
                    <em>{item.job.progress}%</em>
                  </span>
                )}
                {(item.submitError || item.job?.error) && (
                  <p>{item.submitError || item.job?.error}</p>
                )}
              </div>
              {item.job && (
                <button type="button" onClick={() => void onOpenJob(item.job!)}>
                  {item.job.status === "completed"
                    ? "查看结果"
                    : item.job.status === "failed"
                      ? "查看错误"
                      : "查看进度"}
                </button>
              )}
            </div>
          );
        })}
      </div>

      {(hasSubmitFailures || (hasSubmitted && !hasWork)) && (
        <div className="batch-overview-actions">
          {hasSubmitFailures && (
            <button type="button" disabled={submitting} onClick={onRetry}>
              {submitting ? "正在重试…" : "仅重试提交失败项"}
            </button>
          )}
          {hasSubmitted && !hasWork && (
            <button type="button" className="secondary" onClick={onNewBatch}>新建一批</button>
          )}
        </div>
      )}
    </div>
  );
}

function EngineHint({ engine }: { engine?: EngineStatus }) {
  if (!engine) return <small>正在检测引擎…</small>;
  return <small className={engine.available ? "available" : "unavailable"}>
    {engine.available ? "● 已就绪" : "○ 未安装，将使用 Native"}
  </small>;
}

function Toggle({
  label, detail, checked, disabled = false, onChange,
}: {
  label: string;
  detail: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={`toggle-row ${disabled ? "disabled" : ""}`}>
      <span><strong>{label}</strong><small>{detail}</small></span>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <i />
    </label>
  );
}

function ScoreBadge({ score, passed }: { score: number; passed: boolean }) {
  return (
    <div className={`score-badge ${passed ? "passed" : "attention"}`}>
      <strong>{score.toFixed(0)}</strong><span>质量分</span>
    </div>
  );
}

function QualityStatus({ report }: { report: QualityReport }) {
  const warningCount = report.issues.filter((issue) => issue.severity === "warning").length;
  const errorCount = report.issues.filter((issue) => issue.severity === "error").length;
  const degraded = report.issues.some((issue) => [
    "primary_engine_unavailable",
    "primary_engine_failed",
    "limited_layout_validation",
  ].includes(issue.code));
  const needsReview = !report.passed || degraded || warningCount > 0 || errorCount > 0;
  return (
    <div className={`quality-status ${needsReview ? "review" : "clear"}`}>
      <span>{degraded ? "已降级" : needsReview ? "建议复核" : "质量门控通过"}</span>
      <small>{report.primary_engine} · {warningCount + errorCount} 项需注意</small>
    </div>
  );
}

function ProgressView({ job }: { job: Job }) {
  return (
    <div className="progress-view" aria-live="polite">
      <div className="progress-orbit"><span>{job.progress}%</span></div>
      <h3>{job.stage}</h3>
      <p>解析工作在本机后台运行，可以安全地停留在此页面。</p>
      <div
        className="progress-track"
        role="progressbar"
        aria-label="转换进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={job.progress}
      ><i style={{ width: `${job.progress}%` }} /></div>
      <div className="progress-stages"><span>检查文件</span><span>结构解析</span><span>质量验证</span><span>生成结果</span></div>
    </div>
  );
}

function ResultLoading() {
  return <div className="inline-state" role="status"><span className="state-spinner" />正在读取转换结果…</div>;
}

function EmptyResult({ kind, onRetry }: { kind: "preview" | "quality"; onRetry: () => void }) {
  return (
    <div className="inline-state">
      <strong>{kind === "preview" ? "这份 PDF 没有可预览的文本" : "质量报告暂未读取成功"}</strong>
      <span>{kind === "preview" ? "仍可下载结果包检查结构化 JSON 与图片。" : "转换结果不会因此丢失。"}</span>
      <button className="secondary-button" onClick={onRetry}>重新读取</button>
    </div>
  );
}

function SourceView({
  markdown,
  loading,
  wrap,
  onWrapChange,
  onDownload,
  downloading,
  onRetry,
}: {
  markdown: string;
  loading: boolean;
  wrap: boolean;
  onWrapChange: (value: boolean) => void;
  onDownload: () => void;
  downloading: boolean;
  onRetry: () => void;
}) {
  const [copied, setCopied] = useState(false);
  if (loading && !markdown) return <ResultLoading />;
  if (!markdown) return <EmptyResult kind="preview" onRetry={onRetry} />;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="source-pane">
      <div className="source-toolbar">
        <span>{markdown.length.toLocaleString("zh-CN")} 字符</span>
        <label><input type="checkbox" checked={wrap} onChange={(event) => onWrapChange(event.target.checked)} /> 自动换行</label>
        <button onClick={() => void copy()}>{copied ? "已复制" : "复制"}</button>
        <button onClick={onDownload} disabled={downloading}>{downloading ? "下载中…" : "下载 .md"}</button>
      </div>
      <pre className={`source-view ${wrap ? "wrap" : ""}`}>{markdown}</pre>
    </div>
  );
}

function stripYamlFrontMatter(markdown: string) {
  return markdown.replace(/^\uFEFF?---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, "");
}

function MarkdownPreview({ markdown, jobId }: { markdown: string; jobId: string }) {
  const { previewMarkdown, truncated } = useMemo(() => {
    const content = stripYamlFrontMatter(markdown);
    const limit = 250_000;
    return {
      previewMarkdown: content.length > limit ? content.slice(0, limit) : content,
      truncated: content.length > limit,
    };
  }, [markdown]);
  const components = useMemo(() => ({
    img: ({ src = "", alt = "" }: React.ComponentPropsWithoutRef<"img">) => {
      const clean = typeof src === "string" ? src.replace(/^\.\//, "") : "";
      if (!clean.startsWith("assets/")) {
        return <span className="blocked-image">已阻止自动加载外部图片：{alt || clean}</span>;
      }
      const target = `/api/jobs/${jobId}/assets/${clean.slice("assets/".length)}`;
      return <img src={target} alt={alt} loading="lazy" />;
    },
    a: ({ href = "", children }: React.ComponentPropsWithoutRef<"a">) => (
      <a href={href} target="_blank" rel="noreferrer">{children}</a>
    ),
  }), [jobId]);

  return (
    <article className="markdown-preview">
      {truncated && (
        <div className="preview-notice">
          文档较长，在线预览仅显示前 25 万字符；完整内容可在“源码”页查看或下载。
        </div>
      )}
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, rehypeSanitize]}
        components={components}
      >
        {previewMarkdown}
      </ReactMarkdown>
    </article>
  );
}

function QualityView({ report }: { report: QualityReport }) {
  const [selectedPage, setSelectedPage] = useState<number | null>(null);
  const issueGroups = useMemo(() => ({
    error: report.issues.filter((issue) => issue.severity === "error" && (!selectedPage || !issue.page || issue.page === selectedPage)),
    warning: report.issues.filter((issue) => issue.severity === "warning" && (!selectedPage || !issue.page || issue.page === selectedPage)),
    info: report.issues.filter((issue) => issue.severity === "info" && (!selectedPage || !issue.page || issue.page === selectedPage)),
  }), [report, selectedPage]);
  const displayedIssues = [...issueGroups.error, ...issueGroups.warning, ...issueGroups.info];
  const metricItems = [
    ["代码块", report.metrics.code_block_count],
    ["逻辑代码组", report.metrics.logical_code_block_count],
    ["已验证代码", report.metrics.checked_code_block_count],
    ["异常代码", report.metrics.invalid_code_block_count],
    ["未标语言", report.metrics.untyped_code_block_count],
    ["语言纠偏", report.metrics.reclassified_language_code_block_count],
    [
      "代码质量",
      typeof report.metrics.code_quality_score === "number"
        ? `${report.metrics.code_quality_score.toFixed(0)}%`
        : undefined,
    ],
    ["表格", report.metrics.table_count],
    ["自动修复表格", report.metrics.repaired_table_count],
    ["标题", report.metrics.heading_count],
    ["列表项", report.metrics.list_item_count],
    ["图片", report.metrics.image_count],
    ["提取字符", report.metrics.extracted_chars],
    ["最低 10% 页均分", report.metrics.low_decile_score],
  ].filter((item): item is [string, string | number | boolean] => item[1] !== undefined);
  return (
    <div className="quality-view">
      <div className="quality-summary">
        <ScoreBadge score={report.score} passed={report.passed} />
        <dl>
          <div><dt>主引擎</dt><dd>{report.primary_engine}</dd></div>
          <div><dt>兜底引擎</dt><dd>{report.fallback_engine || "未使用"}</dd></div>
          <div><dt>兜底页面</dt><dd>{report.fallback_pages.length ? report.fallback_pages.join(", ") : "无"}</dd></div>
          <div><dt>问题数量</dt><dd>{report.issues.length}</dd></div>
        </dl>
      </div>
      {metricItems.length > 0 && (
        <div className="metric-grid">
          {metricItems.map(([label, value]) => <div key={label}><span>{label}</span><strong>{String(value)}</strong></div>)}
        </div>
      )}
      <div className="page-scores">
        <button
          className={selectedPage === null ? "selected" : ""}
          onClick={() => setSelectedPage(null)}
        >全部页面</button>
        {report.pages.map((page) => (
          <button
            key={page.page}
            className={selectedPage === page.page ? "selected" : ""}
            title={`提取 ${page.extracted_chars} 字符；重复率 ${(page.duplicate_ratio * 100).toFixed(1)}%`}
            onClick={() => setSelectedPage(page.page)}
          >
            <span>第 {page.page} 页</span>
            <i><b style={{ width: `${page.score}%` }} /></i>
            <strong>{page.score.toFixed(0)}</strong>
          </button>
        ))}
      </div>
      <div className="issue-list">
        {displayedIssues.length === 0 && <div className="all-clear">✓ 当前范围没有需要注意的质量问题</div>}
        {displayedIssues.map((issue, index) => (
          <IssueRow key={`${issue.code}-${issue.page}-${index}`} issue={issue} />
        ))}
      </div>
    </div>
  );
}

function IssueRow({ issue }: { issue: QualityIssue }) {
  return (
    <div className={`issue-row ${issue.severity}`}>
      <span>{issue.severity === "error" ? "×" : issue.severity === "warning" ? "!" : "i"}</span>
      <div><strong>{issue.message}</strong><small>{issue.page ? `第 ${issue.page} 页 · ` : ""}{issue.code}</small></div>
    </div>
  );
}

function summarizeBatch(items: BatchItem[]): BatchSummary {
  const summary: BatchSummary = {
    total: items.length,
    totalBytes: items.reduce((total, item) => total + item.file.size, 0),
    submitted: 0,
    ready: 0,
    uploading: 0,
    queued: 0,
    running: 0,
    completed: 0,
    failed: 0,
    progress: 0,
  };
  let progressTotal = 0;
  for (const item of items) {
    if (item.job) {
      summary.submitted += 1;
      if (item.job.status === "queued") summary.queued += 1;
      if (item.job.status === "running") summary.running += 1;
      if (item.job.status === "completed") summary.completed += 1;
      if (item.job.status === "failed") summary.failed += 1;
      progressTotal += ["completed", "failed"].includes(item.job.status)
        ? 100
        : Math.max(0, Math.min(100, item.job.progress));
    } else if (item.status === "uploading") {
      summary.uploading += 1;
      progressTotal += 8;
    } else if (item.status === "submit_failed") {
      summary.failed += 1;
      progressTotal += 100;
    } else {
      summary.ready += 1;
    }
  }
  summary.progress = summary.total ? progressTotal / summary.total : 0;
  return summary;
}

function batchStatusSummary(summary: BatchSummary): string {
  if (!summary.total) return "等待选择文件";
  if (!summary.submitted && !summary.uploading && !summary.failed) {
    return `${summary.total} 份待提交`;
  }
  if (summary.uploading) {
    return `正在上传 ${summary.uploading} 份 · 已加入队列 ${summary.submitted} 份`;
  }
  if (summary.running || summary.queued) {
    return `完成 ${summary.completed}/${summary.total} · 转换中 ${summary.running} · 排队 ${summary.queued}`;
  }
  if (summary.completed + summary.failed === summary.total) {
    return summary.failed
      ? `完成 ${summary.completed} 份 · 失败 ${summary.failed} 份`
      : `${summary.completed} 份全部完成`;
  }
  return `已加入队列 ${summary.submitted}/${summary.total}`;
}

function getBatchItemStatus(item: BatchItem): { label: string; tone: string } {
  if (item.job) {
    if (item.job.status === "queued") return { label: "等待处理", tone: "queued" };
    if (item.job.status === "running") {
      return {
        label: `${item.job.stage} · ${item.job.progress}%`,
        tone: "running",
      };
    }
    if (item.job.status === "completed") return { label: "转换完成", tone: "completed" };
    return { label: "转换失败", tone: "failed" };
  }
  if (item.status === "uploading") return { label: "正在上传", tone: "running" };
  if (item.status === "submit_failed") return { label: "提交失败", tone: "failed" };
  return { label: "待提交", tone: "ready" };
}

function fileFingerprint(file: File): string {
  return `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
}

function createLocalId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `file-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function mergeJobs(current: Job[], updates: Job[]): Job[] {
  const byId = new Map(current.map((job) => [job.id, job]));
  for (const job of updates) byId.set(job.id, job);
  return [...byId.values()]
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
    .slice(0, 100);
}

function engineDisplayName(name: string): string {
  return {
    docling: "Docling",
    native: "Native",
    paddleocr: "PaddleOCR",
  }[name] || name;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  const megabytes = bytes / 1024 / 1024;
  return `${Number.isInteger(megabytes) ? megabytes.toFixed(0) : megabytes.toFixed(1)} MB`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}
