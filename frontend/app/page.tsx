"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import AuthScreen from "./AuthScreen";
import { accessToken, supabase } from "../lib/supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL || (
  process.env.NODE_ENV === "production"
    ? "https://talentflow-backend-e5nx.onrender.com"
    : "http://localhost:8000"
);

type Evidence = { requirement: string; matched: boolean; evidence: string; confidence: number };
type Step = { node: string; status: string };
type InterviewQuestion = { type: string; question: string; signal: string };
type InterviewKit = {
  summary: string;
  questions: InterviewQuestion[];
  rubric: { criterion: string; weight: number }[];
  source: string;
};
type Application = {
  id: string; job_id: string; status: string; resume_filename?: string;
  candidate: { name: string; email: string };
  screening: { final_score: number; raw_score?: number; confidence?: number; recommendation: string; evidence: Evidence[]; experience_years: number; interview_kit?: InterviewKit; routing?: { decision: string; reasons: string[] } };
  pipeline: Step[];
};
type Job = {
  id: string; title: string; department: string; location: string; description: string; status: string; applications_count: number;
  requirements?: { required_skills?: string[]; preferred_skills?: string[]; minimum_experience?: number; approval?: { status: string; note?: string }; shortlist_approval?: { status: string; application_ids?: string[] } };
};
type Interview = { id: string; application_id: string; start_at: string; status: string; meeting_url: string; reschedule_count?: number; outcome?: string };
type PublicSchedule = { candidate_name: string; job_title: string; timezone: string; duration_minutes: number; expires_at: string; mode: "schedule" | "reschedule"; interview?: Interview; slots: { start_at: string; duration_minutes: number }[] };
type Dashboard = {
  metrics: { open_jobs: number; candidates: number; awaiting_review: number; interviews: number };
  applications: Application[]; jobs: Job[]; interviews?: Interview[];
};
type ReviewDecision = "MANUAL_REVIEW" | "REJECT" | "INTERVIEW" | "ARCHIVE";
type ProgressState = { value: number; label: string; detail: string };
type PendingAction = { key: string; label: string };
type BatchResult = {
  batch_id: string; status: "PROCESSING" | "COMPLETED" | "PARTIAL" | "FAILED";
  total: number; processed: number; completed: number; failed: number; skipped: number;
  items: { id: string; filename: string; status: string; application?: Application; error?: string }[];
};
type Approval = {
  id: string; type: "CRITERIA" | "EVIDENCE" | "SHORTLIST" | "ESCALATION"; status: string; job_id?: string; application_id?: string;
  resource_id: string; title: string; summary: string; payload: Record<string, unknown>; created_at: string;
  requested_by_id?: string | null; requested_by_email?: string | null;
  decided_at?: string | null; resolution?: Record<string, unknown>;
};
type MailSandbox = {
  enabled: boolean; base_email: string; max_alias: number; sample_aliases: string[]; delivery_note: string;
};
type AuditLog = {
  id: string; application_id: string | null; action: string;
  actor_id: string | null; actor_email: string | null;
  metadata: Record<string, unknown>; created_at: string;
};
type Integration = {
  provider: string; status: string; account_email: string; scopes: string[]; expires_at: string | null;
};

const emptyDashboard: Dashboard = {
  metrics: { open_jobs: 0, candidates: 0, awaiting_review: 0, interviews: 0 },
  applications: [], jobs: [], interviews: [],
};

function Icon({ name }: { name: string }) {
  const paths: Record<string, React.ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></>,
    briefcase: <><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18"/></>,
    users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></>,
    calendar: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/></>,
    spark: <><path d="m12 3-1.7 4.7L6 9.5l4.3 1.8L12 16l1.7-4.7L18 9.5l-4.3-1.8L12 3Z"/><path d="m5 15-.8 2.2L2 18l2.2.8L5 21l.8-2.2L8 18l-2.2-.8L5 15Z"/></>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    upload: <><path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 15v5h16v-5"/></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    arrow: <path d="m9 18 6-6-6-6"/>, check: <path d="m5 12 4 4L19 6"/>, plus: <path d="M12 5v14M5 12h14"/>,
  };
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

async function request<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const token = await accessToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "Không thể kết nối với API");
  }
  return response.json();
}

