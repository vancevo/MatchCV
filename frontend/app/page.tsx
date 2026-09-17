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
  status_changed_at?: string | null; status_changed_by?: string;
  deleted_at?: string | null; deleted_by?: string; created_at?: string | null;
  candidate: { name: string; email: string };
  screening: {
    final_score: number; raw_score?: number; confidence?: number; recommendation: string;
    evidence: Evidence[]; experience_years: number; interview_kit?: InterviewKit;
    routing?: { decision: string; reasons: string[] };
    rule_score?: number; experience_score?: number; semantic_score?: number; preferred_score?: number;
    screening_source?: string;
  };
  pipeline: Step[];
};
type Job = {
  id: string; title: string; department: string; location: string; description: string; status: string; applications_count: number;
  requirements?: { required_skills?: string[]; preferred_skills?: string[]; minimum_experience?: number; approval?: { status: string; note?: string; version?: number }; shortlist_approval?: { status: string; application_ids?: string[] } };
};
type Interview = { id: string; application_id: string; start_at: string; end_at?: string; status: string; meeting_url: string; reschedule_count?: number; outcome?: string };
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
  allowed_emails?: string[];
  allowed_entries?: { email: string; source: string; candidate: string; added_at: string }[];
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
    file: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></>,
    ban: <><circle cx="12" cy="12" r="9"/><path d="m6 6 12 12"/></>,
    trash: <><path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13M9 7V4h6v3"/></>,
    undo: <><path d="M4 10h10a5 5 0 0 1 0 10h-3"/><path d="m4 10 4-4M4 10l4 4"/></>,
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
  DELETED: "Đã xoá",
} as Record<string, string>)[status] || status;
const statusTone = (status: string) => status === "DELETED" ? "rejected" : status === "WAITING_REVIEW" ? "review" : status === "REJECTED" ? "rejected" : status === "ARCHIVED" ? "archived" : status.startsWith("INTERVIEW") ? "interview" : "manual";
const agoLabel = (value: string) => {
  const minutes = Math.round((Date.now() - new Date(value).getTime()) / 60000);
  if (minutes < 1) return "vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} giờ trước`;
  return `${Math.round(minutes / 1440)} ngày trước`;
};
const dateLabel = (value: string) => new Intl.DateTimeFormat("vi-VN", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
const fullDateLabel = (value: string) => new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));

