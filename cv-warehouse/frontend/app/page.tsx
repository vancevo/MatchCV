"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { supabase, token } from "../lib/supabase";

type Cv = {
  id: string; full_name: string; email: string; location: string; specialization: string; specialization_label?: string; job_title?: string;
  skills: string[]; experience_years: number; original_filename: string; updated_at: string;
  ranking_score?: number; evidence?: { text: string }[];
};
type Category = { code: string; label: string; role: string; count: number };
type ProcessState = {
  label: string; detail: string; current?: number; total?: number;
};

const apiUrl = process.env.NEXT_PUBLIC_CV_WAREHOUSE_API_URL || "http://localhost:8100";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const accessToken = await token();
  const response = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers: { ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}), ...(init?.headers || {}) },
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || `HTTP ${response.status}`);
  return response.json();
}

function Login() {
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setLoading(true); setMessage("");
    const data = new FormData(event.currentTarget);
    try {
      if (!supabase) throw new Error("Thiếu cấu hình Supabase frontend.");
      const result = await supabase.auth.signInWithPassword({
        email: String(data.get("email")), password: String(data.get("password")),
      });
      setMessage(result.error?.message || "Đăng nhập thành công.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Đăng nhập thất bại.");
    } finally { setLoading(false); }
  };
  return <main className="login"><form onSubmit={submit} className="card">
    <div className="brand">◆ <span><b>Kho CV IT</b><small>TalentFlow Data Source</small></span></div>
    <h1>Đăng nhập</h1><p>Truy cập kho CV nội bộ.</p>
    <fieldset disabled={loading}><label>Email<input name="email" type="email" required /></label>
    <label>Mật khẩu<input name="password" type="password" required minLength={6} /></label></fieldset>
    {loading && <ProcessPanel process={{ label: "Đang đăng nhập", detail: "Đang xác thực tài khoản với Supabase…" }}/>}
    {message && <div className="message">{message}</div>}<button disabled={loading}>{loading ? "Đang xử lý…" : "Đăng nhập"}</button>
  </form></main>;
}

function ProcessPanel({ process }: { process: ProcessState }) {
  const hasProgress = process.total != null && process.current != null && process.total > 0;
  const percent = hasProgress ? Math.round((process.current! / process.total!) * 100) : 35;
  return <div className={`process ${hasProgress ? "determinate" : "indeterminate"}`} role="status" aria-live="polite">
    <i className="spinner"/><div><b>{process.label}</b><span>{process.detail}</span>
      <div className="progress"><i style={{ width: `${percent}%` }}/></div>
      {hasProgress && <small>{process.current}/{process.total} CV · {percent}%</small>}
    </div>
  </div>;
}