async function download(path: string, filename: string) {
  const token = await accessToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_URL}${path}`, { headers });
  if (!response.ok) throw new Error("Không thể tải báo cáo");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

const initials = (name: string) => name.split(/\s+/).slice(-2).map(part => part[0]).join("").toUpperCase();
const statusLabel = (status: string) => ({
  PROCESSING: "Đang screening",
  SCREENING_FAILED: "Screening lỗi",
  WAITING_REVIEW: "Chờ duyệt",
  SHORTLISTED: "Shortlist",
  REVIEWED: "Đã xem xét",
  REJECTED: "Đã từ chối",
  ARCHIVED: "Lưu trữ",
  INTERVIEW_PENDING: "Chờ đặt lịch",
  INTERVIEW_SCHEDULED: "Đã đặt lịch",
} as Record<string, string>)[status] || status;
const statusTone = (status: string) => status === "WAITING_REVIEW" ? "review" : status === "REJECTED" ? "rejected" : status === "ARCHIVED" ? "archived" : status.startsWith("INTERVIEW") ? "interview" : "manual";
const dateLabel = (value: string) => new Intl.DateTimeFormat("vi-VN", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
const fullDateLabel = (value: string) => new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));

const AUDIT_LABELS: Record<string, string> = {
  JOB_CREATED: "Tạo vị trí tuyển dụng",
  CV_EXTRACTED: "Trích xuất nội dung CV",
  CV_DUPLICATE_SKIPPED: "Bỏ qua CV trùng",
  SCREENING_QUEUED: "Đưa CV vào hàng đợi chấm điểm",
  SCREENING_COMPLETED: "Chấm điểm CV hoàn tất",
  SCREENING_RETRY_REQUESTED: "Yêu cầu chấm điểm lại",
  RESCREEN_QUEUED: "Đưa vào hàng đợi chấm lại",
  INTERVIEW_KIT_GENERATED: "Sinh bộ câu hỏi phỏng vấn",
  RECRUITER_REVIEWED: "Recruiter ra quyết định",
  CRITERIA_VERSION_CREATED: "Tạo phiên bản tiêu chí mới",
  CRITERIA_APPROVED: "Duyệt tiêu chí tuyển dụng",
  CRITERIA_REVISION_REQUESTED: "Yêu cầu sửa lại tiêu chí",
  SHORTLIST_APPROVED: "Duyệt danh sách shortlist",
  SHORTLIST_REPORT_EXPORTED: "Xuất báo cáo shortlist",
  SHORTLIST_TRIGGER_UPDATED: "Cập nhật điều kiện tự tạo shortlist",
  SCHEDULING_INVITATION_CREATED: "Gửi lời mời chọn lịch phỏng vấn",
  INTERVIEW_SLOT_HELD: "Ứng viên giữ khung giờ phỏng vấn",
  INTERVIEW_BOOKING_REQUESTED: "Yêu cầu đặt lịch phỏng vấn",
  INTERVIEW_CONFIRMED_BY_HR: "HR xác nhận lịch phỏng vấn",
  INTERVIEW_RESCHEDULE_REQUESTED: "Yêu cầu đổi lịch phỏng vấn",
  CANDIDATE_RESCHEDULE_REQUESTED: "Ứng viên xin đổi lịch",
  INTERVIEW_CANCEL_REQUESTED: "Yêu cầu huỷ lịch phỏng vấn",
  INTERVIEW_CANCELLED_BY_PROVIDER: "Lịch bị huỷ từ hệ thống lịch",
  INTERVIEW_NO_SHOW: "Ứng viên không đến phỏng vấn",
  INTERVIEW_POLICY_UPDATED: "Cập nhật chính sách phỏng vấn",
  SCORECARD_SUBMITTED: "Nộp phiếu đánh giá phỏng vấn",
  EMAIL_BOUNCED: "Email gửi không thành công",
  MAIL_SANDBOX_UPDATED: "Cập nhật cấu hình Mail Sandbox",
  MAIL_SANDBOX_TEST_QUEUED: "Gửi email thử nghiệm",
  MAIL_SANDBOX_RECIPIENT_BLOCKED: "Chặn email ngoài whitelist",
  MAIL_SANDBOX_REQUIRED: "Chặn gửi vì chưa bật Mail Sandbox",
  OUTBOX_POLICY_BLOCKED: "Chặn bởi kill switch của tổ chức",
  CANDIDATE_DATA_EXPORTED: "Xuất dữ liệu ứng viên",
  CANDIDATE_DATA_DELETED: "Xoá dữ liệu ứng viên",
  RECRUITMENT_DATA_CLEARED: "Xoá toàn bộ dữ liệu tuyển dụng",
  RETENTION_SWEEP_COMPLETED: "Dọn dữ liệu quá hạn lưu trữ",
  INTEGRATION_REVOKED: "Ngắt kết nối dịch vụ ngoài",
  SOURCE_CONNECTOR_CREATED: "Tạo kênh nhận CV tự động",
  SOURCE_CONNECTOR_REVOKED: "Thu hồi kênh nhận CV",
  SOURCE_CV_INGESTED: "Nhận CV từ kênh ngoài",
  TENANT_POLICY_UPDATED: "Cập nhật chính sách tổ chức",
  MODEL_POLICY_EVALUATED: "Đánh giá cấu hình model AI",
  MODEL_POLICY_ACTIVATED: "Kích hoạt cấu hình model AI",
  OPERATIONAL_SLO_EVALUATED: "Đánh giá cảnh báo vận hành",
  OPERATIONAL_SLO_POLICY_UPDATED: "Cập nhật ngưỡng cảnh báo",
  RELEASE_GATE_EVALUATED: "Đánh giá cổng phát hành",
  RELEASE_PROMOTED: "Phát hành phiên bản mới",
};
const EVIDENCE_REASONS: Record<string, string> = {
  LOW_CONFIDENCE: "Độ tin cậy thấp",
  BORDERLINE_SCORE: "Điểm nằm sát ngưỡng",
  SCORE_EVIDENCE_MISMATCH: "Điểm và bằng chứng không khớp",
};
const DECISION_LABELS: Record<string, string> = {
  INTERVIEW: "Mời phỏng vấn", REJECT: "Từ chối", ARCHIVE: "Lưu trữ", MANUAL_REVIEW: "Đánh dấu xem xét",
  APPROVE: "Phê duyệt",
};
const auditLabel = (action: string) => AUDIT_LABELS[action] || action;
/** Stand-in identity while login is off: the backend cannot tell who acted, so local actions all show this name. */
const LOCAL_ACTOR_ID = "00000000-0000-0000-0000-000000000001";
const LOCAL_ACTOR_NAME = "Vinh Nguyễn";
const actorLabel = (entry: AuditLog) =>
  entry.actor_email?.trim() ? entry.actor_email.trim()
  : entry.actor_id === LOCAL_ACTOR_ID ? LOCAL_ACTOR_NAME
  : entry.actor_id ? entry.actor_id
  : "";
const auditGroup = (action: string): "decision" | "screening" | "interview" | "system" => {
  if (["RECRUITER_REVIEWED", "CRITERIA_APPROVED", "CRITERIA_REVISION_REQUESTED", "SHORTLIST_APPROVED", "CRITERIA_VERSION_CREATED", "SHORTLIST_TRIGGER_UPDATED", "JOB_CREATED"].includes(action)) return "decision";
  if (action.startsWith("SCREENING") || action.startsWith("CV_") || action === "RESCREEN_QUEUED" || action === "INTERVIEW_KIT_GENERATED") return "screening";
  if (action.startsWith("INTERVIEW") || action.startsWith("SCHEDULING") || action.startsWith("CANDIDATE_RESCHEDULE") || action === "SCORECARD_SUBMITTED") return "interview";
  return "system";
};
const AUDIT_GROUP_LABELS: Record<string, string> = {
  decision: "Quyết định", screening: "Xử lý CV", interview: "Phỏng vấn", system: "Hệ thống",
};
function auditDetail(entry: AuditLog): string {
  const meta = entry.metadata || {};
  const parts: string[] = [];
  if (typeof meta.decision === "string") parts.push(DECISION_LABELS[meta.decision] || meta.decision);
  if (typeof meta.score === "number") parts.push(`${meta.score} điểm`);
  if (typeof meta.version === "number") parts.push(`phiên bản ${meta.version}`);
  if (Array.isArray(meta.application_ids)) parts.push(`${meta.application_ids.length} ứng viên`);
  if (typeof meta.count === "number") parts.push(`${meta.count} hồ sơ`);
  if (typeof meta.recipient === "string") parts.push(meta.recipient);
  if (typeof meta.note === "string" && meta.note.trim()) parts.push(`“${meta.note.trim()}”`);
  return parts.join(" · ");
}

export default function Home() {
  const [scheduleToken, setScheduleToken] = useState<string | null | undefined>(undefined);
  useEffect(() => setScheduleToken(new URLSearchParams(window.location.search).get("schedule")), []);
  if (scheduleToken === undefined) return <main className="auth-page"><div className="auth-card">Đang tải TalentFlow...</div></main>;
  return scheduleToken ? <PublicScheduling token={scheduleToken}/> : <RecruiterApp/>;
}

function RecruiterApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(!supabase);
  const [dashboard, setDashboard] = useState<Dashboard>(emptyDashboard);
  const [active, setActive] = useState("Tổng quan");
  const [selected, setSelected] = useState<Application | null>(null);
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<"job" | "upload" | "schedule" | "criteria" | null>(null);
  const [criteriaJob, setCriteriaJob] = useState<Job | null>(null);
  const [candidateTab, setCandidateTab] = useState("all");
  const [slots, setSlots] = useState<{ start_at: string; duration_minutes: number }[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<ProgressState | null>(null);
  const [jobProgress, setJobProgress] = useState<ProgressState | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [lastBatch, setLastBatch] = useState<BatchResult | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);

  const notify = (message: string) => { setToast(message); window.setTimeout(() => setToast(""), 3000); };
  const actionBusy = submitting || Boolean(pendingAction);
  const runAction = async (key: string, label: string, task: () => Promise<void>) => {
    if (actionBusy) return;
    setPendingAction({ key, label });
    try { await task(); }
    catch (err) { notify(err instanceof Error ? err.message : "Thao tác thất bại"); }
    finally { setPendingAction(null); }
  };
  const loadDashboard = async () => {
    try {
      const [nextDashboard, nextApprovals] = await Promise.all([
        request<Dashboard>("/api/dashboard"), request<Approval[]>("/api/approvals"),
      ]);
      setDashboard(nextDashboard); setApprovals(nextApprovals); setError("");
    }
    catch (err) { setError(err instanceof Error ? err.message : "Không thể tải dữ liệu"); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    if (!supabase) { void loadDashboard(); return; }
    void supabase.auth.getSession().then(({ data }) => { setSession(data.session); setAuthReady(true); });
    const { data } = supabase.auth.onAuthStateChange((_event, next) => { setSession(next); setAuthReady(true); });
    return () => data.subscription.unsubscribe();
  }, []);
  useEffect(() => { if (authReady && (!supabase || session)) void loadDashboard(); }, [authReady, session]);

  const tabbedApplications = useMemo(() => dashboard.applications.filter(item => {
    if (candidateTab === "waiting") return item.status === "WAITING_REVIEW";
    if (candidateTab === "reviewed") return item.status === "REVIEWED";
    if (candidateTab === "rejected") return item.status === "REJECTED";
    if (candidateTab === "interview") return item.status.startsWith("INTERVIEW");
    if (candidateTab === "archived") return item.status === "ARCHIVED";
    return true;
  }), [dashboard.applications, candidateTab]);
  const filtered = useMemo(() => tabbedApplications.filter(item =>
    `${item.candidate.name} ${item.candidate.email}`.toLowerCase().includes(query.toLowerCase())), [tabbedApplications, query]);
  const current = selected ? dashboard.applications.find(item => item.id === selected.id) || selected : null;
  const latest = dashboard.applications[0];
  const scoreClass = (score: number) => score >= 80 ? "score-high" : score >= 65 ? "score-mid" : "score-low";
  const chartScores = dashboard.applications.slice(0, 7).reverse().map(item => item.screening.final_score);

  const createJob = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (actionBusy) return;
    setSubmitting(true);
    const form = new FormData(event.currentTarget);
    setJobProgress({ value: 12, label: "Đang lưu JD", detail: "Gửi mô tả công việc lên backend" });
    const timers = [
      window.setTimeout(() => setJobProgress({ value: 38, label: "Đang chuẩn hoá JD", detail: "Kiểm tra vị trí, phòng ban và địa điểm" }), 350),
      window.setTimeout(() => setJobProgress({ value: 64, label: "AI đang extract requirement", detail: "Tách kỹ năng, kinh nghiệm và yêu cầu chính" }), 900),
      window.setTimeout(() => setJobProgress({ value: 86, label: "Đang cập nhật dashboard", detail: "Đồng bộ vị trí mới vào danh sách việc làm" }), 1800),
    ];
    try {
      await request<Job>("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(form)) });
      setJobProgress({ value: 100, label: "Hoàn tất", detail: "Việc làm đã tạo và requirements đã được trích xuất" });
      await new Promise(resolve => window.setTimeout(resolve, 450));
      setModal(null); await loadDashboard(); notify("Đã tạo việc làm và trích xuất yêu cầu");
    } catch (err) { notify(err instanceof Error ? err.message : "Tạo việc làm thất bại"); }
    finally { timers.forEach(timer => window.clearTimeout(timer)); setSubmitting(false); setJobProgress(null); }
  };

  const uploadCV = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (actionBusy) return;
    setSubmitting(true);
    const formData = new FormData(event.currentTarget);
    const files = formData.getAll("files") as File[];
    setUploadProgress({ value: 4, label: "Đang chuẩn bị CV", detail: `${files.length} file được chọn` });
    try {
      let result = await new Promise<BatchResult>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_URL}/api/application-batches`);
        void accessToken().then(token => { if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`); xhr.send(formData); });
        xhr.upload.onprogress = progressEvent => {
          if (!progressEvent.lengthComputable) return;
          const value = Math.max(6, Math.min(38, Math.round(progressEvent.loaded / progressEvent.total * 38)));
          setUploadProgress({ value, label: "Đang tải CV lên", detail: `${Math.round(progressEvent.loaded / progressEvent.total * 100)}% dữ liệu đã gửi` });
        };
        xhr.upload.onload = () => setUploadProgress({ value: 42, label: "Đã tải file", detail: "Đang tạo task screening" });
        xhr.onerror = () => reject(new Error("Mất kết nối khi tải CV"));
        xhr.onload = () => {
          const body = (() => { try { return JSON.parse(xhr.responseText); } catch { return {}; } })();
          if (xhr.status >= 200 && xhr.status < 300) resolve(body);
          else reject(new Error(body.detail || "Tải CV thất bại"));
        };
      });
      setLastBatch(result);
      let polls = 0;
      while (result.status === "PROCESSING" && polls < 120) {
        const active = result.items.filter(item => ["QUEUED", "PROCESSING"].includes(item.status)).length;
        const value = Math.min(96, 45 + Math.round(result.processed / Math.max(result.total, 1) * 51));
        setUploadProgress({ value, label: "Agent đang screening", detail: `${result.processed}/${result.total} xong · ${active} đang chờ/xử lý` });
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        result = await request<BatchResult>(`/api/application-batches/${result.batch_id}`);
        setLastBatch(result);
        polls += 1;
      }
      const finished = result.status !== "PROCESSING";
      setLastBatch(result);
      setUploadProgress({ value: finished ? 100 : 96, label: finished ? "Hoàn tất" : "Đang chạy nền",
        detail: `${result.completed} thành công, ${result.failed} lỗi, ${result.skipped} trùng` });
      await new Promise(resolve => window.setTimeout(resolve, 450));
      setModal(null); await loadDashboard();
      const first = result.items.find(item => item.status === "COMPLETED" && item.application)?.application; if (first) setSelected(first);
      notify(finished ? `Đã screening ${result.completed}/${result.total} CV` : "Screening tiếp tục chạy nền");
    } catch (err) { notify(err instanceof Error ? err.message : "Tải CV thất bại"); }
    finally { setSubmitting(false); setUploadProgress(null); }
  };

  const retryBatchItem = async (batchId: string, itemId: string) => {
    await runAction(`retry-${itemId}`, "Đang retry screening", async () => {
      let result = await request<BatchResult>(`/api/application-batches/${batchId}/items/${itemId}/retry`, { method: "POST" });
      setLastBatch(result);
      let polls = 0;
      while (result.status === "PROCESSING" && polls < 120) {
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        result = await request<BatchResult>(`/api/application-batches/${batchId}`);
        setLastBatch(result);
        polls += 1;
      }
      await loadDashboard();
      notify(result.status === "PROCESSING" ? "Task vẫn tiếp tục chạy nền" : "Đã hoàn tất retry screening");
    });
  };

  const review = async (decision: ReviewDecision) => {
    if (!current) return;
    await runAction(`review-${decision}-${current.id}`, {
      INTERVIEW: "Đang chuẩn bị lịch phỏng vấn",
      MANUAL_REVIEW: "Đang lưu đánh giá",
      REJECT: "Đang từ chối ứng viên",
      ARCHIVE: "Đang lưu trữ hồ sơ",
    }[decision], async () => {
      const note = {
        INTERVIEW: "Mời phỏng vấn từ dashboard",
        MANUAL_REVIEW: "Cần recruiter kiểm tra thêm",
        REJECT: "Recruiter từ chối ứng viên",
        ARCHIVE: "Recruiter lưu trữ hồ sơ",
      }[decision];
      const updated = await request<Application>(`/api/applications/${current.id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision, note }) });
      setSelected(updated); await loadDashboard();
      if (decision === "INTERVIEW") {
        const invitation = await request<{ public_url: string }>(`/api/applications/${current.id}/scheduling-invitations`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Ho_Chi_Minh" }),
        });
        if (navigator.clipboard) await navigator.clipboard.writeText(invitation.public_url).catch(() => undefined);
        setSelected(null); await loadDashboard();
        notify("Đã gửi link chọn lịch cho ứng viên và sao chép link");
      } else { setSelected(null); notify(`Đã cập nhật: ${statusLabel(updated.status)}`); }
    });
  };

  const approveCriteria = async (jobId: string, criteria: { required_skills: string[]; preferred_skills: string[]; minimum_experience: number }) => {
    await runAction(`criteria-${jobId}`, "Đang duyệt tiêu chí", async () => {
      await request(`/api/jobs/${jobId}/criteria`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...criteria, note: "Recruiter reviewed criteria" }) });
      await request(`/api/jobs/${jobId}/approve-criteria`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved: true, note: "Recruiter approved criteria" }) });
      setModal(null); setCriteriaJob(null); await loadDashboard(); notify("Đã lưu và duyệt tiêu chí tuyển dụng");
    });
  };

  const approveShortlist = async (jobId: string) => {
    await runAction(`shortlist-${jobId}`, "Đang duyệt Top 5", async () => {
      const data = await request<{ items: Application[] }>(`/api/jobs/${jobId}/shortlist?limit=5`);
      const application_ids = data.items.map(item => item.id);
      if (!application_ids.length) { notify("Job này chưa có CV để shortlist"); return; }
      const approved = await request<{ invitations?: unknown[] }>(`/api/jobs/${jobId}/approve-shortlist`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ application_ids, note: "Recruiter approved AI top 5 shortlist" }) });
      await loadDashboard(); notify(`Đã duyệt Top ${application_ids.length} và gửi ${approved.invitations?.length || 0} email mời đặt lịch`);
    });
  };

  const resolveApproval = async (approval: Approval, decision: "APPROVE" | "REJECT") => {
    await runAction(`approval-${approval.id}`, `Đang ${decision === "APPROVE" ? "phê duyệt" : "từ chối"}`, async () => {
      await request(`/api/approvals/${approval.id}/resolve`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, note: `Recruiter ${decision.toLowerCase()} from approval inbox` }),
      });
      await loadDashboard();
      notify(decision === "APPROVE" ? "Đã phê duyệt đề xuất" : "Đã trả lại để xem xét");
    });
  };

  const exportReport = async (job: Job) => {
    await runAction(`export-${job.id}`, "Đang xuất report", async () => {
      await download(`/api/jobs/${job.id}/shortlist-report`, `${job.title.toLowerCase().replace(/\s+/g, "-")}-shortlist.md`);
      notify("Đã xuất shortlist report");
    });
  };

  const deleteJob = async (jobId: string) => {
    await runAction(`delete-${jobId}`, "Đang xoá việc làm", async () => {
      await request(`/api/jobs/${jobId}`, { method: "DELETE" });
      await loadDashboard(); notify("Đã xoá việc làm");
    });
  };

  const book = async (slot: string) => {
    if (!current) return;
    await runAction(`book-${slot}`, "Đang đặt lịch phỏng vấn", async () => {
      await request(`/api/applications/${current.id}/interview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ slot }) });
      setModal(null); setSelected(null); await loadDashboard(); notify(`Đã đặt lịch ${dateLabel(slot)}`);
    });
  };

  const signOut = async () => {
    await runAction("signout", "Đang đăng xuất", async () => {
      if (!supabase) return;
      const { error } = await supabase.auth.signOut();
      if (error) throw new Error(error.message);
    });
  };

  if (!authReady) return <main className="auth-page"><div className="auth-card">Đang kiểm tra phiên đăng nhập...</div></main>;
  if (supabase && !session) return <AuthScreen/>;
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brandmark"><Icon name="spark"/></div><div><b>TalentFlow</b><span>AI Recruitment</span></div></div>
      <nav><p className="nav-label">WORKSPACE</p>
        {[["Tổng quan","grid"],["Việc làm","briefcase"],["Ứng viên","users"],["Phê duyệt","bell"],["Phỏng vấn","calendar"],["Lịch sử","clock"],["Mail Sandbox","bell"],["Xoá dữ liệu","users"]].map(([label,icon]) =>
          <button key={label} className={active === label ? "nav-item active" : "nav-item"} onClick={() => setActive(label)}><Icon name={icon}/>{label}{label === "Ứng viên" && <span className="count">{dashboard.metrics.awaiting_review}</span>}{label === "Phê duyệt" && approvals.length > 0 && <span className="count">{approvals.length}</span>}</button>)}
        <p className="nav-label section">AI AGENT</p><button className={active === "Pipeline" ? "nav-item active" : "nav-item"} onClick={() => setActive("Pipeline")}><Icon name="spark"/>Pipeline <span className="live-dot"/></button>
      </nav>
      <div className="agent-card"><div className="agent-icon"><Icon name="spark"/></div><b>Agent đang hoạt động</b><p>Pipeline đã xử lý {dashboard.metrics.candidates} CV.</p><div className="agent-progress"><span/></div><small>Dữ liệu đồng bộ từ API</small></div>
      <div className="profile"><div className="avatar dark">VN</div><div><b>{session?.user.email || LOCAL_ACTOR_NAME}</b><span>Recruiter</span></div><button aria-label="Đăng xuất" disabled={actionBusy} onClick={() => void signOut()}>↪</button></div>
    </aside>

    <main>
      <header><div className="mobile-brand"><b>TalentFlow</b></div><div className="search"><Icon name="search"/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Tìm ứng viên, việc làm..."/><kbd>⌘ K</kbd></div><button className="icon-button" aria-label="Thông báo" disabled={actionBusy} onClick={() => notify("Bạn không có thông báo mới")}><Icon name="bell"/><i/></button><button className="primary" disabled={actionBusy} onClick={() => setModal("job")}><Icon name="plus"/>Tạo việc làm</button></header>
      <div className="content">
        {pendingAction && <GlobalActionStatus label={pendingAction.label}/>}
        <section className="welcome"><div><span className="eyebrow">TALENTFLOW · LIVE DASHBOARD</span><h1>{active === "Tổng quan" ? "Chào buổi sáng, Vinh 👋" : active}</h1><p>Dữ liệu và hoạt động được cập nhật trực tiếp từ API.</p></div><button className="upload" onClick={() => setModal("upload")} disabled={!dashboard.jobs.length || actionBusy}><Icon name="upload"/>Tải CV lên</button></section>
        {error && <div className="error-banner"><b>Không kết nối được backend.</b> {error} — kiểm tra {API_URL.includes("localhost") ? "API tại cổng 8000" : "backend Render"}.</div>}
        {lastBatch && <BatchStatusPanel batch={lastBatch} actionBusy={actionBusy} pendingAction={pendingAction} onRetry={retryBatchItem}/>}
        {loading && <DashboardSkeleton/>}

        {(active === "Tổng quan" || active === "Pipeline") && <section className="metrics">
          {[{icon:"briefcase",label:"Việc làm đang mở",value:dashboard.metrics.open_jobs,tone:"purple"},{icon:"users",label:"Tổng ứng viên",value:dashboard.metrics.candidates,tone:"blue"},{icon:"spark",label:"Chờ đánh giá",value:dashboard.metrics.awaiting_review,tone:"amber"},{icon:"calendar",label:"Phỏng vấn đã đặt",value:dashboard.metrics.interviews,tone:"green"}].map(m =>
            <article className="metric" key={m.label}><div className={`metric-icon ${m.tone}`}><Icon name={m.icon}/></div><div><p>{m.label}</p><strong>{loading ? "—" : m.value}</strong><span className={`delta ${m.tone}`}>Live</span></div></article>)}
        </section>}

        {active === "Việc làm" ? <JobsView jobs={dashboard.jobs} applications={dashboard.applications} actionBusy={actionBusy} pendingAction={pendingAction} onCreate={() => setModal("job")} onDelete={deleteJob} onReviewCriteria={job => { setCriteriaJob(job); setModal("criteria"); }} onApproveShortlist={approveShortlist} onExportReport={exportReport}/>
        : active === "Phê duyệt" ? <ApprovalInbox approvals={approvals} dashboard={dashboard} actionBusy={actionBusy} pendingAction={pendingAction} onResolve={resolveApproval}/>
        : active === "Phỏng vấn" ? <InterviewsView dashboard={dashboard} onChanged={loadDashboard}/>
        : active === "Lịch sử" ? <AuditLogView dashboard={dashboard}/>
        : active === "Mail Sandbox" ? <MailSandboxView/>
        : active === "Xoá dữ liệu" ? <ClearDataView dashboard={dashboard} onCleared={async () => { setSelected(null); setLastBatch(null); await loadDashboard(); }}/>
        : <><div className="dashboard-grid">
          <section className="panel candidates-panel"><div className="panel-head"><div><h2>{active === "Ứng viên" ? "Tất cả ứng viên" : "Ứng viên mới nhất"}</h2><p>Được AI xếp hạng theo mức độ phù hợp</p></div><button onClick={() => { setActive("Ứng viên"); setQuery(""); }}>Xem tất cả <Icon name="arrow"/></button></div>
            {active === "Ứng viên" && <div className="candidate-tabs">{[
              ["all", "Tất cả", dashboard.applications.length],
              ["waiting", "Chờ duyệt", dashboard.applications.filter(item => item.status === "WAITING_REVIEW").length],
              ["reviewed", "Xem xét", dashboard.applications.filter(item => item.status === "REVIEWED").length],
              ["interview", "Phỏng vấn", dashboard.applications.filter(item => item.status.startsWith("INTERVIEW")).length],
              ["rejected", "Từ chối", dashboard.applications.filter(item => item.status === "REJECTED").length],
              ["archived", "Lưu trữ", dashboard.applications.filter(item => item.status === "ARCHIVED").length],
            ].map(([key, label, count]) => <button key={key} className={candidateTab === key ? "active" : ""} onClick={() => setCandidateTab(String(key))}>{label}<span>{count}</span></button>)}</div>}
            <div className="table-head"><span>ỨNG VIÊN</span><span>ĐỘ PHÙ HỢP</span><span>TRẠNG THÁI</span><span/></div><div className="candidate-list">
              {filtered.map((item, index) => <button className="candidate-row" key={item.id} onClick={() => setSelected(item)}><span className="person"><i className={`avatar ${["violet","blue","orange"][index%3]}`}>{initials(item.candidate.name)}</i><span><b>{item.candidate.name}</b><small>{item.candidate.email}</small></span></span><span className="match"><i className={`score-ring ${scoreClass(item.screening.final_score)}`} style={{"--score": `${item.screening.final_score * 3.6}deg`} as React.CSSProperties}>{Math.round(item.screening.final_score)}</i><span><b>{item.screening.recommendation}</b><small>{item.screening.final_score}% match</small></span></span><span><i className={`status ${statusTone(item.status)}`}>{statusLabel(item.status)}</i></span><span className="row-arrow"><Icon name="arrow"/></span></button>)}
              {!filtered.length && <div className="empty-state">Không tìm thấy ứng viên phù hợp.</div>}
            </div>
          </section>
          <section className="panel pipeline-panel"><div className="panel-head"><div><h2>AI Pipeline</h2><p>Hồ sơ có điểm cao nhất</p></div><span className="running"><i/> Đồng bộ</span></div>{latest ? <><div className="pipeline-summary"><div className="avatar violet">{initials(latest.candidate.name)}</div><div><b>{latest.candidate.name}</b><small>{dashboard.jobs.find(j => j.id === latest.job_id)?.title}</small></div><strong>{latest.screening.final_score}</strong></div><div className="steps">{latest.pipeline.map(step => <div className={step.status === "waiting" ? "step waiting" : "step"} key={step.node}><i>{step.status !== "waiting" && <Icon name="check"/>}</i><span><b>{step.node}</b><small>{step.status === "waiting" ? "Đang chờ quyết định của bạn" : "Hoàn thành"}</small></span></div>)}</div><button className="pipeline-button" onClick={() => setSelected(latest)}>Xem chi tiết pipeline <Icon name="arrow"/></button></> : <div className="empty-state">Tải CV đầu tiên để chạy pipeline.</div>}</section>
        </div>
        <section className="bottom-grid"><article className="insight"><div className="insight-icon"><Icon name="spark"/></div><div><span>PHÂN BỐ ĐIỂM THỰC</span><h3>{dashboard.applications.length ? `Điểm trung bình ${Math.round(dashboard.applications.reduce((sum, item) => sum + item.screening.final_score, 0) / dashboard.applications.length)}%` : "Chưa có dữ liệu"}</h3><p>Mỗi cột là điểm của một CV gần đây.</p></div><div className="mini-chart dynamic">{chartScores.map((score, index) => <i key={index} style={{ height: `${Math.max(6, score * .36)}px` }} title={`${score}%`}/>)}</div></article><article className="next-interview"><div><span>HOẠT ĐỘNG TIẾP THEO</span><h3>{dashboard.metrics.awaiting_review} hồ sơ chờ đánh giá</h3><p>Bấm vào ứng viên để xem evidence và ra quyết định.</p></div><button className="secondary compact" onClick={() => setActive("Ứng viên")}>Xem ngay</button></article></section></>}
      </div>
    </main>

    {current && <CandidateDrawer application={current} actionBusy={actionBusy} pendingAction={pendingAction} scoreClass={scoreClass} onClose={() => { if (!actionBusy) setSelected(null); }} onReview={review}/>}
    {modal === "job" && <JobModal submitting={submitting} progress={jobProgress} onClose={() => setModal(null)} onSubmit={createJob}/>}
    {modal === "upload" && <UploadModal jobs={dashboard.jobs} submitting={submitting} progress={uploadProgress} onClose={() => setModal(null)} onSubmit={uploadCV}/>} 
    {modal === "criteria" && criteriaJob && <CriteriaModal job={criteriaJob} busy={actionBusy} onClose={() => { setModal(null); setCriteriaJob(null); }} onSubmit={criteria => approveCriteria(criteriaJob.id, criteria)}/>}
    {modal === "schedule" && current && <div className="modal-layer"><div className="modal"><button className="close" disabled={actionBusy} onClick={() => setModal(null)}>×</button><span className="eyebrow">SCHEDULING AGENT</span><h2>Chọn lịch phỏng vấn</h2><p>Các lịch trống được lấy trực tiếp từ API.</p>{pendingAction?.key.startsWith("book-") && <InlineProgress label={pendingAction.label}/>}<div className="slots">{slots.map(slot => <button key={slot.start_at} disabled={actionBusy} onClick={() => void book(slot.start_at)}>{pendingAction?.key === `book-${slot.start_at}` ? "Đang đặt lịch..." : dateLabel(slot.start_at)}<Icon name="arrow"/></button>)}</div></div></div>}
    {toast && <div className="toast"><Icon name="check"/>{toast}</div>}
  </div>;
}

function PublicScheduling({ token }: { token: string }) {
  const [data, setData] = useState<PublicSchedule | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [booked, setBooked] = useState<Interview | null>(null);
  useEffect(() => {
    void fetch(`${API_URL}/api/public/scheduling/${encodeURIComponent(token)}`)
      .then(async response => { if (!response.ok) throw new Error((await response.json()).detail || "Link không hợp lệ"); return response.json(); })
      .then(setData).catch(err => setError(err instanceof Error ? err.message : "Không tải được lịch"));
  }, [token]);
  const choose = async (slot: string) => {
    setBusy(slot); setError("");
    try {
      const response = await fetch(`${API_URL}/api/public/scheduling/${encodeURIComponent(token)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slot, timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone || data?.timezone || "UTC" }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Không thể giữ lịch này");
      setBooked(body);
    } catch (err) { setError(err instanceof Error ? err.message : "Đặt lịch thất bại"); }
    finally { setBusy(""); }
  };
  return <main className="public-schedule-page"><section className="public-schedule-card">
    <div className="brand public-brand"><div className="brandmark"><Icon name="spark"/></div><div><b>TalentFlow</b><span>Candidate Scheduling</span></div></div>
    {error ? <div className="error-banner"><b>Không thể tiếp tục.</b> {error}</div>
    : booked ? <div className="booking-success"><i><Icon name="check"/></i><span className="eyebrow">ĐÃ GIỮ LỊCH</span><h1>{data?.mode === "reschedule" ? "Yêu cầu đổi lịch đã được ghi nhận" : "Khung giờ đã được giữ cho bạn"}</h1><p>{dateLabel(booked.start_at)}</p><small>HR sẽ xác nhận và gửi email kèm thông tin tham gia.</small>{booked.meeting_url && <a href={booked.meeting_url}>Mở phòng họp</a>}</div>
    : !data ? <InlineProgress label="Đang kiểm tra lịch trống"/>
    : <><span className="eyebrow">{data.mode === "reschedule" ? "ĐỔI LỊCH TRONG POLICY" : "CHỌN LỊCH PHỎNG VẤN"}</span><h1>Chào {data.candidate_name}</h1><p>{data.mode === "reschedule" && data.interview ? <>Lịch hiện tại: <b>{dateLabel(data.interview.start_at)}</b>. </> : null}Chọn một khung giờ cho vị trí <b>{data.job_title}</b>. Thời lượng {data.duration_minutes} phút; giờ hiển thị theo thiết bị của bạn.</p><div className="slots public-slots">{data.slots.map(slot => <button key={slot.start_at} disabled={Boolean(busy)} onClick={() => void choose(slot.start_at)}>{busy === slot.start_at ? "Đang giữ lịch..." : dateLabel(slot.start_at)}<Icon name="arrow"/></button>)}</div>{!data.slots.length && <div className="empty-state">Hiện chưa có lịch phù hợp. Vui lòng liên hệ recruiter.</div>}</>}
  </section></main>;
}

function GlobalActionStatus({ label }: { label: string }) {
  return <div className="action-status" role="status" aria-live="polite"><span>{label}</span><i/></div>;
}

function InlineProgress({ label }: { label: string }) {
  return <div className="inline-progress" role="status" aria-live="polite"><div><b>{label}</b><span>Vui lòng đợi, thao tác đang được xử lý</span></div><i/></div>;
}

function DashboardSkeleton() {
  return <section className="skeleton-grid" aria-label="Đang tải dữ liệu">
    {[0, 1, 2, 3].map(item => <article className="skeleton-card" key={item}><i/><span/><b/></article>)}
    <article className="skeleton-panel"><span/><span/><span/><span/></article>
    <article className="skeleton-panel compact-panel"><span/><span/><span/></article>
  </section>;
}

function JobsView({ jobs, applications, actionBusy, pendingAction, onCreate, onDelete, onReviewCriteria, onApproveShortlist, onExportReport }: {
  jobs: Job[];
  applications: Application[];
  actionBusy: boolean;
  pendingAction: PendingAction | null;
  onCreate: () => void;
  onDelete: (jobId: string) => Promise<void>;
  onReviewCriteria: (job: Job) => void;
  onApproveShortlist: (jobId: string) => Promise<void>;
  onExportReport: (job: Job) => Promise<void>;
}) {
  return <section className="panel jobs-view">
    <div className="panel-head">
      <div><h2>Việc làm đang tuyển</h2><p>{jobs.length} vị trí từ API</p></div>
      <button disabled={actionBusy} onClick={onCreate}><Icon name="plus"/>Tạo mới</button>
    </div>
    {jobs.map(job => <JobRow key={job.id} job={job} applications={applications} actionBusy={actionBusy}
                             pendingAction={pendingAction} onDelete={onDelete} onReviewCriteria={onReviewCriteria}
                             onApproveShortlist={onApproveShortlist} onExportReport={onExportReport}/>)}
  </section>;
}

function JobRow({ job, applications, actionBusy, pendingAction, onDelete, onReviewCriteria, onApproveShortlist, onExportReport }: {
  job: Job;
  applications: Application[];
  actionBusy: boolean;
  pendingAction: PendingAction | null;
  onDelete: (jobId: string) => Promise<void>;
  onReviewCriteria: (job: Job) => void;
  onApproveShortlist: (jobId: string) => Promise<void>;
  onExportReport: (job: Job) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const required = job.requirements?.required_skills || [];
  const preferred = job.requirements?.preferred_skills || [];
  const criteriaApproved = job.requirements?.approval?.status === "APPROVED";
  const shortlistApproved = job.requirements?.shortlist_approval?.status === "APPROVED";

  return <article className={open ? "job-row job-row-detailed job-row-open" : "job-row job-row-detailed"}>
    <div className="metric-icon purple"><Icon name="briefcase"/></div>
    <div>
      <h3>{job.title}</h3>
      <p>{job.department} · {job.location}</p>
      <div className="job-requirements">
        {required.map(item => <span key={item}>{item}</span>)}
        {preferred.map(item => <span className="soft" key={item}>{item}</span>)}
        {Boolean(job.requirements?.minimum_experience) && <span>{job.requirements?.minimum_experience}+ năm</span>}
      </div>
    </div>
    <span>{job.applications_count} ứng viên</span>
    <i className={criteriaApproved ? "status interview" : "status review"}>{criteriaApproved ? "Tiêu chí đã duyệt" : "Chờ duyệt tiêu chí"}</i>
    <div className="job-actions">
      <button className="secondary compact" onClick={() => setOpen(value => !value)} aria-expanded={open}>
        {open ? "Ẩn chi tiết" : "Xem chi tiết"}
      </button>
      <button className="secondary compact" disabled={actionBusy || criteriaApproved} onClick={() => onReviewCriteria(job)}>
        {pendingAction?.key === `criteria-${job.id}` ? "Đang duyệt..." : "Kiểm tra tiêu chí"}
      </button>
      <button className="primary compact" disabled={actionBusy || !job.applications_count || shortlistApproved} onClick={() => void onApproveShortlist(job.id)}>
        {pendingAction?.key === `shortlist-${job.id}` ? "Đang duyệt..." : shortlistApproved ? "Đã duyệt Top 5" : "Duyệt Top 5"}
      </button>
      <button className="secondary compact" disabled={actionBusy || !job.applications_count} onClick={() => void onExportReport(job)}>
        {pendingAction?.key === `export-${job.id}` ? "Đang xuất..." : "Xuất report"}
      </button>
      <button className="danger-link" disabled={actionBusy} onClick={() => void onDelete(job.id)}>
        {pendingAction?.key === `delete-${job.id}` ? "Đang xoá..." : "Xoá"}
      </button>
    </div>
    {open && <JobDetail job={job} applications={applications}/>}
  </article>;
}

function JobDetail({ job, applications }: { job: Job; applications: Application[] }) {
  type CriteriaVersion = {
    id: string; version: number; status: string; change_note: string;
    criteria: { required_skills?: string[]; preferred_skills?: string[]; minimum_experience?: number };
    approved_at: string | null; created_at: string;
  };
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [versions, setVersions] = useState<CriteriaVersion[]>([]);
  const [history, setHistory] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    void Promise.all([
      request<Approval[]>(`/api/approvals?status=ALL&job_id=${job.id}`),
      request<CriteriaVersion[]>(`/api/jobs/${job.id}/criteria-versions`),
      request<AuditLog[]>(`/api/audit-logs?limit=50&job_id=${job.id}`),
    ])
      .then(([nextApprovals, nextVersions, nextHistory]) => {
        setApprovals(nextApprovals); setVersions(nextVersions); setHistory(nextHistory);
      })
      .catch(err => setError(err instanceof Error ? err.message : "Không tải được chi tiết"))
      .finally(() => setLoading(false));
  }, [job.id]);

  const mine = applications.filter(item => item.job_id === job.id);
  const scores = mine.map(item => item.screening.final_score).filter(value => value > 0);
  const average = scores.length ? Math.round(scores.reduce((sum, value) => sum + value, 0) / scores.length) : 0;
  const byStatus = mine.reduce<Record<string, number>>((acc, item) => {
    acc[item.status] = (acc[item.status] || 0) + 1; return acc;
  }, {});
  const criteriaApproval = approvals.find(item => item.type === "CRITERIA");
  const person = (id?: string | null, email?: string | null) =>
    email?.trim() || (id === LOCAL_ACTOR_ID ? LOCAL_ACTOR_NAME : id) || "Hệ thống tự đề xuất";

  return <div className="job-detail">
    {error && <div className="error-banner"><b>Không tải được chi tiết.</b> {error}</div>}

    <div className="job-detail-grid">
      <section className="job-detail-block">
        <h4>Mô tả công việc</h4>
        <p className="job-description">{job.description || "Chưa có mô tả."}</p>
      </section>

      <section className="job-detail-block">
        <h4>Truy vết phê duyệt tiêu chí</h4>
        {loading ? <small>Đang tải...</small> : criteriaApproval ? <dl className="job-trace">
          <div><dt>Người đề xuất</dt><dd>{person(criteriaApproval.requested_by_id, criteriaApproval.requested_by_email)}</dd></div>
          <div><dt>Thời gian đề xuất</dt><dd>{fullDateLabel(criteriaApproval.created_at)}</dd></div>
          <div><dt>Người duyệt</dt><dd>{criteriaApproval.decided_at
            ? person(criteriaApproval.resolution?.decided_by_id as string, criteriaApproval.resolution?.decided_by_email as string)
            : <em>Chưa ai duyệt</em>}</dd></div>
          <div><dt>Thời gian duyệt</dt><dd>{criteriaApproval.decided_at ? fullDateLabel(criteriaApproval.decided_at) : <em>—</em>}</dd></div>
          {typeof criteriaApproval.resolution?.note === "string" && criteriaApproval.resolution.note &&
            <div><dt>Ghi chú khi duyệt</dt><dd>“{criteriaApproval.resolution.note as string}”</dd></div>}
        </dl> : <small>Chưa có yêu cầu phê duyệt nào cho vị trí này.</small>}
      </section>

      <section className="job-detail-block">
        <h4>Tình hình ứng viên</h4>
        <div className="job-stats">
          <span><b>{mine.length}</b>hồ sơ</span>
          <span><b>{scores.length ? Math.max(...scores) : 0}</b>điểm cao nhất</span>
          <span><b>{average}</b>điểm trung bình</span>
        </div>
        <div className="job-status-split">
          {Object.entries(byStatus).length
            ? Object.entries(byStatus).map(([status, count]) =>
                <i className={`status ${statusTone(status)}`} key={status}>{statusLabel(status)}: {count}</i>)
            : <small>Chưa có CV nào được nộp.</small>}
        </div>
      </section>

      <section className="job-detail-block">
        <h4>Các phiên bản tiêu chí</h4>
        {loading ? <small>Đang tải...</small> : versions.length ? <ol className="job-versions">
          {versions.map(version => <li key={version.id}>
            <b>v{version.version}</b>
            <i className={`status ${version.status === "APPROVED" ? "interview" : "review"}`}>{version.status === "APPROVED" ? "Đã duyệt" : "Chờ duyệt"}</i>
            <span>{(version.criteria.required_skills || []).join(", ") || "không có kỹ năng nào"}</span>
            {version.change_note && <em>{version.change_note}</em>}
            <time dateTime={version.created_at}>{fullDateLabel(version.created_at)}</time>
          </li>)}
        </ol> : <small>Chưa có phiên bản nào.</small>}
      </section>
    </div>

    <section className="job-detail-block wide">
      <h4>Lịch sử thao tác của vị trí này</h4>
      {loading ? <small>Đang tải...</small> : history.length ? <ol className="approval-history">
        {history.map(entry => <li key={entry.id}>
          <b>{auditLabel(entry.action)}</b>
          <span>{actorLabel(entry) || "Hệ thống tự động"}</span>
          <time dateTime={entry.created_at}>{fullDateLabel(entry.created_at)}</time>
        </li>)}
      </ol> : <small>Chưa có thao tác nào được ghi nhận cho vị trí này.</small>}
    </section>
  </div>;
}

