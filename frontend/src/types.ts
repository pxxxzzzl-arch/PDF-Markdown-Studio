export type JobStatus = "queued" | "running" | "completed" | "failed";

export interface ConversionOptions {
  primary_engine: string;
  fallback_engine: string;
  ocr_mode: "auto" | "always" | "never";
  enable_code_enrichment: boolean;
  extract_images: boolean;
  preserve_page_markers: boolean;
  include_front_matter: boolean;
  enable_quality_fallback: boolean;
  minimum_quality_score: number;
}

export interface RuntimeConfig {
  max_file_size: number;
  max_pages: number;
  max_batch_files: number;
  job_ttl_hours: number;
}

export interface Job {
  id: string;
  status: JobStatus;
  filename: string;
  progress: number;
  stage: string;
  created_at: string;
  updated_at: string;
  error: string | null;
  quality_score: number | null;
  options: ConversionOptions;
}

export interface EngineStatus {
  name: string;
  available: boolean;
  role: string;
  detail: string | null;
}

export interface QualityIssue {
  code: string;
  severity: "info" | "warning" | "error";
  message: string;
  page: number | null;
  block_id: string | null;
}

export interface PageQuality {
  page: number;
  score: number;
  extracted_chars: number;
  replacement_ratio: number;
  duplicate_ratio: number;
  needs_fallback: boolean;
}

export interface QualityReport {
  score: number;
  passed: boolean;
  primary_engine: string;
  fallback_engine: string | null;
  fallback_pages: number[];
  issues: QualityIssue[];
  pages: PageQuality[];
  metrics: Record<string, string | number | boolean>;
}