export default function Home() {
  const [sessionReady, setSessionReady] = useState(!supabase);
  const [loggedIn, setLoggedIn] = useState(!supabase);
  const [items, setItems] = useState<Cv[]>([]);
  const [query, setQuery] = useState("");
  const [skills, setSkills] = useState("");
  const [experience, setExperience] = useState("");
  const [message, setMessage] = useState("");
  const [categories, setCategories] = useState<Category[]>([]);
  const [unclassified, setUnclassified] = useState(0);
  const [category, setCategory] = useState("");
  const [process, setProcess] = useState<ProcessState | null>(null);
  const busy = process !== null;

  const loadCategories = useCallback(async () => {
    try {
      const result = await api<{ categories: Category[]; unclassified: number }>("/api/v1/filters");
      setCategories(result.categories); setUnclassified(result.unclassified);
    } catch { /* tabs are optional; the list still works */ }
  }, []);

  const load = useCallback(async (selected = category) => {
    setProcess({ label: "Đang tải Kho CV", detail: "Đang đồng bộ danh sách CV mới nhất…" });
    try {
      const filter = selected ? `&specialization=${encodeURIComponent(selected)}` : "";
      setItems((await api<{ items: Cv[] }>(`/api/v1/cvs?limit=100${filter}`)).items);
      void loadCategories();
    }
    catch (error) { setMessage(error instanceof Error ? error.message : "Không tải được CV"); }
    finally { setProcess(null); }
  }, [category, loadCategories]);

  const selectCategory = (code: string) => { setCategory(code); setQuery(""); setMessage(""); void load(code); };

  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => { setLoggedIn(Boolean(data.session)); setSessionReady(true); });
    const { data } = supabase.auth.onAuthStateChange((_event, session) => setLoggedIn(Boolean(session)));
    return () => data.subscription.unsubscribe();
  }, [load]);
  useEffect(() => { if (loggedIn) void load(); }, [loggedIn]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!sessionReady) return <main className="loading">Đang kiểm tra phiên đăng nhập…</main>;
  if (!loggedIn) return <Login />;

  const upload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setMessage("");
    const form = event.currentTarget;
    const formData = new FormData(form);
    const files = formData.getAll("files").filter((value): value is File => value instanceof File && value.size > 0);
    if (!files.length) return setMessage("Hãy chọn ít nhất 1 CV.");
    if (files.length > 50) return setMessage("Mỗi lần chỉ được upload tối đa 50 CV.");
    const source = String(formData.get("source") || "UPLOAD");
    let succeeded = 0;
    const failures: string[] = [];
    try {
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        setProcess({ label: "Đang upload và phân tích CV", detail: file.name, current: index, total: files.length });
        const body = new FormData(); body.append("file", file); body.append("source", source);
        try {
          await api("/api/v1/cvs", { method: "POST", body });
          succeeded += 1;
        } catch (error) {
          failures.push(`${file.name}: ${error instanceof Error ? error.message : "Thất bại"}`);
        }
        setProcess({ label: "Đang upload và phân tích CV", detail: file.name, current: index + 1, total: files.length });
      }
      form.reset();
      setMessage(`Đã xử lý ${files.length} CV: ${succeeded} thành công${failures.length ? `, ${failures.length} lỗi — ${failures.join("; ")}` : "."}`);
      await load();
    } finally { setProcess(null); }
  };
  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return load(category);
    setProcess({ label: "Đang Semantic Search", detail: "BGE-M3 đang đối chiếu query với các chunk CV…" }); setMessage("");
    try {
      const result = await api<{ mode: string; results: Cv[]; specialization_filter?: { code: string; label: string; source: string } | null }>("/api/v1/cvs/search", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, filters: {
          specialization: category || null,
          required_skills: skills.split(",").map(value => value.trim()).filter(Boolean),
          minimum_experience: experience ? Number(experience) : null,
        }, limit: 50 }),
      });
      setItems(result.results);
      const filter = result.specialization_filter;
      setMessage(`Chế độ tìm kiếm: ${result.mode}${filter ? ` · ${filter.source === "AUTO" ? "Tự nhận diện ngành" : "Ngành"}: ${filter.label}` : ""}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Search thất bại"); }
    finally { setProcess(null); }
  };

  const logout = async () => {
    if (!supabase) return;
    setProcess({ label: "Đang đăng xuất", detail: "Đang đóng phiên làm việc an toàn…" }); setMessage("");
    try { await supabase.auth.signOut(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Đăng xuất thất bại"); setProcess(null); }
  };

  return <main className="shell">
    <header><div className="brand">◆ <span><b>Kho CV IT</b><small>Semantic CV Warehouse</small></span></div>
      {supabase && <button className="ghost" disabled={busy} onClick={() => void logout()}>{process?.label === "Đang đăng xuất" ? "Đang đăng xuất…" : "Đăng xuất"}</button>}</header>
    <section className="hero"><div><small>PRIVATE IT TALENT SOURCE</small><h1>Kho CV tập trung</h1>
      <p>Semantic Search bằng BGE-M3, filter metadata và API riêng cho TalentFlow.</p></div>
      <form className="upload" onSubmit={upload}><label className="file-picker"><span>Chọn tối đa 50 CV</span><input name="files" type="file" accept=".pdf,.docx,.txt" multiple required disabled={busy}/></label>
        <input name="source" placeholder="Nguồn CV" defaultValue="UPLOAD" disabled={busy}/><button disabled={busy}>{process?.label.includes("upload") ? "Đang xử lý…" : "Thêm CV"}</button></form></section>
    <form className="search" onSubmit={search}><input value={query} onChange={event => setQuery(event.target.value)} placeholder={category ? `Tìm trong ngành đã chọn…` : "Tìm Backend Python, Data Engineer, DevOps…"} />
      <input value={skills} onChange={event => setSkills(event.target.value)} placeholder="Kỹ năng bắt buộc, cách nhau dấu phẩy" />
      <input className="years" value={experience} onChange={event => setExperience(event.target.value)} type="number" min="0" placeholder="Số năm" />
      <button disabled={busy}>Semantic Search</button></form>
    <nav className="categories" aria-label="Ngành CV">
      <button type="button" className={category === "" ? "active" : ""} onClick={() => selectCategory("")}>Tất cả<span>{categories.reduce((sum, item) => sum + item.count, 0) + unclassified}</span></button>
      {categories.map((item, index) => <button type="button" key={item.code} title={item.role} className={category === item.code ? "active" : ""} onClick={() => selectCategory(item.code)}>
        <i>{String(index + 1).padStart(2, "0")}</i>{item.label}<span>{item.count}</span></button>)}
      {unclassified > 0 && <button type="button" className={category === "UNCLASSIFIED" ? "active" : ""} onClick={() => selectCategory("UNCLASSIFIED")}>Chưa phân loại<span>{unclassified}</span></button>}
    </nav>
    {process && <ProcessPanel process={process}/>}
    {message && <div className="notice">{message}</div>}
    <section className="grid">{items.map(item => <article key={item.id} className="cv">
      <div className="avatar">{item.full_name.slice(0, 1).toUpperCase()}</div><div><h2>{item.full_name}</h2>
      <p>{item.specialization_label || item.specialization}{item.job_title ? ` · ${item.job_title}` : ""} · {item.experience_years} năm {item.location ? `· ${item.location}` : ""}</p>
      <div className="tags">{item.skills.slice(0, 8).map(skill => <span key={skill}>{skill}</span>)}</div>
      {item.ranking_score != null && <b className="score">{item.ranking_score.toFixed(1)} điểm</b>}
      {item.evidence?.[0]?.text && <blockquote>{item.evidence[0].text}</blockquote>}
      <small>{item.original_filename}</small></div></article>)}
      {!items.length && <div className="empty">Kho chưa có CV. Upload PDF, DOCX hoặc TXT để bắt đầu lập chỉ mục.</div>}
    </section>
  </main>;
}