function CriteriaModal({ job, busy, onClose, onSubmit }: {
  job: Job; busy: boolean; onClose: () => void;
  onSubmit: (criteria: { required_skills: string[]; preferred_skills: string[]; minimum_experience: number }) => Promise<void>;
}) {
  const [required, setRequired] = useState((job.requirements?.required_skills || []).map(value => ({ value, checked: true })));
  const [preferred, setPreferred] = useState((job.requirements?.preferred_skills || []).map(value => ({ value, checked: true })));
  const [newCriterion, setNewCriterion] = useState("");
  const [newKind, setNewKind] = useState<"required" | "preferred">("required");
  const [experience, setExperience] = useState(Number(job.requirements?.minimum_experience || 0));
  const add = () => {
    const value = newCriterion.trim();
    if (!value) return;
    const setter = newKind === "required" ? setRequired : setPreferred;
    setter(items => items.some(item => item.value.toLowerCase() === value.toLowerCase()) ? items : [...items, { value, checked: true }]);
    setNewCriterion("");
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const required_skills = required.filter(item => item.checked).map(item => item.value);
    if (!required_skills.length) return;
    await onSubmit({ required_skills, preferred_skills: preferred.filter(item => item.checked).map(item => item.value), minimum_experience: experience });
  };
  return <div className="modal-layer"><form className="modal form-modal criteria-modal" onSubmit={submit}><button type="button" className="close" disabled={busy} onClick={onClose}>×</button><span className="eyebrow">HUMAN REVIEW</span><h2>Duyệt tiêu chí · {job.title}</h2><p>Check/uncheck tiêu chí AI đã tách, hoặc bổ sung tiêu chí mới trước khi phê duyệt.</p><fieldset disabled={busy}><h3>Bắt buộc</h3><div className="criteria-checks">{required.map((item, index) => <label key={`${item.value}-${index}`}><input type="checkbox" checked={item.checked} onChange={event => setRequired(values => values.map((entry, current) => current === index ? { ...entry, checked: event.target.checked } : entry))}/><span>{item.value}</span></label>)}</div><h3>Ưu tiên</h3><div className="criteria-checks">{preferred.map((item, index) => <label key={`${item.value}-${index}`}><input type="checkbox" checked={item.checked} onChange={event => setPreferred(values => values.map((entry, current) => current === index ? { ...entry, checked: event.target.checked } : entry))}/><span>{item.value}</span></label>)}{!preferred.length && <small>Chưa có tiêu chí ưu tiên.</small>}</div><div className="criteria-add"><input value={newCriterion} onChange={event => setNewCriterion(event.target.value)} placeholder="Bổ sung kỹ năng/tiêu chí"/><select value={newKind} onChange={event => setNewKind(event.target.value as "required" | "preferred")}><option value="required">Bắt buộc</option><option value="preferred">Ưu tiên</option></select><button type="button" className="secondary compact" onClick={add}>Thêm</button></div><label><span>Kinh nghiệm tối thiểu (năm)</span><input type="number" min="0" max="60" value={experience} onChange={event => setExperience(Number(event.target.value))}/></label><button className="primary submit" disabled={!required.some(item => item.checked)}>{busy ? "Đang lưu và duyệt..." : "Lưu & duyệt tiêu chí"}</button></fieldset></form></div>;
}

