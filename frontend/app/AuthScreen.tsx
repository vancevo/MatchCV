"use client";

import { FormEvent, useState } from "react";
import { supabase } from "../lib/supabase";

export default function AuthScreen() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (loading) return;
    setLoading(true); setMessage("");
    try {
      const data = new FormData(event.currentTarget);
      const email = String(data.get("email")); const password = String(data.get("password"));
      if (!supabase) throw new Error("Thiếu cấu hình Supabase ở frontend");
      const result = mode === "login"
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({ email, password });
      if (result.error) throw result.error;
      setMessage(mode === "register" && !result.data.session
        ? "Đã đăng ký. Hãy kiểm tra email để xác nhận tài khoản." : "Đăng nhập thành công.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Không thể xử lý đăng nhập");
    } finally {
      setLoading(false);
    }
  };

  return <main className="auth-page"><form className="auth-card" onSubmit={submit}>
    <div className="brand auth-brand"><div className="brandmark">✦</div><div><b>TalentFlow</b><span>AI Recruitment</span></div></div>
    <span className="eyebrow">SECURE WORKSPACE</span>
    <h1>{mode === "login" ? "Đăng nhập" : "Tạo tài khoản"}</h1>
    <p>Dữ liệu tuyển dụng được tách riêng theo tài khoản recruiter.</p>
    <fieldset disabled={loading}>
      <label>Email<input name="email" type="email" required autoComplete="email" /></label>
      <label>Mật khẩu<input name="password" type="password" required minLength={6} autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
    </fieldset>
    {loading && <div className="inline-progress auth-progress" role="status" aria-live="polite"><div><b>{mode === "login" ? "Đang đăng nhập" : "Đang đăng ký"}</b><span>Vui lòng đợi đến khi hệ thống phản hồi</span></div><i/></div>}
    {message && <div className="auth-message">{message}</div>}
    <button className="primary submit" disabled={loading}>{loading ? "Đang xử lý..." : mode === "login" ? "Đăng nhập" : "Đăng ký"}</button>
    <button type="button" className="auth-switch" disabled={loading} onClick={() => { setMode(mode === "login" ? "register" : "login"); setMessage(""); }}>
      {mode === "login" ? "Chưa có tài khoản? Đăng ký" : "Đã có tài khoản? Đăng nhập"}
    </button>
  </form></main>;
}
