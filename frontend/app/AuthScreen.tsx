"use client";

import { FormEvent, useState } from "react";
import { supabase } from "../lib/supabase";

export default function AuthScreen() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setLoading(true); setMessage("");
    const data = new FormData(event.currentTarget);
    const email = String(data.get("email")); const password = String(data.get("password"));
    if (!supabase) { setMessage("Thiếu cấu hình Supabase ở frontend"); setLoading(false); return; }
    const result = mode === "login"
      ? await supabase.auth.signInWithPassword({ email, password })
      : await supabase.auth.signUp({ email, password });
    setMessage(result.error ? result.error.message : mode === "register" && !result.data.session
      ? "Đã đăng ký. Hãy kiểm tra email để xác nhận tài khoản." : "Đăng nhập thành công.");
    setLoading(false);
  };

  return <main className="auth-page"><form className="auth-card" onSubmit={submit}>
    <div className="brand auth-brand"><div className="brandmark">✦</div><div><b>TalentFlow</b><span>AI Recruitment</span></div></div>
    <span className="eyebrow">SECURE WORKSPACE</span>
    <h1>{mode === "login" ? "Đăng nhập" : "Tạo tài khoản"}</h1>
    <p>Dữ liệu tuyển dụng được tách riêng theo tài khoản recruiter.</p>
    <label>Email<input name="email" type="email" required autoComplete="email" /></label>
    <label>Mật khẩu<input name="password" type="password" required minLength={6} autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
    {message && <div className="auth-message">{message}</div>}
    <button className="primary submit" disabled={loading}>{loading ? "Đang xử lý..." : mode === "login" ? "Đăng nhập" : "Đăng ký"}</button>
    <button type="button" className="auth-switch" onClick={() => { setMode(mode === "login" ? "register" : "login"); setMessage(""); }}>
      {mode === "login" ? "Chưa có tài khoản? Đăng ký" : "Đã có tài khoản? Đăng nhập"}
    </button>
  </form></main>;
}