function ClearDataView({ dashboard, onCleared }: { dashboard: Dashboard; onCleared: () => Promise<void> }) {
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const clear = async () => {
    setBusy(true); setMessage("");
    try {
      const result = await request<{ removed: { jobs: number; applications: number; interviews: number } }>("/api/data/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation }) });
      await onCleared(); setConfirmation("");
      setMessage(`Đã xoá ${result.removed.jobs} việc làm, ${result.removed.applications} CV/ứng viên và ${result.removed.interviews} lịch phỏng vấn.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không thể xoá dữ liệu"); }
    finally { setBusy(false); }
  };
  return <section className="panel clear-data-view"><div className="panel-head"><div><h2>Xoá toàn bộ dữ liệu tuyển dụng</h2><p>Giữ nguyên tài khoản, phân quyền, kết nối và cấu hình hệ thống.</p></div><span className="status rejected">DANGER ZONE</span></div><div className="clear-data-body"><h3>Sẽ xoá vĩnh viễn</h3><div className="clear-summary"><span><b>{dashboard.jobs.length}</b> việc làm</span><span><b>{dashboard.applications.length}</b> CV / ứng viên</span><span><b>{dashboard.interviews?.length || 0}</b> lịch phỏng vấn</span></div><p>Nhập chính xác <code>XOA TOAN BO</code> để xác nhận.</p><input value={confirmation} onChange={event => setConfirmation(event.target.value)} placeholder="XOA TOAN BO"/><button className="danger" disabled={busy || confirmation.trim().toUpperCase() !== "XOA TOAN BO"} onClick={() => void clear()}>{busy ? "Đang xoá..." : "Xoá toàn bộ dữ liệu"}</button>{message && <p className="operations-message">{message}</p>}</div></section>;
}

function ApprovalInbox({ approvals, dashboard, actionBusy, pendingAction, onResolve }: {
  approvals: Approval[]; dashboard: Dashboard; actionBusy: boolean; pendingAction: PendingAction | null;
  onResolve: (approval: Approval, decision: "APPROVE" | "REJECT") => Promise<void>;
}) {
  const labels: Record<Approval["type"], string> = { CRITERIA: "Tiêu chí", EVIDENCE: "Evidence yếu", SHORTLIST: "Shortlist", ESCALATION: "Ngoại lệ phỏng vấn" };
  return <section className="panel approval-view">
    <div className="panel-head">
      <div><h2>Approval inbox</h2><p>{approvals.length} quyết định cần recruiter xử lý</p></div>
      <span className="bounded-badge">Bounded agent</span>
    </div>
    {approvals.length ? approvals.map(item =>
      <ApprovalCard key={item.id} approval={item} typeLabel={labels[item.type]} dashboard={dashboard}
                    actionBusy={actionBusy} pendingAction={pendingAction} onResolve={onResolve}/>)
      : <div className="empty-state">Inbox đã sạch. Agent chỉ chuyển tới đây các quyết định cần người.</div>}
  </section>;
}

function ApprovalCard({ approval, typeLabel, dashboard, actionBusy, pendingAction, onResolve }: {
  approval: Approval; typeLabel: string; dashboard: Dashboard; actionBusy: boolean;
  pendingAction: PendingAction | null;
  onResolve: (approval: Approval, decision: "APPROVE" | "REJECT") => Promise<void>;
}) {
  const [history, setHistory] = useState<AuditLog[] | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState("");

  const payload = approval.payload || {};
  const requester = approval.requested_by_email?.trim()
    || (approval.requested_by_id === LOCAL_ACTOR_ID ? LOCAL_ACTOR_NAME : approval.requested_by_id) || "";
  const job = dashboard.jobs.find(item => item.id === approval.job_id);
  const candidate = dashboard.applications.find(item => item.id === approval.application_id);

  const toggleHistory = async () => {
    if (history) { setHistory(null); return; }
    setLoadingHistory(true); setHistoryError("");
    const query = approval.application_id ? `application_id=${approval.application_id}`
      : approval.job_id ? `job_id=${approval.job_id}` : "";
    try { setHistory(await request<AuditLog[]>(`/api/audit-logs?limit=50&${query}`)); }
    catch (err) { setHistoryError(err instanceof Error ? err.message : "Không tải được lịch sử"); }
    finally { setLoadingHistory(false); }
  };

  const criteria = (payload.criteria || {}) as { required_skills?: string[]; preferred_skills?: string[]; minimum_experience?: number };
  const ranking = (payload.ranking || []) as { application_id: string; score: number; confidence?: number }[];

  return <article className="approval-row approval-card">
    <div className={`approval-type ${approval.type.toLowerCase()}`}>
      <Icon name={approval.type === "SHORTLIST" ? "users" : approval.type === "CRITERIA" ? "briefcase" : "spark"}/>
    </div>
    <div className="approval-main">
      <span className="eyebrow">{typeLabel}</span>
      <h3>{approval.title}</h3>
      <p>{approval.summary}</p>

      <div className="approval-meta">
        <span>Người đề xuất: <b>{requester || "Hệ thống tự đề xuất"}</b></span>
        {approval.created_at && <span>Thời gian: <b>{fullDateLabel(approval.created_at)}</b></span>}
        {job && <span>Vị trí: <b>{job.title}</b></span>}
        {candidate && <span>Ứng viên: <b>{candidate.candidate.name}</b></span>}
      </div>

      {approval.type === "CRITERIA" && <div className="approval-detail">
        <div className="approval-field">
          <span>Kỹ năng bắt buộc</span>
          <div className="approval-chips">
            {criteria.required_skills?.length
              ? criteria.required_skills.map(skill => <i key={skill}>{skill}</i>)
              : <em className="approval-empty">AI không tách được kỹ năng nào — cần tự bổ sung trước khi duyệt</em>}
          </div>
        </div>
        <div className="approval-field">
          <span>Kỹ năng ưu tiên</span>
          <div className="approval-chips">
            {criteria.preferred_skills?.length
              ? criteria.preferred_skills.map(skill => <i className="soft" key={skill}>{skill}</i>)
              : <em className="approval-empty">Không có</em>}
          </div>
        </div>
        <div className="approval-field">
          <span>Kinh nghiệm tối thiểu</span>
          <div className="approval-chips"><i>{criteria.minimum_experience || 0} năm</i></div>
        </div>
        <div className="approval-field">
          <span>Phiên bản · Nguồn</span>
          <div className="approval-chips"><i>v{String(payload.version ?? 1)}</i>
            {typeof payload.change_note === "string" && payload.change_note && <i className="soft">{payload.change_note}</i>}</div>
        </div>
      </div>}

      {approval.type === "SHORTLIST" && <div className="approval-detail">
        <div className="approval-field wide">
          <span>{ranking.length} ứng viên được đề xuất — chưa gửi email cho ai</span>
          <table className="approval-table">
            <thead><tr><th>#</th><th>Ứng viên</th><th>Điểm</th><th>Độ tin cậy</th></tr></thead>
            <tbody>
              {ranking.map((row, index) => {
                const person = dashboard.applications.find(item => item.id === row.application_id);
                return <tr key={row.application_id}>
                  <td>{index + 1}</td>
                  <td>{person?.candidate.name || row.application_id.slice(0, 8)}</td>
                  <td className="num">{row.score}</td>
                  <td className="num">{row.confidence != null ? `${Math.round(row.confidence * 100)}%` : "—"}</td>
                </tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>}

      {approval.type === "EVIDENCE" && <div className="approval-detail">
        <div className="approval-field"><span>Điểm chấm</span><div className="approval-chips"><i>{String(payload.score ?? "—")}</i></div></div>
        <div className="approval-field"><span>Độ tin cậy</span><div className="approval-chips"><i>{Math.round(Number(payload.confidence || 0) * 100)}%</i></div></div>
        <div className="approval-field"><span>Lý do cần người kiểm tra</span>
          <div className="approval-chips">{((payload.reasons as string[]) || []).map(reason =>
            <i className="warn" key={reason}>{EVIDENCE_REASONS[reason] || reason}</i>)}</div>
        </div>
      </div>}

      {approval.type === "ESCALATION" && <div className="approval-detail">
        <div className="approval-field wide"><span>Chi tiết</span>
          <pre className="approval-raw">{JSON.stringify(payload, null, 2)}</pre></div>
      </div>}

      <button className="approval-history-toggle" disabled={loadingHistory} onClick={() => void toggleHistory()}>
        <Icon name="clock"/>{loadingHistory ? "Đang tải..." : history ? "Ẩn lịch sử" : "Xem lịch sử"}
      </button>
      {historyError && <p className="approval-empty">{historyError}</p>}
      {history && <ol className="approval-history">
        {history.length ? history.map(entry => <li key={entry.id}>
          <b>{auditLabel(entry.action)}</b>
          <span>{actorLabel(entry) || "Hệ thống tự động"}</span>
          <time dateTime={entry.created_at}>{fullDateLabel(entry.created_at)}</time>
        </li>) : <li className="approval-empty">Chưa có thao tác nào được ghi nhận.</li>}
      </ol>}
    </div>

    <div className="approval-actions">
      <button className="secondary compact" disabled={actionBusy} onClick={() => void onResolve(approval, "REJECT")}>Trả lại</button>
      <button className="primary compact" disabled={actionBusy} onClick={() => void onResolve(approval, "APPROVE")}>
        {pendingAction?.key === `approval-${approval.id}` ? "Đang xử lý..." : approval.type === "EVIDENCE" ? "Đã kiểm tra" : "Phê duyệt"}
      </button>
    </div>
  </article>;
}

function InterviewsView({ dashboard, onChanged }: { dashboard: Dashboard; onChanged: () => Promise<void> }) {
  type Ops = { reminders: { id: string; status: string; due_at: string }[]; scorecards: { id: string; interviewer_email: string; recommendation: string }[]; feedback_summary?: { summary: string; strengths: string[]; concerns: string[]; conflicts: unknown[]; sources: unknown[] } };
  const [selectedId, setSelectedId] = useState("");
  const [ops, setOps] = useState<Ops | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const selectedInterview = dashboard.interviews?.find(item => item.id === selectedId);
  const person = selectedInterview ? dashboard.applications.find(item => item.id === selectedInterview.application_id) : undefined;
  const loadOps = async (id: string) => { setBusy(true); setMessage(""); try { setSelectedId(id); setOps(await request<Ops>(`/api/interviews/${id}/operations`)); } catch (error) { setMessage(error instanceof Error ? error.message : "Không tải được operations"); } finally { setBusy(false); } };
  const submitScorecard = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (!selectedInterview) return; setBusy(true); setMessage("");
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      await request(`/api/interviews/${selectedInterview.id}/scorecards`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ interviewer_email: values.interviewer_email, recommendation: values.recommendation, note: values.note, answers: [{ criterion: values.criterion, rating: Number(values.rating), evidence: values.evidence }] }) });
      event.currentTarget.reset(); await loadOps(selectedInterview.id); setMessage("Đã lưu scorecard và tạo feedback summary có nguồn.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không lưu được scorecard"); setBusy(false); }
  };
  const markNoShow = async () => { if (!selectedInterview) return; setBusy(true); try { await request(`/api/interviews/${selectedInterview.id}/no-show`, { method: "POST" }); setMessage("Đã chuyển no-show vào approval inbox."); await loadOps(selectedInterview.id); } catch (error) { setMessage(error instanceof Error ? error.message : "Không cập nhật được"); setBusy(false); } };
  const confirmInterview = async (id: string) => {
    setBusy(true); setMessage("");
    try {
      await request(`/api/interviews/${id}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: "HR confirmed candidate-selected slot" }) });
      await onChanged(); setMessage("Đã xác nhận lịch và gửi email phản hồi cho ứng viên.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không xác nhận được lịch"); }
    finally { setBusy(false); }
  };
  const rubric = person?.screening.interview_kit?.rubric || [];
  return <section className="panel jobs-view interview-ops"><div className="panel-head"><div><h2>Interview operations</h2><p>Ứng viên giữ slot trước; HR xác nhận để tạo lịch và gửi email.</p></div><span className="bounded-badge">Human-gated</span></div>{message && <p className="operations-message interview-message">{message}</p>}{dashboard.interviews?.length ? dashboard.interviews.map(interview => { const candidate = dashboard.applications.find(item => item.id === interview.application_id); return <article className="job-row" key={interview.id}><div className="metric-icon green"><Icon name="calendar"/></div><div><h3>{dateLabel(interview.start_at)}</h3><p>{candidate?.candidate.name || "Ứng viên"} · đổi lịch {interview.reschedule_count || 0} lần</p></div>{interview.meeting_url && <a href={interview.meeting_url} target="_blank" rel="noreferrer">Mở phòng họp</a>}<i className={`status ${interview.status === "NO_SHOW" ? "rejected" : interview.status === "SCHEDULED" ? "interview" : "review"}`}>{interview.status === "PENDING_CONFIRMATION" ? "Chờ HR xác nhận" : interview.status}</i>{interview.status === "PENDING_CONFIRMATION" ? <button className="primary compact" disabled={busy} onClick={() => void confirmInterview(interview.id)}>{busy ? "Đang xác nhận..." : "Xác nhận lịch"}</button> : <button className="secondary compact" disabled={busy} onClick={() => void loadOps(interview.id)}>Operations</button>}</article>; }) : <div className="empty-state">Chưa có lịch phỏng vấn. Duyệt Top 5 để tự động gửi link chọn lịch.</div>}
    {selectedInterview && <div className="operations-detail"><div className="panel-head"><div><span className="eyebrow">FOLLOW-UP AGENT</span><h3>{person?.candidate.name || "Ứng viên"}</h3></div><button className="danger-link" disabled={busy || selectedInterview.status === "NO_SHOW"} onClick={() => void markNoShow()}>Đánh dấu no-show</button></div>{busy && <InlineProgress label="Đang đồng bộ interview operations"/>}{message && <p className="operations-message">{message}</p>}<div className="operations-grid"><div><h4>Reminder</h4>{ops?.reminders.length ? ops.reminders.map(item => <p key={item.id}><b>{dateLabel(item.due_at)}</b><span>{item.status}</span></p>) : <small>Chưa có reminder.</small>}</div><div><h4>Scorecard</h4>{ops?.scorecards.length ? ops.scorecards.map(item => <p key={item.id}><b>{item.interviewer_email}</b><span>{item.recommendation}</span></p>) : <small>Đang chờ feedback.</small>}</div></div>{ops?.feedback_summary && <article className="feedback-summary"><h4>Feedback summary</h4><p>{ops.feedback_summary.summary}</p><small>Nguồn: {ops.feedback_summary.sources.length} scorecard · Mâu thuẫn: {ops.feedback_summary.conflicts.length}</small></article>}<form className="scorecard-form" onSubmit={submitScorecard}><h4>Nộp scorecard có cấu trúc</h4><input name="interviewer_email" type="email" required placeholder="interviewer@company.com"/><select name="criterion" required defaultValue={rubric[0]?.criterion || "Technical capability"}>{rubric.map(item => <option key={item.criterion}>{item.criterion}</option>)}{!rubric.length && <option>Technical capability</option>}</select><select name="rating" defaultValue="3"><option value="1">1 — Không đạt</option><option value="2">2</option><option value="3">3 — Trung bình</option><option value="4">4</option><option value="5">5 — Xuất sắc</option></select><select name="recommendation" defaultValue="MIXED"><option value="STRONG_YES">Strong yes</option><option value="YES">Yes</option><option value="MIXED">Mixed</option><option value="NO">No</option><option value="STRONG_NO">Strong no</option></select><textarea name="evidence" required placeholder="Evidence quan sát được trong buổi phỏng vấn"/><textarea name="note" placeholder="Ghi chú bổ sung"/><button className="primary compact" disabled={busy}>Lưu scorecard</button></form></div>}
  </section>;
}

