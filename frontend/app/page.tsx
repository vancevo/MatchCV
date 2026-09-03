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
  screening: { final_score: number; recommendation: string; evidence: Evidence[]; experience_years: number; interview_kit?: InterviewKit };
  pipeline: Step[];
};
type Job = {
  id: string; title: string; department: string; location: string; description: string; status: string; applications_count: number;
  requirements?: { required_skills?: string[]; preferred_skills?: string[]; minimum_experience?: number; approval?: { status: string; note?: string }; shortlist_approval?: { status: string; application_ids?: string[] } };
};
type Interview = { id: string; application_id: string; start_at: string; status: string; meeting_url: string };
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

export default function Home() {
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(!supabase);
  const [dashboard, setDashboard] = useState<Dashboard>(emptyDashboard);
  const [active, setActive] = useState("Tổng quan");
  const [selected, setSelected] = useState<Application | null>(null);
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<"job" | "upload" | "schedule" | null>(null);
  const [candidateTab, setCandidateTab] = useState("all");
  const [slots, setSlots] = useState<{ start_at: string; duration_minutes: number }[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<ProgressState | null>(null);
  const [jobProgress, setJobProgress] = useState<ProgressState | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [lastBatch, setLastBatch] = useState<BatchResult | null>(null);

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
    try { setDashboard(await request<Dashboard>("/api/dashboard")); setError(""); }
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
        setSlots(await request("/api/interviewers/recruiter-1/available-slots"));
        setModal("schedule");
      } else { setSelected(null); notify(`Đã cập nhật: ${statusLabel(updated.status)}`); }
    });
  };

  const approveCriteria = async (jobId: string) => {
    await runAction(`criteria-${jobId}`, "Đang duyệt tiêu chí", async () => {
      await request(`/api/jobs/${jobId}/approve-criteria`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved: true, note: "Recruiter approved criteria" }) });
      await loadDashboard(); notify("Đã duyệt tiêu chí tuyển dụng");
    });
  };

  const approveShortlist = async (jobId: string) => {
    await runAction(`shortlist-${jobId}`, "Đang duyệt Top 5", async () => {
      const data = await request<{ items: Application[] }>(`/api/jobs/${jobId}/shortlist?limit=5`);
      const application_ids = data.items.map(item => item.id);
      if (!application_ids.length) { notify("Job này chưa có CV để shortlist"); return; }
      await request(`/api/jobs/${jobId}/approve-shortlist`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ application_ids, note: "Recruiter approved AI top 5 shortlist" }) });
      await loadDashboard(); notify(`Đã duyệt Top ${application_ids.length} ứng viên`);
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
        {[["Tổng quan","grid"],["Việc làm","briefcase"],["Ứng viên","users"],["Phỏng vấn","calendar"]].map(([label,icon]) =>
          <button key={label} className={active === label ? "nav-item active" : "nav-item"} onClick={() => setActive(label)}><Icon name={icon}/>{label}{label === "Ứng viên" && <span className="count">{dashboard.metrics.awaiting_review}</span>}</button>)}
        <p className="nav-label section">AI AGENT</p><button className={active === "Pipeline" ? "nav-item active" : "nav-item"} onClick={() => setActive("Pipeline")}><Icon name="spark"/>Pipeline <span className="live-dot"/></button>
      </nav>
      <div className="agent-card"><div className="agent-icon"><Icon name="spark"/></div><b>Agent đang hoạt động</b><p>Pipeline đã xử lý {dashboard.metrics.candidates} CV.</p><div className="agent-progress"><span/></div><small>Dữ liệu đồng bộ từ API</small></div>
      <div className="profile"><div className="avatar dark">VN</div><div><b>{session?.user.email || "Vinh Nguyễn"}</b><span>Recruiter</span></div><button aria-label="Đăng xuất" disabled={actionBusy} onClick={() => void signOut()}>↪</button></div>
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

        {active === "Việc làm" ? <JobsView jobs={dashboard.jobs} actionBusy={actionBusy} pendingAction={pendingAction} onCreate={() => setModal("job")} onDelete={deleteJob} onApproveCriteria={approveCriteria} onApproveShortlist={approveShortlist} onExportReport={exportReport}/>
        : active === "Phỏng vấn" ? <InterviewsView dashboard={dashboard}/>
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
    {modal === "schedule" && current && <div className="modal-layer"><div className="modal"><button className="close" disabled={actionBusy} onClick={() => setModal(null)}>×</button><span className="eyebrow">SCHEDULING AGENT</span><h2>Chọn lịch phỏng vấn</h2><p>Các lịch trống được lấy trực tiếp từ API.</p>{pendingAction?.key.startsWith("book-") && <InlineProgress label={pendingAction.label}/>}<div className="slots">{slots.map(slot => <button key={slot.start_at} disabled={actionBusy} onClick={() => void book(slot.start_at)}>{pendingAction?.key === `book-${slot.start_at}` ? "Đang đặt lịch..." : dateLabel(slot.start_at)}<Icon name="arrow"/></button>)}</div></div></div>}
    {toast && <div className="toast"><Icon name="check"/>{toast}</div>}
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

function JobsView({ jobs, actionBusy, pendingAction, onCreate, onDelete, onApproveCriteria, onApproveShortlist, onExportReport }: {
  jobs: Job[];
  actionBusy: boolean;
  pendingAction: PendingAction | null;
  onCreate: () => void;
  onDelete: (jobId: string) => Promise<void>;
  onApproveCriteria: (jobId: string) => Promise<void>;
  onApproveShortlist: (jobId: string) => Promise<void>;
  onExportReport: (job: Job) => Promise<void>;
}) {
  return <section className="panel jobs-view"><div className="panel-head"><div><h2>Việc làm đang tuyển</h2><p>{jobs.length} vị trí từ API</p></div><button disabled={actionBusy} onClick={onCreate}><Icon name="plus"/>Tạo mới</button></div>{jobs.map(job => {
    const required = job.requirements?.required_skills || [];
    const preferred = job.requirements?.preferred_skills || [];
    const criteriaApproved = job.requirements?.approval?.status === "APPROVED";
    const shortlistApproved = job.requirements?.shortlist_approval?.status === "APPROVED";
    return <article className="job-row job-row-detailed" key={job.id}><div className="metric-icon purple"><Icon name="briefcase"/></div><div><h3>{job.title}</h3><p>{job.department} · {job.location}</p><div className="job-requirements">{required.map(item => <span key={item}>{item}</span>)}{preferred.map(item => <span className="soft" key={item}>{item}</span>)}{Boolean(job.requirements?.minimum_experience) && <span>{job.requirements?.minimum_experience}+ năm</span>}</div></div><span>{job.applications_count} ứng viên</span><i className={criteriaApproved ? "status interview" : "status review"}>{criteriaApproved ? "Tiêu chí đã duyệt" : "Chờ duyệt tiêu chí"}</i><div className="job-actions"><button className="secondary compact" disabled={actionBusy || criteriaApproved} onClick={() => void onApproveCriteria(job.id)}>{pendingAction?.key === `criteria-${job.id}` ? "Đang duyệt..." : "Duyệt tiêu chí"}</button><button className="primary compact" disabled={actionBusy || !job.applications_count || shortlistApproved} onClick={() => void onApproveShortlist(job.id)}>{pendingAction?.key === `shortlist-${job.id}` ? "Đang duyệt..." : shortlistApproved ? "Đã duyệt Top 5" : "Duyệt Top 5"}</button><button className="secondary compact" disabled={actionBusy || !job.applications_count} onClick={() => void onExportReport(job)}>{pendingAction?.key === `export-${job.id}` ? "Đang xuất..." : "Xuất report"}</button><button className="danger-link" disabled={actionBusy} onClick={() => void onDelete(job.id)}>{pendingAction?.key === `delete-${job.id}` ? "Đang xoá..." : "Xoá"}</button></div></article>;
  })}</section>;
}

function InterviewsView({ dashboard }: { dashboard: Dashboard }) {
  return <section className="panel jobs-view"><div className="panel-head"><div><h2>Lịch phỏng vấn</h2><p>Các lịch đã đặt thành công</p></div></div>{dashboard.interviews?.length ? dashboard.interviews.map(interview => { const person = dashboard.applications.find(item => item.id === interview.application_id); return <article className="job-row" key={interview.id}><div className="metric-icon green"><Icon name="calendar"/></div><div><h3>{dateLabel(interview.start_at)}</h3><p>{person?.candidate.name || "Ứng viên"}</p></div><a href={interview.meeting_url} target="_blank" rel="noreferrer">Mở phòng họp</a><i className="status review">Đã đặt</i></article>; }) : <div className="empty-state">Chưa có lịch phỏng vấn. Mở hồ sơ ứng viên để mời phỏng vấn.</div>}</section>;
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