const AUDIT_LABELS: Record<string, string> = {
  JOB_CREATED: "Tạo vị trí tuyển dụng",
  JOB_DESCRIPTION_UPDATED: "Sửa mô tả công việc",
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
  INTERVIEW_POLICY_UPDATED: "Cập nhật lịch làm việc / chính sách phỏng vấn",
  BUSY_BLOCK_ADDED: "Thêm khoảng bận",
  BUSY_BLOCK_REMOVED: "Xoá khoảng bận",
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
  if (["RECRUITER_REVIEWED", "CRITERIA_APPROVED", "CRITERIA_REVISION_REQUESTED", "SHORTLIST_APPROVED", "CRITERIA_VERSION_CREATED", "SHORTLIST_TRIGGER_UPDATED", "JOB_CREATED", "JOB_DESCRIPTION_UPDATED"].includes(action)) return "decision";
  if (action.startsWith("SCREENING") || action.startsWith("CV_") || action === "RESCREEN_QUEUED" || action === "INTERVIEW_KIT_GENERATED") return "screening";
  if (action.startsWith("INTERVIEW") || action.startsWith("SCHEDULING") || action.startsWith("CANDIDATE_RESCHEDULE")
      || action.startsWith("BUSY_BLOCK") || action === "SCORECARD_SUBMITTED") return "interview";
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
  const [explained, setExplained] = useState<Application | null>(null);
  const [rowResume, setRowResume] = useState<{ resume: Resume; name: string } | null>(null);
  const [resumeBusy, setResumeBusy] = useState("");
  const [rejecting, setRejecting] = useState<Application | null>(null);
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
    if (candidateTab === "deleted") return item.status === "DELETED";
    // Everything except what the recruiter cleared out; deleted has its own tab.
    return item.status !== "DELETED";
  }), [dashboard.applications, candidateTab]);
  const filtered = useMemo(() => tabbedApplications.filter(item =>
    `${item.candidate.name} ${item.candidate.email}`.toLowerCase().includes(query.toLowerCase())), [tabbedApplications, query]);
  const current = selected ? dashboard.applications.find(item => item.id === selected.id) || selected : null;
  const latest = dashboard.applications[0];
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

  // Mở CV và từ chối ngay từ danh sách: trước đây phải mở hồ sơ chi tiết chỉ để làm một việc.
  const openResumeFor = async (item: Application) => {
    if (resumeBusy) return;
    setResumeBusy(item.id);
    try { setRowResume({ resume: await request<Resume>(`/api/applications/${item.id}/resume`), name: item.candidate.name }); }
    catch (err) { notify(err instanceof Error ? err.message : "Không mở được CV của ứng viên này"); }
    finally { setResumeBusy(""); }
  };

  const rejectFromRow = async (item: Application, reason: string) => {
    await runAction(`review-REJECT-${item.id}`, "Đang từ chối ứng viên", async () => {
      await request<Application>(`/api/applications/${item.id}/review`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: "REJECT", note: reason.trim() || "Recruiter từ chối ứng viên" }),
      });
      setRejecting(null);
      if (selected?.id === item.id) setSelected(null);
      await loadDashboard();
      notify(`Đã từ chối ${item.candidate.name}`);
    });
  };

  const saveCriteria = async (jobId: string, criteria: { required_skills: string[]; preferred_skills: string[]; minimum_experience: number }) => {
    await runAction(`criteria-${jobId}`, "Đang lưu tiêu chí", async () => {
      await request(`/api/jobs/${jobId}/criteria`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...criteria, note: "Recruiter reviewed criteria" }) });
      setModal(null); setCriteriaJob(null); await loadDashboard();
      notify("Đã lưu tiêu chí. Vào tab “Phê duyệt” để duyệt trước khi dùng chấm điểm.");
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

  const resolveApproval = async (approval: Approval, decision: "APPROVE" | "REJECT", note = "") => {
    await runAction(`approval-${approval.id}`, `Đang ${decision === "APPROVE" ? "phê duyệt" : "trả lại"}`, async () => {
      await request(`/api/approvals/${approval.id}/resolve`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, note: note.trim() || (decision === "APPROVE" ? "Recruiter phê duyệt" : "Recruiter trả lại") }),
      });
      await loadDashboard();
      notify(decision === "APPROVE" ? "Đã phê duyệt đề xuất" : "Đã trả lại kèm lý do");
    });
  };

  const softDelete = async (item: Application) => {
    await runAction(`delete-${item.id}`, `Đang xoá ${item.candidate.name}`, async () => {
      await request(`/api/applications/${item.id}/delete`, { method: "POST" });
      if (selected?.id === item.id) setSelected(null);
      await loadDashboard();
      notify(`Đã chuyển ${item.candidate.name} vào mục đã xoá`);
    });
  };

  const restore = async (item: Application) => {
    await runAction(`restore-${item.id}`, `Đang khôi phục ${item.candidate.name}`, async () => {
      await request(`/api/applications/${item.id}/restore`, { method: "POST" });
      await loadDashboard();
      notify(`Đã khôi phục ${item.candidate.name}`);
    });
  };

  const resolveMany = async (items: Approval[], decision: "APPROVE" | "REJECT", note = "") => {
    await runAction(`approval-bulk-${decision}`,
      `Đang ${decision === "APPROVE" ? "phê duyệt" : "trả lại"} ${items.length} đề xuất`, async () => {
        let done = 0; const failed: string[] = [];
        for (const item of items) {
          // Sequential on purpose: each resolve writes an audit row and may fan out email, and a
          // burst of parallel writes against SQLite is how you get "database is locked".
          try {
            await request(`/api/approvals/${item.id}/resolve`, {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ decision, note: note.trim() || (decision === "APPROVE" ? "Recruiter phê duyệt hàng loạt" : "Recruiter trả lại hàng loạt") }),
            });
            done += 1;
          } catch { failed.push(item.title); }
        }
        await loadDashboard();
        notify(failed.length
          ? `Xong ${done}/${items.length}. Lỗi: ${failed.slice(0, 2).join(", ")}${failed.length > 2 ? "…" : ""}`
          : `Đã ${decision === "APPROVE" ? "phê duyệt" : "trả lại"} ${done} đề xuất`);
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
        {[["Tổng quan","grid"],["Việc làm","briefcase"],["Ứng viên","users"],["Phê duyệt","bell"],["Phỏng vấn","calendar"],["Lịch làm việc","clock"],["Lịch sử","clock"],["Mail Sandbox","bell"],["Xoá dữ liệu","users"]].map(([label,icon]) =>
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
        <section className="welcome"><div><span className="eyebrow">TALENTFLOW · LIVE DASHBOARD</span><h1>{active === "Tổng quan" ? "Chào buổi sáng, Vinh 👋" : active}</h1><p>Dữ liệu và hoạt động được cập nhật trực tiếp từ API.</p></div><button className="upload" onClick={() => setModal("upload")} disabled={!dashboard.jobs.some(job => job.requirements?.approval?.status === "APPROVED") || actionBusy}><Icon name="upload"/>Tải CV lên</button></section>
        {error && <div className="error-banner"><b>Không kết nối được backend.</b> {error} — kiểm tra {API_URL.includes("localhost") ? "API tại cổng 8000" : "backend Render"}.</div>}
        {lastBatch && <BatchStatusPanel batch={lastBatch} actionBusy={actionBusy} pendingAction={pendingAction} onRetry={retryBatchItem}/>}
        {loading && <DashboardSkeleton/>}

        {(active === "Tổng quan" || active === "Pipeline") && <section className="metrics">
          {[{icon:"briefcase",label:"Việc làm đang mở",value:dashboard.metrics.open_jobs,tone:"purple"},{icon:"users",label:"Tổng ứng viên",value:dashboard.metrics.candidates,tone:"blue"},{icon:"spark",label:"Chờ đánh giá",value:dashboard.metrics.awaiting_review,tone:"amber"},{icon:"calendar",label:"Phỏng vấn đã đặt",value:dashboard.metrics.interviews,tone:"green"}].map(m =>
            <article className="metric" key={m.label}><div className={`metric-icon ${m.tone}`}><Icon name={m.icon}/></div><div><p>{m.label}</p><strong>{loading ? "—" : m.value}</strong><span className={`delta ${m.tone}`}>Live</span></div></article>)}
        </section>}

        {active === "Việc làm" ? <JobsView jobs={dashboard.jobs} applications={dashboard.applications} actionBusy={actionBusy} pendingAction={pendingAction} onCreate={() => setModal("job")} onDelete={deleteJob} onReviewCriteria={job => { setCriteriaJob(job); setModal("criteria"); }} onApproveShortlist={approveShortlist} onExportReport={exportReport} onJobChanged={loadDashboard}/>
        : active === "Phê duyệt" ? <ApprovalInbox approvals={approvals} dashboard={dashboard} actionBusy={actionBusy} pendingAction={pendingAction}
                                                  onResolve={resolveApproval} onResolveMany={resolveMany}
                                                  resumeBusy={resumeBusy} onExplain={setExplained}
                                                  onViewResume={openResumeFor} onReject={setRejecting}/>
        : active === "Phỏng vấn" ? <InterviewsView dashboard={dashboard} onChanged={loadDashboard}/>
        : active === "Lịch làm việc" ? <AvailabilityView dashboard={dashboard}/>
        : active === "Lịch sử" ? <AuditLogView dashboard={dashboard}/>
        : active === "Mail Sandbox" ? <MailSandboxView/>
        : active === "Xoá dữ liệu" ? <ClearDataView dashboard={dashboard} onCleared={async () => { setSelected(null); setLastBatch(null); await loadDashboard(); }}/>
        : <><div className="dashboard-grid">
          <section className="panel candidates-panel"><div className="panel-head"><div><h2>{active === "Ứng viên" ? "Tất cả ứng viên" : "Ứng viên mới nhất"}</h2><p>Được AI xếp hạng theo mức độ phù hợp</p></div><button onClick={() => { setActive("Ứng viên"); setQuery(""); }}>Xem tất cả <Icon name="arrow"/></button></div>
            {active === "Ứng viên" && <div className="candidate-tabs">{[
              ["all", "Tất cả", dashboard.applications.filter(item => item.status !== "DELETED").length],
              ["waiting", "Chờ duyệt", dashboard.applications.filter(item => item.status === "WAITING_REVIEW").length],
              ["reviewed", "Xem xét", dashboard.applications.filter(item => item.status === "REVIEWED").length],
              ["interview", "Phỏng vấn", dashboard.applications.filter(item => item.status.startsWith("INTERVIEW")).length],
              ["rejected", "Từ chối", dashboard.applications.filter(item => item.status === "REJECTED").length],
              ["archived", "Lưu trữ", dashboard.applications.filter(item => item.status === "ARCHIVED").length],
              ["deleted", "Đã xoá", dashboard.applications.filter(item => item.status === "DELETED").length],
            ].map(([key, label, count]) => <button key={key} className={candidateTab === key ? "active" : ""} onClick={() => setCandidateTab(String(key))}>{label}<span>{count}</span></button>)}</div>}
            <div className="table-head"><span>ỨNG VIÊN</span><span>ĐỘ PHÙ HỢP</span><span>TRẠNG THÁI</span><span/></div><div className="candidate-list">
              {filtered.map((item, index) => <div className="candidate-row" key={item.id}>
                <button className="candidate-open" onClick={() => setSelected(item)}>
                  <span className="person"><i className={`avatar ${["violet","blue","orange"][index%3]}`}>{initials(item.candidate.name)}</i><span><b>{item.candidate.name}</b><small>{item.candidate.email}</small></span></span>
                  <span className="match"><i className={`score-ring ${scoreClass(item.screening.final_score)}`} style={{"--score": `${item.screening.final_score * 3.6}deg`} as React.CSSProperties}>{Math.round(item.screening.final_score)}</i><span><b>{item.screening.recommendation}</b><small>{item.screening.final_score}% match</small></span></span>
                  <span className="status-cell">
                    <i className={`status ${statusTone(item.status)}`}>{statusLabel(item.status)}</i>
                    {item.status_changed_at && <small className="status-when"
                      title={`${fullDateLabel(item.status_changed_at)}${item.status_changed_by ? ` · ${item.status_changed_by}` : ""}`}>
                      {agoLabel(item.status_changed_at)}
                      {item.status_changed_by ? ` · ${item.status_changed_by.split("@")[0]}` : ""}
                    </small>}
                  </span>
                </button>
                <div className="row-actions">
                  <button className="why-button" disabled={!item.screening.evidence?.length}
                          title={item.screening.evidence?.length ? "Xem vì sao ứng viên được điểm này" : "Chưa có kết quả chấm điểm"}
                          onClick={() => setExplained(item)}><Icon name="spark"/>Vì sao?</button>
                  <button className="why-button" disabled={resumeBusy === item.id}
                          title="Mở toàn bộ CV ứng viên đã nộp"
                          onClick={() => void openResumeFor(item)}><Icon name="file"/>{resumeBusy === item.id ? "Đang mở" : "Xem CV"}</button>
                  {item.status === "DELETED"
                    ? <button className="why-button restore" disabled={actionBusy}
                              title="Đưa hồ sơ trở lại danh sách"
                              onClick={() => void restore(item)}><Icon name="undo"/>Khôi phục</button>
                    : <>
                        <button className="why-button reject" disabled={actionBusy || item.status === "REJECTED"}
                                title={item.status === "REJECTED" ? "Ứng viên này đã bị từ chối" : "Từ chối ngay, không cần mở hồ sơ"}
                                onClick={() => setRejecting(item)}><Icon name="ban"/>Từ chối</button>
                        <button className="why-button delete" disabled={actionBusy}
                                title="Chuyển vào mục đã xoá, khôi phục lại được"
                                onClick={() => void softDelete(item)}><Icon name="trash"/>Xoá</button>
                      </>}
                </div>
              </div>)}
              {!filtered.length && <div className="empty-state">Không tìm thấy ứng viên phù hợp.</div>}
            </div>
          </section>
          <section className="panel pipeline-panel"><div className="panel-head"><div><h2>AI Pipeline</h2><p>Hồ sơ có điểm cao nhất</p></div><span className="running"><i/> Đồng bộ</span></div>{latest ? <><div className="pipeline-summary"><div className="avatar violet">{initials(latest.candidate.name)}</div><div><b>{latest.candidate.name}</b><small>{dashboard.jobs.find(j => j.id === latest.job_id)?.title}</small></div><strong>{latest.screening.final_score}</strong></div><div className="steps">{latest.pipeline.map(step => <div className={step.status === "waiting" ? "step waiting" : "step"} key={step.node}><i>{step.status !== "waiting" && <Icon name="check"/>}</i><span><b>{step.node}</b><small>{step.status === "waiting" ? "Đang chờ quyết định của bạn" : "Hoàn thành"}</small></span></div>)}</div><button className="pipeline-button" onClick={() => setSelected(latest)}>Xem chi tiết pipeline <Icon name="arrow"/></button></> : <div className="empty-state">Tải CV đầu tiên để chạy pipeline.</div>}</section>
        </div>
        <section className="bottom-grid"><article className="insight"><div className="insight-icon"><Icon name="spark"/></div><div><span>PHÂN BỐ ĐIỂM THỰC</span><h3>{dashboard.applications.length ? `Điểm trung bình ${Math.round(dashboard.applications.reduce((sum, item) => sum + item.screening.final_score, 0) / dashboard.applications.length)}%` : "Chưa có dữ liệu"}</h3><p>Mỗi cột là điểm của một CV gần đây.</p></div><div className="mini-chart dynamic">{chartScores.map((score, index) => <i key={index} style={{ height: `${Math.max(6, score * .36)}px` }} title={`${score}%`}/>)}</div></article><article className="next-interview"><div><span>HOẠT ĐỘNG TIẾP THEO</span><h3>{dashboard.metrics.awaiting_review} hồ sơ chờ đánh giá</h3><p>Bấm vào ứng viên để xem evidence và ra quyết định.</p></div><button className="secondary compact" onClick={() => setActive("Ứng viên")}>Xem ngay</button></article></section></>}
      </div>
    </main>

    {explained && <ScoreExplainer application={explained} job={dashboard.jobs.find(item => item.id === explained.job_id)}
                                  onClose={() => setExplained(null)}
                                  onOpenProfile={() => { setSelected(explained); setExplained(null); }}/>}
    {rowResume && <ResumeViewer resume={rowResume.resume} candidateName={rowResume.name} onClose={() => setRowResume(null)}/>}
    {rejecting && <RejectCandidateDialog application={rejecting} busy={actionBusy}
                                         onClose={() => { if (!actionBusy) setRejecting(null); }}
                                         onConfirm={reason => void rejectFromRow(rejecting, reason)}/>}
    {current && <CandidateDrawer application={current} actionBusy={actionBusy} pendingAction={pendingAction} scoreClass={scoreClass} onClose={() => { if (!actionBusy) setSelected(null); }} onReview={review}/>}
    {modal === "job" && <JobModal submitting={submitting} progress={jobProgress} onClose={() => setModal(null)} onSubmit={createJob}/>}
    {modal === "upload" && <UploadModal jobs={dashboard.jobs} submitting={submitting} progress={uploadProgress} onClose={() => setModal(null)} onSubmit={uploadCV}/>} 
    {modal === "criteria" && criteriaJob && <CriteriaModal job={criteriaJob} busy={actionBusy} onClose={() => { setModal(null); setCriteriaJob(null); }} onSubmit={criteria => saveCriteria(criteriaJob.id, criteria)}/>}
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
    : <><span className="eyebrow">{data.mode === "reschedule" ? "ĐỔI LỊCH TRONG POLICY" : "CHỌN LỊCH PHỎNG VẤN"}</span>
      <h1>Chào {data.candidate_name}</h1>
      <p>
        {data.mode === "reschedule" && data.interview ? <>Lịch hiện tại: <b>{dateLabel(data.interview.start_at)}</b>. </> : null}
        Chọn một khung giờ cho vị trí <b>{data.job_title}</b>. Mỗi buổi kéo dài {data.duration_minutes} phút,
        giờ hiển thị theo múi giờ trên thiết bị của bạn.
      </p>
      <SlotPicker slots={data.slots} busy={busy} onChoose={choose}/>
    </>}
  </section></main>;
}

const VN_WEEKDAY = ["Chủ nhật", "Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7"];
const PART_OF_DAY = (hour: number) => hour < 12 ? "Buổi sáng" : hour < 18 ? "Buổi chiều" : "Buổi tối";

/** Days first, then times inside the chosen day — a candidate picks a date before an hour. */
function SlotPicker({ slots, busy, onChoose }: {
  slots: { start_at: string; duration_minutes: number }[];
  busy: string;
  onChoose: (slot: string) => Promise<void>;
}) {
  const days = useMemo(() => {
    const grouped = new Map<string, { date: Date; times: string[] }>();
    slots.forEach(slot => {
      const date = new Date(slot.start_at);
      const key = dayKey(date);
      if (!grouped.has(key)) grouped.set(key, { date, times: [] });
      grouped.get(key)!.times.push(slot.start_at);
    });
    return Array.from(grouped.entries()).map(([key, value]) => ({ key, ...value }));
  }, [slots]);

  const [active, setActive] = useState("");
  const [chosen, setChosen] = useState("");
  const current = days.find(day => day.key === active) || days[0];

  if (!days.length) return <div className="empty-state">Hiện chưa có khung giờ trống. Vui lòng liên hệ lại nhà tuyển dụng.</div>;

  const byPart = new Map<string, string[]>();
  current.times.forEach(time => {
    const part = PART_OF_DAY(new Date(time).getHours());
    byPart.set(part, [...(byPart.get(part) || []), time]);
  });

  return <div className="slot-picker">
    <div className="slot-step">
      <span className="slot-step-no">1</span>
      <div><b>Chọn ngày</b><small>{days.length} ngày còn nhận lịch</small></div>
    </div>
    <div className="slot-days">
      {days.map(day => {
        const selected = day.key === current.key;
        return <button key={day.key} type="button" className={selected ? "slot-day on" : "slot-day"}
          disabled={Boolean(busy)} onClick={() => setActive(day.key)}>
          <span className="slot-dow">{VN_WEEKDAY[day.date.getDay()]}</span>
          <b>{String(day.date.getDate()).padStart(2, "0")}/{String(day.date.getMonth() + 1).padStart(2, "0")}</b>
          <small>{day.times.length} khung giờ</small>
        </button>;
      })}
    </div>

    <div className="slot-step">
      <span className="slot-step-no">2</span>
      <div>
        <b>Chọn giờ</b>
        <small>{VN_WEEKDAY[current.date.getDay()]}, ngày {String(current.date.getDate()).padStart(2, "0")}/{String(current.date.getMonth() + 1).padStart(2, "0")}/{current.date.getFullYear()}</small>
      </div>
    </div>
    {Array.from(byPart.entries()).map(([part, times]) => <div className="slot-part" key={part}>
      <span className="slot-part-label">{part}</span>
      <div className="slot-times">
        {times.map(time => <button key={time} type="button" disabled={Boolean(busy)}
          className={time === chosen ? "slot-time on" : "slot-time"}
          aria-pressed={time === chosen}
          onClick={() => setChosen(time)}>
          {new Date(time).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}
        </button>)}
      </div>
    </div>)}

    <div className="slot-confirm">
      {chosen ? <>
        <div className="slot-summary">
          <span>Bạn đã chọn</span>
          <b>{VN_WEEKDAY[new Date(chosen).getDay()]}, {fullDateLabel(chosen)}</b>
        </div>
        <div className="slot-confirm-actions">
          <button type="button" className="secondary compact" disabled={Boolean(busy)} onClick={() => setChosen("")}>Chọn lại</button>
          <button type="button" className="primary" disabled={Boolean(busy)} onClick={() => void onChoose(chosen)}>
            {busy === chosen ? "Đang giữ lịch..." : "Xác nhận khung giờ này"}
          </button>
        </div>
      </> : <p className="slot-empty-note">Bấm vào một khung giờ phía trên để chọn.</p>}
    </div>
  </div>;
}

type Resume = {
  application_id: string; filename: string | null; size: number | null; checksum: string | null;
  text: string; file_available: boolean; file_type: string;
};

function RejectCandidateDialog({ application, busy, onClose, onConfirm }: {
  application: Application; busy: boolean; onClose: () => void; onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  return <div className="modal-layer" onMouseDown={onClose}>
    <div className="modal cand-reject-modal" onMouseDown={event => event.stopPropagation()} role="dialog" aria-label="Từ chối ứng viên">
      <button className="close" disabled={busy} onClick={onClose}>×</button>
      <span className="eyebrow">TỪ CHỐI ỨNG VIÊN</span>
      <h2>{application.candidate.name}</h2>
      <p className="reject-sub">{application.candidate.email} · {application.screening.final_score}% phù hợp</p>
      <label className="reject-field">Lý do từ chối <small>không bắt buộc — sẽ lưu vào nhật ký thao tác</small>
        <textarea rows={3} value={reason} disabled={busy} autoFocus
                  placeholder="Ví dụ: thiếu kinh nghiệm so với yêu cầu tối thiểu"
                  onChange={event => setReason(event.target.value)}/>
      </label>
      <div className="cand-reject-actions">
        <button className="secondary" disabled={busy} onClick={onClose}>Huỷ</button>
        <button className="danger" disabled={busy} onClick={() => onConfirm(reason)}>{busy ? "Đang từ chối..." : "Xác nhận từ chối"}</button>
      </div>
    </div>
  </div>;
}

function ResumeViewer({ resume, candidateName, onClose }: {
  resume: Resume; candidateName: string; onClose: () => void;
}) {
  const [showText, setShowText] = useState(false);
  const fileUrl = `${API_URL}/api/applications/${resume.application_id}/resume-file`;
  const canEmbed = resume.file_available && resume.file_type === "pdf";
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const words = resume.text.trim() ? resume.text.trim().split(/\s+/).length : 0;
  return <div className="modal-layer resume-layer" onMouseDown={onClose}>
    <div className="resume-window" onMouseDown={event => event.stopPropagation()} role="dialog" aria-label={`CV của ${candidateName}`}>
      <header>
        <div>
          <span className="eyebrow">CV ỨNG VIÊN</span>
          <h2>{candidateName}</h2>
          <p>{resume.filename || "Nộp trực tiếp dạng văn bản"}
            {resume.size ? ` · ${(resume.size / 1024).toFixed(0)} KB` : ""} · {words} từ</p>
        </div>
        <button className="close" onClick={onClose} aria-label="Đóng">×</button>
      </header>
      {canEmbed && !showText
        ? <iframe className="resume-frame" src={fileUrl} title={`CV của ${candidateName}`}/>
        : <div className="resume-sheet"><pre>{resume.text}</pre></div>}
      <footer>
        {resume.file_available
          ? <>
              <a className="resume-action" href={fileUrl} target="_blank" rel="noreferrer">
                {resume.file_type === "pdf" ? "Mở file gốc ở tab mới" : `Tải file gốc (.${resume.file_type})`}
              </a>
              {canEmbed && <button className="resume-action ghost" onClick={() => setShowText(value => !value)}>
                {showText ? "Xem lại bản gốc" : "Xem dạng văn bản"}
              </button>}
              {!canEmbed && <span className="resume-note">
                Trình duyệt không mở được .{resume.file_type} — đang hiển thị nội dung đã trích xuất, tải file về để xem đúng định dạng
              </span>}
            </>
          : <span className="resume-note">Hồ sơ này nộp trước khi hệ thống lưu file gốc, chỉ còn nội dung đã trích xuất</span>}
      </footer>
    </div>
  </div>;
}

const SCORE_REASONS: Record<string, string> = {
  LOW_CONFIDENCE: "Độ tin cậy thấp — bằng chứng trong CV chưa đủ rõ",
  BORDERLINE_SCORE: "Điểm nằm sát ngưỡng quyết định",
  SCORE_EVIDENCE_MISMATCH: "Điểm cao nhưng thiếu bằng chứng cho kỹ năng bắt buộc",
};

function ScoreExplainer({ application, job, onClose, onOpenProfile }: {
  application: Application; job?: Job; onClose: () => void; onOpenProfile: () => void;
}) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const [resume, setResume] = useState<Resume | null>(null);
  const [loadingResume, setLoadingResume] = useState(false);
  const [resumeError, setResumeError] = useState("");

  const screening = application.screening;
  const evidence = screening.evidence || [];
  const matched = evidence.filter(item => item.matched);
  const missing = evidence.filter(item => !item.matched);
  const reasons = screening.routing?.reasons || [];
  const requiredSkills = job?.requirements?.required_skills || [];
  const preferredSkills = job?.requirements?.preferred_skills || [];
  const requiredCount = requiredSkills.length;
  const minimumExperience = job?.requirements?.minimum_experience || 0;

  const openResume = async () => {
    setLoadingResume(true); setResumeError("");
    try { setResume(await request<Resume>(`/api/applications/${application.id}/resume`)); }
    catch (err) { setResumeError(err instanceof Error ? err.message : "Không đọc được nội dung CV"); }
    finally { setLoadingResume(false); }
  };

  // One row per company requirement so the two sides line up, instead of two separate lists.
  const checklist = [
    ...evidence.map(item => ({
      label: item.requirement,
      kind: preferredSkills.includes(item.requirement) ? "Ưu tiên" : requiredSkills.includes(item.requirement) ? "Bắt buộc" : "Tiêu chí",
      met: item.matched,
      proof: item.matched ? item.evidence : "Không tìm thấy bằng chứng trong CV",
    })),
    ...(minimumExperience > 0 ? [{
      label: `Kinh nghiệm từ ${minimumExperience} năm`,
      kind: "Bắt buộc",
      met: screening.experience_years >= minimumExperience,
      proof: screening.experience_years >= minimumExperience
        ? `CV thể hiện ${screening.experience_years} năm kinh nghiệm`
        : `CV chỉ thể hiện ${screening.experience_years} năm`,
    }] : []),
  ];

  // The backend blends these four parts with fixed weights; showing the weighted points
  // makes the final number traceable instead of a black box.
  const parts = [
    { label: "Kỹ năng bắt buộc", weight: 40, score: screening.rule_score, tone: "rule" },
    { label: "Kinh nghiệm", weight: 25, score: screening.experience_score, tone: "experience" },
    { label: "Tương đồng nội dung", weight: 20, score: screening.semantic_score, tone: "semantic" },
    { label: "Kỹ năng ưu tiên", weight: 15, score: screening.preferred_score, tone: "preferred" },
  ].filter(part => typeof part.score === "number") as { label: string; weight: number; score: number; tone: string }[];

  const verdict = missing.length === 0
    ? `Khớp toàn bộ ${matched.length} tiêu chí được kiểm tra.`
    : matched.length === 0
      ? `Không khớp tiêu chí nào trong ${evidence.length} tiêu chí được kiểm tra.`
      : `Khớp ${matched.length}/${evidence.length} tiêu chí, còn thiếu ${missing.map(item => item.requirement).join(", ")}.`;

  return <div className="modal-layer" onMouseDown={onClose}>
    <div className="modal why-modal" onMouseDown={event => event.stopPropagation()} role="dialog" aria-label="Giải thích điểm">
      <button className="close" onClick={onClose} aria-label="Đóng">×</button>

      <div className="why-head">
        <i className={`score-ring large ${application.screening.final_score >= 80 ? "score-high" : application.screening.final_score >= 65 ? "score-mid" : "score-low"}`}
           style={{"--score": `${screening.final_score * 3.6}deg`} as React.CSSProperties}>{Math.round(screening.final_score)}</i>
        <div>
          <span className="eyebrow">VÌ SAO ĐIỂM NÀY</span>
          <h2>{application.candidate.name}</h2>
          <p>{job?.title || "Vị trí tuyển dụng"} · <b>{screening.recommendation}</b>
            {typeof screening.confidence === "number" && <> · độ tin cậy {Math.round(screening.confidence * 100)}%</>}</p>
        </div>
      </div>

      <p className="why-verdict">{verdict}</p>

      {reasons.length > 0 && <div className="why-flags">
        {reasons.map(reason => <span key={reason}>{SCORE_REASONS[reason] || reason}</span>)}
      </div>}

      <h3 className="why-section">Đối chiếu từng tiêu chí</h3>
      <div className="match-table">
        <div className="match-head">
          <span>Công ty yêu cầu</span>
          <span>Ứng viên đáp ứng</span>
        </div>
        {checklist.map(row => <div className={row.met ? "match-row met" : "match-row unmet"} key={row.label}>
          <span className="match-left">
            <i className="match-mark" aria-hidden="true">{row.met ? "✓" : "✕"}</i>
            <span><b>{row.label}</b><em>{row.kind}</em></span>
          </span>
          <span className="match-right">{row.met
            ? <q>{row.proof}</q>
            : <span className="match-none">{row.proof}</span>}</span>
        </div>)}
      </div>

      <button className="why-resume-toggle" disabled={loadingResume} onClick={() => void openResume()}>
        <Icon name="upload"/>{loadingResume ? "Đang mở CV..." : "Mở CV của ứng viên"}
      </button>
      {resumeError && <p className="why-note warn">{resumeError}</p>}
      {resume && <ResumeViewer resume={resume} candidateName={application.candidate.name}
                               onClose={() => setResume(null)}/>}

      <h3 className="why-section">Điểm này được cộng từ đâu</h3>
      <div className="why-parts">
        {parts.map(part => {
          const contribution = Math.round(part.score * part.weight) / 100;
          return <div className="why-part" key={part.label}>
            <div className="why-part-head">
              <b>{part.label}</b>
              <span className="why-weight">chiếm {part.weight}%</span>
              <span className="why-points">+{contribution.toFixed(1)} điểm</span>
            </div>
            <div className="why-bar"><i className={part.tone} style={{ width: `${Math.max(1, part.score)}%` }}/></div>
            <small>Đạt {Math.round(part.score)}/100 ở phần này</small>
          </div>;
        })}
      </div>
      {typeof screening.raw_score === "number" && Math.abs(screening.raw_score - screening.final_score) >= 0.1 &&
        <p className="why-note">Điểm thô {screening.raw_score} đã được hiệu chỉnh xuống {screening.final_score} theo bộ dữ liệu đối chiếu.</p>}
      {screening.screening_source === "rules" &&
        <p className="why-note">Kết quả do bộ quy tắc so khớp từ khoá tạo ra (chưa bật AI), nên chỉ nhận diện được kỹ năng viết đúng từ khoá.</p>}

      {requiredCount === 0 && <p className="why-note warn">
        Vị trí này chưa có kỹ năng bắt buộc nào, nên điểm gần như giống nhau cho mọi CV. Hãy bổ sung tiêu chí ở mục “Kiểm tra tiêu chí” rồi chấm lại.
      </p>}

      <div className="why-actions">
        <button className="secondary compact" onClick={onClose}>Đóng</button>
        <button className="primary compact" onClick={onOpenProfile}>Xem hồ sơ đầy đủ<Icon name="arrow"/></button>
      </div>
    </div>
  </div>;
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

function JobsView({ jobs, applications, actionBusy, pendingAction, onCreate, onDelete, onReviewCriteria, onApproveShortlist, onExportReport, onJobChanged }: {
  jobs: Job[];
  applications: Application[];
  actionBusy: boolean;
  pendingAction: PendingAction | null;
  onCreate: () => void;
  onDelete: (jobId: string) => Promise<void>;
  onReviewCriteria: (job: Job) => void;
  onApproveShortlist: (jobId: string) => Promise<void>;
  onExportReport: (job: Job) => Promise<void>;
  onJobChanged: () => Promise<void>;
}) {
  return <section className="panel jobs-view">
    <div className="panel-head">
      <div><h2>Việc làm đang tuyển</h2><p>{jobs.length} vị trí từ API</p></div>
      <button disabled={actionBusy} onClick={onCreate}><Icon name="plus"/>Tạo mới</button>
    </div>
    {jobs.map(job => <JobRow key={job.id} job={job} applications={applications} actionBusy={actionBusy}
                             pendingAction={pendingAction} onDelete={onDelete} onReviewCriteria={onReviewCriteria}
                             onApproveShortlist={onApproveShortlist} onExportReport={onExportReport} onJobChanged={onJobChanged}/>)}
  </section>;
}

function JobRow({ job, applications, actionBusy, pendingAction, onDelete, onReviewCriteria, onApproveShortlist, onExportReport, onJobChanged }: {
  job: Job;
  applications: Application[];
  actionBusy: boolean;
  pendingAction: PendingAction | null;
  onDelete: (jobId: string) => Promise<void>;
  onReviewCriteria: (job: Job) => void;
  onApproveShortlist: (jobId: string) => Promise<void>;
  onExportReport: (job: Job) => Promise<void>;
  onJobChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const required = job.requirements?.required_skills || [];
  const preferred = job.requirements?.preferred_skills || [];
  const approval = job.requirements?.approval;
  const criteriaApproved = approval?.status === "APPROVED";
  // Version 1 is the posting itself; anything later is an edit to something already approved.
  const isUpdate = (approval?.version || 1) > 1;
  const jobState = criteriaApproved
    ? { label: "Tiêu chí đã duyệt", tone: "interview" }
    : approval?.status === "NEEDS_REVISION"
      ? { label: isUpdate ? "Cập nhật bị trả lại" : "Công việc bị trả lại", tone: "rejected" }
      : { label: isUpdate ? "Chờ duyệt cập nhật" : "Chờ duyệt công việc mới", tone: "review" };
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
    <i className={`status ${jobState.tone}`}>{jobState.label}</i>
    <div className="job-actions">
      <button className="secondary compact" onClick={() => setOpen(value => !value)} aria-expanded={open}>
        {open ? "Ẩn chi tiết" : "Xem chi tiết"}
      </button>
      <button className="secondary compact" disabled={actionBusy} onClick={() => onReviewCriteria(job)}
              title={criteriaApproved ? "Sửa sẽ tạo phiên bản mới và cần duyệt lại" : undefined}>
        {pendingAction?.key === `criteria-${job.id}` ? "Đang lưu..." : criteriaApproved ? "Sửa tiêu chí" : "Kiểm tra tiêu chí"}
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
    {open && <JobDetail job={job} applications={applications} onJobChanged={onJobChanged}/>}
  </article>;
}

function JobDetail({ job, applications, onJobChanged }: { job: Job; applications: Application[]; onJobChanged: () => Promise<void> }) {
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
  const [description, setDescription] = useState(job.description || "");
  const [savingDescription, setSavingDescription] = useState(false);
  const [descriptionNote, setDescriptionNote] = useState("");

  const saveDescription = async () => {
    setSavingDescription(true); setDescriptionNote("");
    try {
      await request(`/api/jobs/${job.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description }),
      });
      await onJobChanged();
      setDescriptionNote("Đã lưu mô tả. Tiêu chí chấm điểm không đổi — sửa riêng ở “Kiểm tra tiêu chí”.");
    } catch (err) { setDescriptionNote(err instanceof Error ? err.message : "Không lưu được mô tả"); }
    finally { setSavingDescription(false); }
  };

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
        <textarea className="job-description-edit" rows={9} value={description}
                  disabled={savingDescription}
                  onChange={event => setDescription(event.target.value)}
                  placeholder="Mô tả yêu cầu, trách nhiệm và quyền lợi của vị trí này"/>
        <div className="job-description-actions">
          <button className="primary compact" onClick={() => void saveDescription()}
                  disabled={savingDescription || description.trim().length < 10 || description === (job.description || "")}>
            {savingDescription ? "Đang lưu..." : "Lưu mô tả"}
          </button>
          {description !== (job.description || "") && !savingDescription &&
            <button className="secondary compact" onClick={() => setDescription(job.description || "")}>Hoàn tác</button>}
        </div>
        {descriptionNote && <p className="job-description-note">{descriptionNote}</p>}
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
            <i className={`status ${CRITERIA_STATUS[version.status]?.tone || "manual"}`}>{CRITERIA_STATUS[version.status]?.label || version.status}</i>
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
  return <div className="modal-layer"><form className="modal form-modal criteria-modal" onSubmit={submit}><button type="button" className="close" disabled={busy} onClick={onClose}>×</button><span className="eyebrow">HUMAN REVIEW</span><h2>Duyệt tiêu chí · {job.title}</h2><p>Check/uncheck tiêu chí AI đã tách, hoặc bổ sung tiêu chí mới. Lưu xong cần vào tab “Phê duyệt” để duyệt.</p><fieldset disabled={busy}><h3>Bắt buộc</h3><div className="criteria-checks">{required.map((item, index) => <label key={`${item.value}-${index}`}><input type="checkbox" checked={item.checked} onChange={event => setRequired(values => values.map((entry, current) => current === index ? { ...entry, checked: event.target.checked } : entry))}/><span>{item.value}</span></label>)}</div><h3>Ưu tiên</h3><div className="criteria-checks">{preferred.map((item, index) => <label key={`${item.value}-${index}`}><input type="checkbox" checked={item.checked} onChange={event => setPreferred(values => values.map((entry, current) => current === index ? { ...entry, checked: event.target.checked } : entry))}/><span>{item.value}</span></label>)}{!preferred.length && <small>Chưa có tiêu chí ưu tiên.</small>}</div><div className="criteria-add"><input value={newCriterion} onChange={event => setNewCriterion(event.target.value)} placeholder="Bổ sung kỹ năng/tiêu chí"/><select value={newKind} onChange={event => setNewKind(event.target.value as "required" | "preferred")}><option value="required">Bắt buộc</option><option value="preferred">Ưu tiên</option></select><button type="button" className="secondary compact" onClick={add}>Thêm</button></div><label><span>Kinh nghiệm tối thiểu (năm)</span><input type="number" min="0" max="60" value={experience} onChange={event => setExperience(Number(event.target.value))}/></label><button className="primary submit" disabled={!required.some(item => item.checked)}>{busy ? "Đang lưu..." : "Lưu tiêu chí"}</button></fieldset></form></div>;
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

const scoreClass = (score: number) => score >= 80 ? "score-high" : score >= 65 ? "score-mid" : "score-low";

const APPROVAL_LABELS: Record<Approval["type"], string> = {
  CRITERIA: "Công việc", EVIDENCE: "Ứng viên", SHORTLIST: "Danh sách rút gọn", ESCALATION: "Ngoại lệ phỏng vấn",
};

/** The stored codes are English shorthand; a recruiter needs the number that triggered them. */
function escalationReasons(payload: Record<string, unknown>): string[] {
  const score = Number(payload.score);
  const confidence = Math.round(Number(payload.confidence || 0) * 100);
  const codes = Array.isArray(payload.reasons) ? (payload.reasons as string[]) : [];
  const explain: Record<string, string> = {
    LOW_CONFIDENCE: `Máy chưa chắc về kết quả chấm (độ chắc chắn ${confidence}%, dưới ngưỡng 65%)`,
    BORDERLINE_SCORE: `Điểm ${score} nằm sát ranh giới đạt/không đạt (vùng 60–75)`,
    SCORE_EVIDENCE_MISMATCH: "Điểm cao nhưng CV thiếu quá nửa kỹ năng bắt buộc — có thể là khớp nhầm",
  };
  return codes.map(code => explain[code] || code);
}

function ApprovalInbox({ approvals, dashboard, actionBusy, pendingAction, onResolve, onResolveMany,
                         resumeBusy, onExplain, onViewResume, onReject }: {
  approvals: Approval[]; dashboard: Dashboard; actionBusy: boolean; pendingAction: PendingAction | null;
  onResolve: (approval: Approval, decision: "APPROVE" | "REJECT", note?: string) => Promise<void>;
  onResolveMany: (items: Approval[], decision: "APPROVE" | "REJECT", note?: string) => Promise<void>;
  resumeBusy: string;
  onExplain: (application: Application) => void;
  onViewResume: (application: Application) => void;
  onReject: (application: Application) => void;
}) {
  const [filter, setFilter] = useState<"ALL" | Approval["type"]>("ALL");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [bulkReject, setBulkReject] = useState(false);

  const pending = approvals.filter(item => item.status === "PENDING");
  const shown = filter === "ALL" ? approvals : approvals.filter(item => item.type === filter);
  const selectable = shown.filter(item => item.status === "PENDING");
  const chosen = selectable.filter(item => picked.has(item.id));
  const allChosen = selectable.length > 0 && chosen.length === selectable.length;

  const toggle = (id: string) => setPicked(current => {
    const next = new Set(current);
    if (!next.delete(id)) next.add(id);
    return next;
  });
  const runBulk = async (decision: "APPROVE" | "REJECT", note = "") => {
    const items = [...chosen];
    setBulkReject(false); setPicked(new Set());
    await onResolveMany(items, decision, note);
  };

  const tabs: ["ALL" | Approval["type"], string][] = [
    ["ALL", "Tất cả"], ["CRITERIA", APPROVAL_LABELS.CRITERIA],
    ["EVIDENCE", APPROVAL_LABELS.EVIDENCE], ["SHORTLIST", APPROVAL_LABELS.SHORTLIST],
  ];

  return <section className="panel approval-view">
    <div className="panel-head">
      <div>
        <h2>Chờ bạn quyết định</h2>
        <p>{pending.length
          ? `${pending.length} đề xuất máy không tự quyết được, cần bạn duyệt hoặc trả lại`
          : "Không còn đề xuất nào chờ bạn"}</p>
      </div>
      <span className="bounded-badge">Máy không tự quyết</span>
    </div>

    <div className="candidate-tabs approval-tabs">
      {tabs.map(([key, label]) => {
        const count = (key === "ALL" ? approvals : approvals.filter(item => item.type === key))
          .filter(item => item.status === "PENDING").length;
        return <button key={key} className={filter === key ? "active" : ""}
                       onClick={() => { setFilter(key); setPicked(new Set()); }}>
          {label}<span>{count}</span></button>;
      })}
    </div>

    {selectable.length > 0 && <div className="bulk-bar">
      <label className="bulk-check">
        <input type="checkbox" checked={allChosen} disabled={actionBusy}
               onChange={() => setPicked(allChosen ? new Set() : new Set(selectable.map(item => item.id)))}/>
        {allChosen ? "Bỏ chọn tất cả" : `Chọn tất cả ${selectable.length} mục`}
      </label>
      {chosen.length > 0 && <>
        <span className="bulk-count">Đã chọn <b>{chosen.length}</b></span>
        <button className="secondary compact" disabled={actionBusy} onClick={() => setBulkReject(true)}>
          Trả lại {chosen.length} mục</button>
        <button className="primary compact" disabled={actionBusy} onClick={() => void runBulk("APPROVE")}>
          <Icon name="check"/>Duyệt {chosen.length} mục</button>
      </>}
    </div>}

    {pendingAction?.key.startsWith("approval-bulk-") && <InlineProgress label={pendingAction.label}/>}

    {shown.length ? shown.map(item =>
      <ApprovalCard key={item.id} approval={item} typeLabel={APPROVAL_LABELS[item.type]} dashboard={dashboard}
                    actionBusy={actionBusy} pendingAction={pendingAction} onResolve={onResolve}
                    picked={picked.has(item.id)} onPick={() => toggle(item.id)}
                    resumeBusy={resumeBusy} onExplain={onExplain}
                    onViewResume={onViewResume} onReject={onReject}/>)
      : <div className="empty-state">Không có đề xuất nào trong mục này.</div>}

    {bulkReject && <RejectDialog title={`Trả lại ${chosen.length} đề xuất`} busy={actionBusy}
                                 onCancel={() => setBulkReject(false)}
                                 onConfirm={note => runBulk("REJECT", note)}/>}
  </section>;
}

function RejectDialog({ title, busy, onCancel, onConfirm }: {
  title: string; busy: boolean; onCancel: () => void; onConfirm: (note: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const reasons = ["Tiêu chí chưa đầy đủ", "Sai yêu cầu của vị trí", "Cần bổ sung bằng chứng", "Chưa đúng thời điểm"];

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onCancel]);

  return <div className="modal-layer" onMouseDown={onCancel}>
    <div className="modal reject-modal" onMouseDown={event => event.stopPropagation()} role="dialog" aria-label="Lý do trả lại">
      <button className="close" onClick={onCancel} aria-label="Đóng">×</button>
      <span className="eyebrow">TRẢ LẠI ĐỀ XUẤT</span>
      <h2>Vì sao trả lại?</h2>
      <p className="reject-target">{title}</p>
      <div className="reject-presets">
        {reasons.map(reason => <button key={reason} type="button" className="reject-preset"
          onClick={() => setNote(current => current ? `${current}. ${reason}` : reason)}>{reason}</button>)}
      </div>
      <textarea rows={4} value={note} autoFocus maxLength={500} disabled={busy}
                placeholder="Ghi rõ cần sửa gì để người đề xuất biết đường chỉnh lại"
                onChange={event => setNote(event.target.value)}/>
      <p className="reject-hint">Lý do này được lưu vào nhật ký và hiện lại cho người đề xuất.</p>
      <div className="reject-actions">
        <button className="secondary compact" disabled={busy} onClick={onCancel}>Huỷ</button>
        <button className="danger" disabled={busy || note.trim().length < 3} onClick={() => void onConfirm(note)}>
          {busy ? "Đang gửi..." : "Xác nhận trả lại"}
        </button>
      </div>
    </div>
  </div>;
}

function ApprovalCard({ approval, typeLabel, dashboard, actionBusy, pendingAction, onResolve,
                       picked, onPick, resumeBusy, onExplain, onViewResume, onReject }: {
  approval: Approval; typeLabel: string; dashboard: Dashboard; actionBusy: boolean;
  pendingAction: PendingAction | null;
  onResolve: (approval: Approval, decision: "APPROVE" | "REJECT", note?: string) => Promise<void>;
  picked: boolean; onPick: () => void; resumeBusy: string;
  onExplain: (application: Application) => void;
  onViewResume: (application: Application) => void;
  onReject: (application: Application) => void;
}) {
  const [history, setHistory] = useState<AuditLog[] | null>(null);
  const [rejecting, setRejecting] = useState(false);
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

  const reasons = escalationReasons(payload as Record<string, unknown>);

  return <article className={`approval-row approval-card${picked ? " approval-picked" : ""}`}>
    {approval.status === "PENDING"
      ? <label className="approval-pick" title="Chọn để xử lý hàng loạt">
          <input type="checkbox" checked={picked} disabled={actionBusy} onChange={onPick}/>
        </label>
      : <div className={`approval-type ${approval.type.toLowerCase()}`}>
          <Icon name={approval.type === "SHORTLIST" ? "users" : approval.type === "CRITERIA" ? "briefcase" : "spark"}/>
        </div>}
    <div className="approval-main">
      <span className="eyebrow">{approval.type === "CRITERIA"
        ? (Number(payload.version) || 1) > 1 ? "Cập nhật công việc" : "Công việc mới"
        : typeLabel}</span>
      <h3>{candidate ? candidate.candidate.name : approval.title}</h3>
      {reasons.length
        ? <div className="approval-why">
            <b>Vì sao cần bạn xem:</b>
            <ul>{reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
          </div>
        : <p>{approval.summary}</p>}

      {candidate && <div className="approval-candidate">
        <i className={`score-ring ${scoreClass(candidate.screening.final_score)}`}
           style={{"--score": `${candidate.screening.final_score * 3.6}deg`} as React.CSSProperties}>
          {Math.round(candidate.screening.final_score)}</i>
        <div className="approval-candidate-meta">
          <b>{candidate.screening.recommendation}</b>
          <small>{candidate.candidate.email} · {candidate.screening.experience_years} năm kinh nghiệm</small>
          <span className="approval-hits">
            {candidate.screening.evidence.map(item =>
              <i key={item.requirement} className={item.matched ? "met" : "unmet"}>
                {item.matched ? "✓" : "✗"} {item.requirement}</i>)}
          </span>
        </div>
        <div className="row-actions approval-row-actions">
          <button className="why-button" disabled={!candidate.screening.evidence?.length}
                  onClick={() => onExplain(candidate)}><Icon name="spark"/>Vì sao?</button>
          <button className="why-button" disabled={resumeBusy === candidate.id}
                  onClick={() => onViewResume(candidate)}>
            <Icon name="file"/>{resumeBusy === candidate.id ? "Đang mở" : "Xem CV"}</button>
          <button className="why-button reject" disabled={actionBusy || candidate.status === "REJECTED"}
                  onClick={() => onReject(candidate)}><Icon name="ban"/>Từ chối</button>
        </div>
      </div>}

      <div className="approval-meta">
        <span>Người đề xuất: <b>{requester || "Hệ thống tự đề xuất"}</b></span>
        {approval.created_at && <span>Thời gian: <b>{fullDateLabel(approval.created_at)}</b></span>}
        {approval.status === "REJECTED" && typeof approval.resolution?.note === "string" &&
          <span className="approval-rejected">Đã trả lại: <b>{approval.resolution.note as string}</b></span>}
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

    {rejecting && <RejectDialog title={approval.title} busy={actionBusy}
      onCancel={() => setRejecting(false)}
      onConfirm={async note => { setRejecting(false); await onResolve(approval, "REJECT", note); }}/>}

    <div className="approval-actions">
      <button className="secondary compact" disabled={actionBusy} onClick={() => setRejecting(true)}>Trả lại</button>
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

type InterviewPolicy = {
  reminder_minutes: number[]; max_reschedules: number; feedback_due_hours: number;
  timezone_name: string; working_days: number[]; working_start_hour: number; working_end_hour: number;
};
type BusyBlock = { id: string; start_at: string; end_at: string; note: string };

const CRITERIA_STATUS: Record<string, { label: string; tone: string }> = {
  APPROVED: { label: "Đã duyệt", tone: "interview" },
  PENDING: { label: "Chờ duyệt", tone: "review" },
  NEEDS_REVISION: { label: "Đã trả lại", tone: "rejected" },
  REJECTED: { label: "Đã từ chối", tone: "rejected" },
  SUPERSEDED: { label: "Đã thay thế", tone: "archived" },
};

const WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];
/** Monday-first weekday index, matching the backend's working_days numbering. */
const weekdayIndex = (date: Date) => (date.getDay() + 6) % 7;
const dayKey = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const cellKey = (day: string, hour: number) => `${day}:${hour}`;
const shortDay = (date: Date) => `${String(date.getDate()).padStart(2, "0")}/${String(date.getMonth() + 1).padStart(2, "0")}`;

/** Contiguous hours on the same day become one block, so a full day is one row rather than eight. */
function selectionToBlocks(selection: Set<string>): { start_at: string; end_at: string }[] {
  const byDay = new Map<string, number[]>();
  selection.forEach(key => {
    const [day, hour] = key.split(":");
    byDay.set(day, [...(byDay.get(day) || []), Number(hour)]);
  });
  const blocks: { start_at: string; end_at: string }[] = [];
  byDay.forEach((hours, day) => {
    hours.sort((left, right) => left - right);
    let from = hours[0];
    let previous = hours[0];
    hours.slice(1).forEach(hour => {
      if (hour !== previous + 1) {
        blocks.push({
          start_at: new Date(`${day}T${String(from).padStart(2, "0")}:00`).toISOString(),
          end_at: new Date(`${day}T${String(previous + 1).padStart(2, "0")}:00`).toISOString(),
        });
        from = hour;
      }
      previous = hour;
    });
    blocks.push({
      start_at: new Date(`${day}T${String(from).padStart(2, "0")}:00`).toISOString(),
      end_at: new Date(`${day}T${String(previous + 1).padStart(2, "0")}:00`).toISOString(),
    });
  });
  return blocks;
}

function AvailabilityView({ dashboard }: { dashboard: Dashboard }) {
  const [policy, setPolicy] = useState<InterviewPolicy | null>(null);
  const [blocks, setBlocks] = useState<BusyBlock[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = async () => {
    const [nextPolicy, nextBlocks] = await Promise.all([
      request<InterviewPolicy>("/api/interview-policy"),
      request<BusyBlock[]>("/api/busy-blocks"),
    ]);
    setPolicy(nextPolicy); setBlocks(nextBlocks);
  };
  useEffect(() => {
    void load().catch(err => setMessage(err instanceof Error ? err.message : "Không tải được lịch"))
      .finally(() => setLoading(false));
  }, []);

  const save = async (next: InterviewPolicy) => {
    setPolicy(next); setBusy(true); setMessage("");
    try {
      setPolicy(await request<InterviewPolicy>("/api/interview-policy", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next),
      }));
      setMessage("Đã lưu. Ứng viên sẽ không còn thấy những khung giờ này.");
    } catch (err) { setMessage(err instanceof Error ? err.message : "Không lưu được"); await load(); }
    finally { setBusy(false); }
  };

  const toggleDay = (day: number) => {
    if (!policy) return;
    const days = policy.working_days.includes(day)
      ? policy.working_days.filter(value => value !== day)
      : [...policy.working_days, day].sort((left, right) => left - right);
    if (!days.length) { setMessage("Phải giữ lại ít nhất một ngày làm việc."); return; }
    void save({ ...policy, working_days: days });
  };

  if (loading) return <section className="panel"><InlineProgress label="Đang tải lịch làm việc"/></section>;
  if (!policy) return <section className="panel"><div className="error-banner">{message || "Không tải được lịch"}</div></section>;

  return <section className="panel availability-view">
    <div className="panel-head">
      <div><h2>Lịch làm việc & lịch bận</h2><p>Ứng viên chỉ chọn được những khung giờ còn trống ở đây</p></div>
      <span className="bounded-badge">{blocks.length} khoảng bận đã khai</span>
    </div>
    {message && <p className="operations-message">{message}</p>}

    <div className="availability-block">
      <h3>Khung giờ làm việc</h3>
      <div className="availability-row">
        <span className="availability-label">Ngày làm việc</span>
        <div className="day-toggles">
          {WEEKDAYS.map((label, day) => <button key={label} type="button" disabled={busy}
            className={policy.working_days.includes(day) ? "day-toggle on" : "day-toggle"}
            aria-pressed={policy.working_days.includes(day)}
            onClick={() => toggleDay(day)}>{label}</button>)}
        </div>
      </div>
      <div className="availability-row">
        <span className="availability-label">Giờ làm việc</span>
        <div className="availability-fields">
          <select disabled={busy} value={policy.working_start_hour}
                  onChange={event => void save({ ...policy, working_start_hour: Number(event.target.value) })}>
            {Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{String(hour).padStart(2, "0")}:00</option>)}
          </select>
          <span>đến</span>
          <select disabled={busy} value={policy.working_end_hour}
                  onChange={event => void save({ ...policy, working_end_hour: Number(event.target.value) })}>
            {Array.from({ length: 24 }, (_, index) => index + 1).map(hour =>
              <option key={hour} value={hour}>{String(hour).padStart(2, "0")}:00</option>)}
          </select>
          <select disabled={busy} value={policy.timezone_name}
                  onChange={event => void save({ ...policy, timezone_name: event.target.value })}>
            {["Asia/Ho_Chi_Minh", "Asia/Bangkok", "Asia/Singapore", "Asia/Tokyo", "UTC"].map(zone =>
              <option key={zone} value={zone}>{zone}</option>)}
          </select>
        </div>
      </div>
    </div>

    <BusyBoard policy={policy} blocks={blocks} busy={busy}
               interviews={dashboard.interviews || []} applications={dashboard.applications}
               onSaved={async note => { setBlocks(await request<BusyBlock[]>("/api/busy-blocks")); setMessage(note); }}
               setBusy={setBusy} onError={setMessage}/>

  </section>;
}

function BusyBoard({ policy, blocks, interviews, applications, busy, setBusy, onSaved, onError }: {
  policy: InterviewPolicy; blocks: BusyBlock[]; interviews: Interview[]; applications: Application[];
  busy: boolean;
  setBusy: (value: boolean) => void;
  onSaved: (note: string) => Promise<void>;
  onError: (note: string) => void;
}) {
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [painting, setPainting] = useState<boolean | null>(null);
  const [dirty, setDirty] = useState(false);
  const today = dayKey(new Date());

  useEffect(() => {
    const stop = () => setPainting(null);
    window.addEventListener("mouseup", stop);
    return () => window.removeEventListener("mouseup", stop);
  }, []);

  // Saved blocks are the source of truth until the grid is edited, so a reload shows them again.
  useEffect(() => {
    const marked = new Set<string>();
    blocks.forEach(block => {
      const end = new Date(block.end_at);
      const cursor = new Date(block.start_at);
      cursor.setMinutes(0, 0, 0);
      while (cursor < end) {
        marked.add(cellKey(dayKey(cursor), cursor.getHours()));
        cursor.setHours(cursor.getHours() + 1);
      }
    });
    setSelection(marked);
    setDirty(false);
  }, [blocks]);

  const hours = Array.from({ length: Math.max(0, policy.working_end_hour - policy.working_start_hour) },
                           (_, index) => policy.working_start_hour + index);

  // Always this week plus the next two, rolling forward on its own as days pass.
  const weeks = useMemo(() => {
    const monday = new Date();
    monday.setHours(0, 0, 0, 0);
    monday.setDate(monday.getDate() - weekdayIndex(monday));
    return [0, 1, 2].map(offset => Array.from({ length: 7 }, (_, index) => {
      const date = new Date(monday);
      date.setDate(monday.getDate() + offset * 7 + index);
      return date;
    }).filter(date => policy.working_days.includes(weekdayIndex(date))));
  }, [policy.working_days]);

  const interviewCells = useMemo(() => {
    const map = new Map<string, string>();
    interviews.forEach(interview => {
      if (interview.status === "CANCELLED") return;
      const name = applications.find(item => item.id === interview.application_id)?.candidate.name || "Ứng viên";
      const start = new Date(interview.start_at);
      // Older records may not carry an end time; an interview slot is an hour by default.
      const end = interview.end_at ? new Date(interview.end_at) : new Date(start.getTime() + 3600000);
      const cursor = new Date(start);
      cursor.setMinutes(0, 0, 0);
      while (cursor < end) {
        map.set(cellKey(dayKey(cursor), cursor.getHours()), name);
        cursor.setHours(cursor.getHours() + 1);
      }
    });
    return map;
  }, [interviews, applications]);

  const paint = (key: string, makeBusy: boolean) => {
    setSelection(current => {
      if (current.has(key) === makeBusy) return current;
      const next = new Set(current);
      if (makeBusy) next.add(key); else next.delete(key);
      return next;
    });
    setDirty(true);
  };

  const save = async () => {
    setBusy(true);
    try {
      const editable = blocks.filter(block => dayKey(new Date(block.start_at)) >= today);
      for (const block of editable) await request(`/api/busy-blocks/${block.id}`, { method: "DELETE" });

      const future = new Set(Array.from(selection).filter(key => key.split(":")[0] >= today));
      for (const block of selectionToBlocks(future)) {
        await request("/api/busy-blocks", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...block, note: "Lịch bận" }),
        });
      }
      await onSaved(`Đã lưu ${future.size} giờ bận. Ứng viên sẽ không thấy các giờ này.`);
    } catch (err) { onError(err instanceof Error ? err.message : "Không lưu được lịch bận"); }
    finally { setBusy(false); }
  };

  return <div className="availability-block">
    <div className="board-head">
      <div>
        <h3>Lịch 3 tuần</h3>
        <p className="availability-hint">Kéo chuột để tô giờ bận. Ngày đã trôi qua bị đóng băng, chỉ xem lại được.</p>
      </div>
      <div className="board-legend">
        <span><i className="swatch free"/>Trống</span>
        <span><i className="swatch busy"/>Bận</span>
        <span><i className="swatch interview"/>Có phỏng vấn</span>
        <span><i className="swatch frozen"/>Đã qua</span>
      </div>
    </div>

    {weeks.map((week, index) => <div className="planner-week" key={dayKey(week[0])}>
      <h4>{["Tuần này", "Tuần sau", "Tuần kế tiếp"][index]} · {shortDay(week[0])} – {shortDay(week[week.length - 1])}</h4>
      <div className="week-grid" style={{ gridTemplateColumns: `54px repeat(${week.length}, minmax(48px, 1fr))` }}>
        <span className="week-corner"/>
        {week.map(date => <span className={dayKey(date) === today ? "week-day current" : "week-day"} key={dayKey(date)}>
          {WEEKDAYS[weekdayIndex(date)]}<i>{shortDay(date)}</i>
        </span>)}
        {hours.map(hour => <div key={hour} style={{ display: "contents" }}>
          <span className="week-hour">{String(hour).padStart(2, "0")}:00</span>
          {week.map(date => {
            const day = dayKey(date);
            const key = cellKey(day, hour);
            const frozen = day < today;
            const candidate = interviewCells.get(key);
            const marked = selection.has(key);
            const tone = candidate ? "interview" : marked ? "busy" : "free";
            return <button key={key} type="button" disabled={busy || frozen || Boolean(candidate)}
              className={`week-cell ${tone}${frozen ? " frozen" : ""}`}
              aria-pressed={marked}
              title={candidate ? `Phỏng vấn: ${candidate}` : frozen ? "Ngày đã trôi qua" : undefined}
              aria-label={`${shortDay(date)} ${String(hour).padStart(2, "0")}:00 — ${candidate ? `phỏng vấn ${candidate}` : marked ? "bận" : "trống"}`}
              onMouseDown={() => { setPainting(!marked); paint(key, !marked); }}
              onMouseEnter={() => { if (painting !== null) paint(key, painting); }}/>;
          })}
        </div>)}
      </div>
    </div>)}

    <div className="planner-actions">
      <span>{Array.from(selection).filter(key => key.split(":")[0] >= today).length} giờ đang đánh dấu bận</span>
      <button className="primary compact" disabled={busy || !dirty} onClick={() => void save()}>
        {busy ? "Đang lưu..." : dirty ? "Lưu lịch bận" : "Đã lưu"}
      </button>
    </div>
  </div>;
}

function MailSandboxView() {
  const [config, setConfig] = useState<MailSandbox | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [baseEmail, setBaseEmail] = useState("vinhvp.khmtk36@gmail.com");
  const [maxAlias, setMaxAlias] = useState(100);
  const [aliasNumber, setAliasNumber] = useState(1);
  const [newEmail, setNewEmail] = useState("");
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
  const addEmail = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      setConfig(await request<MailSandbox>("/api/mail-sandbox/allowed-emails", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: newEmail }),
      }));
      setNewEmail(""); setMessage(`Đã cho phép gửi tới ${newEmail.trim().toLowerCase()}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không thêm được email"); }
    finally { setBusy(false); }
  };

  const removeEmail = async (email: string) => {
    setBusy(true); setMessage("");
    try {
      setConfig(await request<MailSandbox>(`/api/mail-sandbox/allowed-emails/${encodeURIComponent(email)}`,
        { method: "DELETE" }));
      setMessage(`Đã bỏ ${email} khỏi danh sách cho phép`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không xoá được email"); }
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
    <div className="alias-list"><h3>Alias mẫu của email gốc</h3><div>{config?.sample_aliases.map(alias => <code key={alias}>{alias}</code>)}</div></div>

    <div className="allow-list">
      <h3>Email được phép nhận thư thật</h3>
      <p className="allow-hint">Mỗi lần bạn mời một ứng viên phỏng vấn, email của họ được thêm vào đây,
        nếu không thư mời sẽ bị chặn. Bạn cũng có thể tự thêm hoặc bỏ bớt.</p>
      {config?.allowed_entries?.length
        ? <table className="allow-table">
            <thead><tr><th>Email</th><th>Nguồn</th><th>Thêm lúc</th><th/></tr></thead>
            <tbody>{config.allowed_entries.map(entry => <tr key={entry.email}>
              <td><code>{entry.email}</code></td>
              <td>{entry.source === "INTERVIEW"
                ? <span className="allow-src auto">Tự thêm khi mời {entry.candidate || "ứng viên"}</span>
                : <span className="allow-src manual">Bạn tự thêm</span>}</td>
              <td className="allow-when">{entry.added_at ? fullDateLabel(entry.added_at) : "—"}</td>
              <td><button type="button" disabled={busy} title="Bỏ khỏi danh sách"
                          onClick={() => void removeEmail(entry.email)}>×</button></td>
            </tr>)}</tbody>
          </table>
        : <p className="allow-empty">Chưa có email nào ngoài email gốc. Mời một ứng viên phỏng vấn
          là email của họ tự xuất hiện ở đây.</p>}
      <form className="allow-add" onSubmit={addEmail}>
        <input type="email" required placeholder="them.email@vidu.com" value={newEmail}
               disabled={busy} onChange={event => setNewEmail(event.target.value)}/>
        <button className="secondary compact" type="submit" disabled={busy || !newEmail.trim()}>Thêm email</button>
      </form>
    </div>
  </section>;
}

function BatchStatusPanel({ batch, actionBusy, pendingAction, onRetry }: { batch: BatchResult; actionBusy: boolean; pendingAction: PendingAction | null; onRetry: (batchId: string, itemId: string) => Promise<void> }) {
  return <section className="panel jobs-view"><div className="panel-head"><div><h2>Batch screening gần nhất</h2><p>{batch.processed}/{batch.total} hoàn tất · {batch.completed} thành công · {batch.skipped} trùng · {batch.failed} lỗi</p></div><i className={`status ${batch.status === "COMPLETED" ? "interview" : batch.status === "PROCESSING" ? "review" : "rejected"}`}>{batch.status}</i></div>{batch.items.map(item => <article className="job-row" key={item.id}><div className="metric-icon blue"><Icon name="spark"/></div><div><h3>{item.application?.candidate.name || item.filename}</h3><p>{item.filename}{item.error ? ` · ${item.error}` : ""}</p></div><i className={`status ${item.status === "COMPLETED" || item.status === "DUPLICATE" ? "interview" : item.status === "FAILED" ? "rejected" : "review"}`}>{item.status}</i>{item.status === "FAILED" && item.application && <button className="secondary compact" disabled={actionBusy} onClick={() => void onRetry(batch.batch_id, item.id)}>{pendingAction?.key === `retry-${item.id}` ? "Đang retry..." : "Retry"}</button>}</article>)}</section>;
}

function CandidateDrawer({ application, actionBusy, pendingAction, scoreClass, onClose, onReview }: { application: Application; actionBusy: boolean; pendingAction: PendingAction | null; scoreClass: (score: number) => string; onClose: () => void; onReview: (decision: ReviewDecision) => Promise<void> }) {
  const kit = application.screening.interview_kit;
  const unavailable = ["PROCESSING", "SCREENING_FAILED"].includes(application.status);
  // Once a candidate has been invited, reviewing or inviting again would overwrite a live invitation.
  const invited = application.status.startsWith("INTERVIEW");
  return <div className="overlay" onMouseDown={onClose}><aside className="drawer" onMouseDown={e => e.stopPropagation()}><button className="close" disabled={actionBusy} onClick={onClose}>×</button><div className="drawer-person"><i className="avatar large violet">{initials(application.candidate.name)}</i><div><span className="eyebrow">CANDIDATE PROFILE</span><h2>{application.candidate.name}</h2><p>{application.candidate.email} · {application.screening.experience_years} năm kinh nghiệm</p>{application.resume_filename && <span className="resume-link"><Icon name="upload"/>Đã extract · {application.resume_filename}</span>}</div></div><div className="overall"><div><span>Mức độ phù hợp</span><strong>{application.screening.final_score}%</strong></div><i className={`score-ring large ${scoreClass(application.screening.final_score)}`} style={{"--score": `${application.screening.final_score * 3.6}deg`} as React.CSSProperties}>{Math.round(application.screening.final_score)}</i></div>{pendingAction?.key.startsWith("review-") && <InlineProgress label={pendingAction.label}/>}<h3 className="evidence-title">AI Evidence</h3>{invited && <p className="drawer-locked">Ứng viên đã được mời phỏng vấn — không thể xem xét hay mời lại. Quản lý lịch ở tab “Phỏng vấn”.</p>}{unavailable && <p className="kit-summary">{application.status === "PROCESSING" ? "Agent đang xử lý hồ sơ này." : "Screening thất bại; hãy retry từ batch."}</p>}<div className="evidence-list">{application.screening.evidence.map(e => <div className="evidence" key={e.requirement}><i className={e.matched ? "found" : "missing"}>{e.matched ? "✓" : "?"}</i><div><div><b>{e.requirement}</b><span>{Math.round(e.confidence*100)}% tin cậy</span></div><p>“{e.evidence}”</p></div></div>)}</div>{kit && <><h3 className="evidence-title">Bộ câu hỏi phỏng vấn</h3><p className="kit-summary">{kit.summary}</p><div className="question-list">{kit.questions.map((item, index) => <article key={`${item.type}-${index}`}><span>{item.type}</span><b>{item.question}</b><p>{item.signal}</p></article>)}</div><div className="rubric-list">{kit.rubric.map(item => <span key={item.criterion}>{item.criterion}<b>{item.weight}%</b></span>)}</div></>}<h3 className="evidence-title">Pipeline</h3><div className="compact-pipeline">{application.pipeline.map(step => <span key={step.node}><i>{step.status === "completed" ? "✓" : "○"}</i>{step.node}</span>)}</div><div className="drawer-actions four"><button className="secondary" disabled={actionBusy || unavailable || invited} title={invited ? "Ứng viên đã được mời phỏng vấn" : undefined} onClick={() => void onReview("MANUAL_REVIEW")}>{pendingAction?.key === `review-MANUAL_REVIEW-${application.id}` ? "Đang lưu..." : "Xem xét"}</button><button className="danger" disabled={actionBusy || unavailable} onClick={() => void onReview("REJECT")}>{pendingAction?.key === `review-REJECT-${application.id}` ? "Đang từ chối..." : "Từ chối"}</button><button className="secondary" disabled={actionBusy || unavailable} onClick={() => void onReview("ARCHIVE")}>{pendingAction?.key === `review-ARCHIVE-${application.id}` ? "Đang lưu..." : "Lưu trữ"}</button><button className="primary" disabled={actionBusy || unavailable || invited} title={invited ? "Ứng viên đã được mời phỏng vấn" : undefined} onClick={() => void onReview("INTERVIEW")}><Icon name="calendar"/>{pendingAction?.key === `review-INTERVIEW-${application.id}` ? "Đang xử lý..." : invited ? "Đã mời PV" : "Mời PV"}</button></div></aside></div>;
}

function JobModal({ submitting, progress, onClose, onSubmit }: { submitting: boolean; progress: ProgressState | null; onClose: () => void; onSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void> }) {
  return <div className="modal-layer"><form className="modal form-modal" onSubmit={onSubmit}><button type="button" className="close" disabled={submitting} onClick={onClose}>×</button><span className="eyebrow">NEW POSITION</span><h2>Tạo việc làm</h2><p>AI sẽ tự trích xuất kỹ năng và số năm kinh nghiệm từ mô tả.</p><fieldset disabled={submitting}><label>Tên vị trí<input name="title" required minLength={2} placeholder="Backend Python Developer"/></label><div className="form-grid"><label>Phòng ban<input name="department" defaultValue="Engineering" required/></label><label>Địa điểm<input name="location" defaultValue="Hồ Chí Minh · Hybrid" required/></label></div><label>Mô tả công việc<textarea name="description" required minLength={10} rows={6} placeholder="Yêu cầu Python, FastAPI, PostgreSQL. Ít nhất 2 năm kinh nghiệm..."/></label></fieldset>{progress && <div className="upload-progress job-progress" role="status" aria-live="polite"><div><b>{progress.label}</b><strong>{progress.value}%</strong></div><div className="progress-track"><i style={{ width: `${progress.value}%` }}/></div><p>{progress.detail}</p><div className="progress-steps"><span className={progress.value >= 10 ? "done" : ""}>JD</span><span className={progress.value >= 38 ? "done" : ""}>Validate</span><span className={progress.value >= 64 ? "done" : ""}>Extract</span><span className={progress.value >= 86 ? "done" : ""}>Dashboard</span></div></div>}<button className="primary submit" disabled={submitting}>{submitting ? progress?.label || "Đang tạo..." : "Tạo việc làm"}</button></form></div>;
}

function UploadModal({ jobs, submitting, progress, onClose, onSubmit }: { jobs: Job[]; submitting: boolean; progress: ProgressState | null; onClose: () => void; onSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void> }) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const total = selectedFiles.reduce((sum, file) => sum + file.size, 0);
  // Screening runs against approved criteria only, so an unapproved job cannot accept CVs yet.
  const openJobs = jobs.filter(job => job.requirements?.approval?.status === "APPROVED");
  return <div className="modal-layer"><form className="modal form-modal" onSubmit={onSubmit}><button type="button" className="close" disabled={submitting} onClick={onClose}>×</button><span className="eyebrow">AI SCREENING</span><h2>Tải nhiều CV</h2><p>Chọn tối đa 20 file PDF, DOCX, TXT, mỗi file tối đa 10 MB. Chỉ text extract được lưu.</p><fieldset disabled={submitting}><label>Việc làm<select name="job_id" required disabled={!openJobs.length}>{openJobs.map(job => <option key={job.id} value={job.id}>{job.title}</option>)}</select></label>{!openJobs.length && <p className="upload-blocked">Chưa có vị trí nào duyệt xong tiêu chí. Vào tab “Phê duyệt” duyệt tiêu chí trước, rồi mới tải CV lên được.</p>}<label className={selectedFiles.length ? "file-drop selected" : "file-drop"} htmlFor="cv-file">{selectedFiles.length ? <><i className="file-check">✓</i><b>{selectedFiles.length} CV đã chọn</b><span>{(total / 1024 / 1024).toFixed(2)} MB · Bấm để chọn lại</span></> : <><Icon name="upload"/><b>Chọn nhiều CV từ máy</b><span>PDF, DOCX hoặc TXT</span></>}<input id="cv-file" name="files" type="file" multiple accept=".pdf,.docx,.txt" required onChange={event => setSelectedFiles(Array.from(event.target.files || []).slice(0, 20))}/></label>{selectedFiles.length > 0 && <div className="selected-files">{selectedFiles.map(file => <span key={`${file.name}-${file.size}`}>{file.name}<small>{(file.size / 1024).toFixed(0)} KB</small></span>)}</div>}</fieldset>{progress && <div className="upload-progress" role="status" aria-live="polite"><div><b>{progress.label}</b><strong>{progress.value}%</strong></div><div className="progress-track"><i style={{ width: `${progress.value}%` }}/></div><p>{progress.detail}</p><div className="progress-steps"><span className={progress.value >= 5 ? "done" : ""}>Upload</span><span className={progress.value >= 45 ? "done" : ""}>Extract</span><span className={progress.value >= 65 ? "done" : ""}>AI Profile</span><span className={progress.value >= 82 ? "done" : ""}>Evidence</span></div></div>}<button className="primary submit" disabled={submitting || !selectedFiles.length || !openJobs.length}>{submitting ? progress?.label || "Đang xử lý..." : `Extract ${selectedFiles.length || "nhiều"} CV`}</button></form></div>;
}