function AuditLogView({ dashboard }: { dashboard: Dashboard }) {
  const PAGE_SIZE = 50;
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [group, setGroup] = useState("all");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");

  const load = async (offset: number) => {
    const next = await request<AuditLog[]>(`/api/audit-logs?limit=${PAGE_SIZE}&offset=${offset}`);
    setHasMore(next.length === PAGE_SIZE);
    setLogs(current => offset === 0 ? next : [...current, ...next]);
  };
  useEffect(() => {
    void load(0)
      .catch(err => setError(err instanceof Error ? err.message : "Không tải được lịch sử"))
      .finally(() => setLoading(false));
  }, []);
  const loadMore = async () => {
    setLoadingMore(true);
    try { await load(logs.length); }
    catch (err) { setError(err instanceof Error ? err.message : "Không tải thêm được"); }
    finally { setLoadingMore(false); }
  };

  const counts = useMemo(() => {
    const result: Record<string, number> = { all: logs.length, decision: 0, screening: 0, interview: 0, system: 0 };
    logs.forEach(item => { result[auditGroup(item.action)] += 1; });
    return result;
  }, [logs]);
  const visible = useMemo(
    () => group === "all" ? logs : logs.filter(item => auditGroup(item.action) === group),
    [logs, group],
  );
  const candidateName = (applicationId: string | null) =>
    applicationId ? dashboard.applications.find(item => item.id === applicationId)?.candidate.name : undefined;

  return <section className="panel audit-view">
    <div className="panel-head">
      <div><h2>Lịch sử thao tác</h2><p>Toàn bộ hành động đã thực hiện trên hệ thống, mới nhất trước</p></div>
      <span className="bounded-badge">{logs.length} bản ghi</span>
    </div>
    {error && <div className="error-banner"><b>Không tải được lịch sử.</b> {error}</div>}
    <div className="candidate-tabs audit-tabs">
      {[["all", "Tất cả"], ["decision", "Quyết định"], ["screening", "Xử lý CV"], ["interview", "Phỏng vấn"], ["system", "Hệ thống"]].map(([key, label]) =>
        <button key={key} className={group === key ? "active" : ""} onClick={() => setGroup(key)}>{label}<span>{counts[key]}</span></button>)}
    </div>
    {loading ? <InlineProgress label="Đang tải lịch sử thao tác"/>
      : !visible.length ? <div className="empty-state">Chưa có thao tác nào được ghi nhận.</div>
      : <ol className="audit-list">
          {visible.map(entry => {
            const name = candidateName(entry.application_id);
            const detail = auditDetail(entry);
            const actor = actorLabel(entry);
            return <li className="audit-row" key={entry.id}>
              <i className={`audit-dot ${auditGroup(entry.action)}`} aria-hidden="true"/>
              <div className="audit-body">
                <div className="audit-line">
                  <b>{auditLabel(entry.action)}</b>
                  <span className={`audit-tag ${auditGroup(entry.action)}`}>{AUDIT_GROUP_LABELS[auditGroup(entry.action)]}</span>
                  {actor
                    ? <span className="audit-actor">Người thực hiện: <b>{actor}</b></span>
                    : <span className="audit-actor system">Hệ thống tự động</span>}
                </div>
                {name && <span className="audit-who">Ứng viên: {name}</span>}
                {detail && <p className="audit-detail">{detail}</p>}
              </div>
              <time className="audit-time" dateTime={entry.created_at}>{fullDateLabel(entry.created_at)}</time>
            </li>;
          })}
        </ol>}
    {hasMore && group === "all" && !loading &&
      <button className="secondary compact audit-more" disabled={loadingMore} onClick={() => void loadMore()}>
        {loadingMore ? "Đang tải..." : "Xem thêm"}
      </button>}
  </section>;
}

function MailSandboxView() {
  const [config, setConfig] = useState<MailSandbox | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [baseEmail, setBaseEmail] = useState("vinhvp.khmtk36@gmail.com");
  const [maxAlias, setMaxAlias] = useState(100);
  const [aliasNumber, setAliasNumber] = useState(1);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const load = async () => {
    try {
      const value = await request<MailSandbox>("/api/mail-sandbox");
      setConfig(value); setEnabled(value.enabled); setBaseEmail(value.base_email || "vinhvp.khmtk36@gmail.com"); setMaxAlias(value.max_alias || 100);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không tải được Mail Sandbox"); }
  };
  const loadIntegrations = async () => {
    try { setIntegrations(await request<Integration[]>("/api/integrations")); }
    catch { setIntegrations([]); }
  };
  useEffect(() => { void load(); void loadIntegrations(); }, []);
  const connectGoogle = async () => {
    setBusy(true); setMessage("");
    try {
      const result = await request<{ authorization_url: string }>("/api/integrations/google/authorize", { method: "POST" });
      window.location.href = result.authorization_url;
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không mở được Google OAuth"); setBusy(false); }
  };
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      const value = await request<MailSandbox>("/api/mail-sandbox", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled, base_email: baseEmail, max_alias: maxAlias }),
      });
      setConfig(value); setMessage("Đã lưu whitelist. Email ngoài danh sách sẽ bị chặn trước khi gửi.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không lưu được whitelist"); }
    finally { setBusy(false); }
  };
  const sendTest = async () => {
    setBusy(true); setMessage("");
    try {
      const result = await request<{ recipient: string; stored_recipient: string; status: string; simulated: boolean }>("/api/mail-sandbox/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias_number: aliasNumber, subject: `TalentFlow test +${aliasNumber}`, body: `Email thử nghiệm cho alias +${aliasNumber}.` }),
      });
      setMessage(`${result.simulated ? "Đã mô phỏng" : "Đã gửi"} tới ${result.recipient}. Recipient được lưu: ${result.stored_recipient}.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không gửi được email thử"); }
    finally { setBusy(false); }
  };
  const [local, domain] = baseEmail.toLowerCase().split("@");
  const preview = local && domain ? `${local}+${aliasNumber}@${domain}` : "Alias chưa hợp lệ";
  const google = integrations.find(item => item.provider === "google" && item.status === "ACTIVE");
  return <section className="panel jobs-view mail-sandbox-view"><div className="panel-head"><div><h2>Mail Sandbox</h2><p>Whitelist Gmail plus alias cho tester; alias được giữ nguyên trong dữ liệu và email header.</p></div><span className={`sandbox-state ${config?.enabled ? "on" : "off"}`}>{config?.enabled ? "ENABLED" : "DISABLED"}</span></div>
    <div className="mail-sandbox-grid"><form onSubmit={save}><h3>Cấu hình whitelist</h3><label><span>Email inbox chính</span><input type="email" value={baseEmail} onChange={event => setBaseEmail(event.target.value)} required/></label><label><span>Alias tối đa</span><input type="number" min="1" max="10000" value={maxAlias} onChange={event => setMaxAlias(Number(event.target.value))} required/></label><label className="sandbox-toggle"><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)}/><span>Bật chặn recipient ngoài whitelist</span></label><button className="primary compact" disabled={busy}>Lưu whitelist</button></form>
      <div><h3>Gửi email thử</h3><label><span>Hậu tố + number</span><input type="number" min="1" max={maxAlias} value={aliasNumber} onChange={event => setAliasNumber(Number(event.target.value))}/></label><div className="alias-preview"><small>Email sẽ gửi tới</small><b>{preview}</b><span>Gmail nhận tại {baseEmail}; TalentFlow vẫn lưu địa chỉ alias phía trên.</span></div><button className="primary compact" type="button" disabled={busy || !config?.enabled} onClick={() => void sendTest()}>{busy ? "Đang xử lý..." : "Gửi email test"}</button></div></div>
    <div className="gmail-connect"><div><h3>Gmail gửi thật</h3><p>{google ? `Đã kết nối ${google.account_email || "Google"}` : "Kết nối tài khoản Gmail gửi qua OAuth. Mail Sandbox vẫn chặn mọi email ngoài whitelist."}</p></div><button className={google ? "secondary compact" : "primary compact"} type="button" disabled={busy || !config?.enabled} onClick={() => void connectGoogle()}>{google ? "Kết nối lại Gmail" : "Kết nối Gmail"}</button></div>
    {message && <p className="operations-message">{message}</p>}
    <div className="alias-list"><h3>Alias mẫu trong whitelist</h3><div>{config?.sample_aliases.map(alias => <code key={alias}>{alias}</code>)}</div></div>
  </section>;
}

function BatchStatusPanel({ batch, actionBusy, pendingAction, onRetry }: { batch: BatchResult; actionBusy: boolean; pendingAction: PendingAction | null; onRetry: (batchId: string, itemId: string) => Promise<void> }) {
  return <section className="panel jobs-view"><div className="panel-head"><div><h2>Batch screening gần nhất</h2><p>{batch.processed}/{batch.total} hoàn tất · {batch.completed} thành công · {batch.skipped} trùng · {batch.failed} lỗi</p></div><i className={`status ${batch.status === "COMPLETED" ? "interview" : batch.status === "PROCESSING" ? "review" : "rejected"}`}>{batch.status}</i></div>{batch.items.map(item => <article className="job-row" key={item.id}><div className="metric-icon blue"><Icon name="spark"/></div><div><h3>{item.application?.candidate.name || item.filename}</h3><p>{item.filename}{item.error ? ` · ${item.error}` : ""}</p></div><i className={`status ${item.status === "COMPLETED" || item.status === "DUPLICATE" ? "interview" : item.status === "FAILED" ? "rejected" : "review"}`}>{item.status}</i>{item.status === "FAILED" && item.application && <button className="secondary compact" disabled={actionBusy} onClick={() => void onRetry(batch.batch_id, item.id)}>{pendingAction?.key === `retry-${item.id}` ? "Đang retry..." : "Retry"}</button>}</article>)}</section>;
}

function CandidateDrawer({ application, actionBusy, pendingAction, scoreClass, onClose, onReview }: { application: Application; actionBusy: boolean; pendingAction: PendingAction | null; scoreClass: (score: number) => string; onClose: () => void; onReview: (decision: ReviewDecision) => Promise<void> }) {
  const kit = application.screening.interview_kit;
  const unavailable = ["PROCESSING", "SCREENING_FAILED"].includes(application.status);
  return <div className="overlay" onMouseDown={onClose}><aside className="drawer" onMouseDown={e => e.stopPropagation()}><button className="close" disabled={actionBusy} onClick={onClose}>×</button><div className="drawer-person"><i className="avatar large violet">{initials(application.candidate.name)}</i><div><span className="eyebrow">CANDIDATE PROFILE</span><h2>{application.candidate.name}</h2><p>{application.candidate.email} · {application.screening.experience_years} năm kinh nghiệm</p>{application.resume_filename && <span className="resume-link"><Icon name="upload"/>Đã extract · {application.resume_filename}</span>}</div></div><div className="overall"><div><span>Mức độ phù hợp</span><strong>{application.screening.final_score}%</strong></div><i className={`score-ring large ${scoreClass(application.screening.final_score)}`} style={{"--score": `${application.screening.final_score * 3.6}deg`} as React.CSSProperties}>{Math.round(application.screening.final_score)}</i></div>{pendingAction?.key.startsWith("review-") && <InlineProgress label={pendingAction.label}/>}<h3 className="evidence-title">AI Evidence</h3>{unavailable && <p className="kit-summary">{application.status === "PROCESSING" ? "Agent đang xử lý hồ sơ này." : "Screening thất bại; hãy retry từ batch."}</p>}<div className="evidence-list">{application.screening.evidence.map(e => <div className="evidence" key={e.requirement}><i className={e.matched ? "found" : "missing"}>{e.matched ? "✓" : "?"}</i><div><div><b>{e.requirement}</b><span>{Math.round(e.confidence*100)}% tin cậy</span></div><p>“{e.evidence}”</p></div></div>)}</div>{kit && <><h3 className="evidence-title">Bộ câu hỏi phỏng vấn</h3><p className="kit-summary">{kit.summary}</p><div className="question-list">{kit.questions.map((item, index) => <article key={`${item.type}-${index}`}><span>{item.type}</span><b>{item.question}</b><p>{item.signal}</p></article>)}</div><div className="rubric-list">{kit.rubric.map(item => <span key={item.criterion}>{item.criterion}<b>{item.weight}%</b></span>)}</div></>}<h3 className="evidence-title">Pipeline</h3><div className="compact-pipeline">{application.pipeline.map(step => <span key={step.node}><i>{step.status === "completed" ? "✓" : "○"}</i>{step.node}</span>)}</div><div className="drawer-actions four"><button className="secondary" disabled={actionBusy || unavailable} onClick={() => void onReview("MANUAL_REVIEW")}>{pendingAction?.key === `review-MANUAL_REVIEW-${application.id}` ? "Đang lưu..." : "Xem xét"}</button><button className="danger" disabled={actionBusy || unavailable} onClick={() => void onReview("REJECT")}>{pendingAction?.key === `review-REJECT-${application.id}` ? "Đang từ chối..." : "Từ chối"}</button><button className="secondary" disabled={actionBusy || unavailable} onClick={() => void onReview("ARCHIVE")}>{pendingAction?.key === `review-ARCHIVE-${application.id}` ? "Đang lưu..." : "Lưu trữ"}</button><button className="primary" disabled={actionBusy || unavailable} onClick={() => void onReview("INTERVIEW")}><Icon name="calendar"/>{pendingAction?.key === `review-INTERVIEW-${application.id}` ? "Đang xử lý..." : "Mời PV"}</button></div></aside></div>;
}

function JobModal({ submitting, progress, onClose, onSubmit }: { submitting: boolean; progress: ProgressState | null; onClose: () => void; onSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void> }) {
  return <div className="modal-layer"><form className="modal form-modal" onSubmit={onSubmit}><button type="button" className="close" disabled={submitting} onClick={onClose}>×</button><span className="eyebrow">NEW POSITION</span><h2>Tạo việc làm</h2><p>AI sẽ tự trích xuất kỹ năng và số năm kinh nghiệm từ mô tả.</p><fieldset disabled={submitting}><label>Tên vị trí<input name="title" required minLength={2} placeholder="Backend Python Developer"/></label><div className="form-grid"><label>Phòng ban<input name="department" defaultValue="Engineering" required/></label><label>Địa điểm<input name="location" defaultValue="Hồ Chí Minh · Hybrid" required/></label></div><label>Mô tả công việc<textarea name="description" required minLength={10} rows={6} placeholder="Yêu cầu Python, FastAPI, PostgreSQL. Ít nhất 2 năm kinh nghiệm..."/></label></fieldset>{progress && <div className="upload-progress job-progress" role="status" aria-live="polite"><div><b>{progress.label}</b><strong>{progress.value}%</strong></div><div className="progress-track"><i style={{ width: `${progress.value}%` }}/></div><p>{progress.detail}</p><div className="progress-steps"><span className={progress.value >= 10 ? "done" : ""}>JD</span><span className={progress.value >= 38 ? "done" : ""}>Validate</span><span className={progress.value >= 64 ? "done" : ""}>Extract</span><span className={progress.value >= 86 ? "done" : ""}>Dashboard</span></div></div>}<button className="primary submit" disabled={submitting}>{submitting ? progress?.label || "Đang tạo..." : "Tạo việc làm"}</button></form></div>;
}

function UploadModal({ jobs, submitting, progress, onClose, onSubmit }: { jobs: Job[]; submitting: boolean; progress: ProgressState | null; onClose: () => void; onSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void> }) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const total = selectedFiles.reduce((sum, file) => sum + file.size, 0);
  return <div className="modal-layer"><form className="modal form-modal" onSubmit={onSubmit}><button type="button" className="close" disabled={submitting} onClick={onClose}>×</button><span className="eyebrow">AI SCREENING</span><h2>Tải nhiều CV</h2><p>Chọn tối đa 20 file PDF, DOCX, TXT, mỗi file tối đa 10 MB. Chỉ text extract được lưu.</p><fieldset disabled={submitting}><label>Việc làm<select name="job_id" required>{jobs.map(job => <option key={job.id} value={job.id}>{job.title}</option>)}</select></label><label className={selectedFiles.length ? "file-drop selected" : "file-drop"} htmlFor="cv-file">{selectedFiles.length ? <><i className="file-check">✓</i><b>{selectedFiles.length} CV đã chọn</b><span>{(total / 1024 / 1024).toFixed(2)} MB · Bấm để chọn lại</span></> : <><Icon name="upload"/><b>Chọn nhiều CV từ máy</b><span>PDF, DOCX hoặc TXT</span></>}<input id="cv-file" name="files" type="file" multiple accept=".pdf,.docx,.txt" required onChange={event => setSelectedFiles(Array.from(event.target.files || []).slice(0, 20))}/></label>{selectedFiles.length > 0 && <div className="selected-files">{selectedFiles.map(file => <span key={`${file.name}-${file.size}`}>{file.name}<small>{(file.size / 1024).toFixed(0)} KB</small></span>)}</div>}</fieldset>{progress && <div className="upload-progress" role="status" aria-live="polite"><div><b>{progress.label}</b><strong>{progress.value}%</strong></div><div className="progress-track"><i style={{ width: `${progress.value}%` }}/></div><p>{progress.detail}</p><div className="progress-steps"><span className={progress.value >= 5 ? "done" : ""}>Upload</span><span className={progress.value >= 45 ? "done" : ""}>Extract</span><span className={progress.value >= 65 ? "done" : ""}>AI Profile</span><span className={progress.value >= 82 ? "done" : ""}>Evidence</span></div></div>}<button className="primary submit" disabled={submitting || !selectedFiles.length}>{submitting ? progress?.label || "Đang xử lý..." : `Extract ${selectedFiles.length || "nhiều"} CV`}</button></form></div>;
}
