"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Zap, LogIn } from "lucide-react";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      router.push("/research");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <div className="glass" style={{ width: "100%", maxWidth: 400, padding: "40px 32px" }}>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 24 }}>
          <div style={{ width: 48, height: 48, borderRadius: 12, background: "var(--accent-dim)", border: "1px solid var(--accent)", display: "grid", placeItems: "center" }}>
            <Zap size={22} color="var(--accent)" />
          </div>
        </div>
        <h1 style={{ textAlign: "center", fontSize: "1.3rem", fontWeight: 700, marginBottom: 4 }}>Welcome Back</h1>
        <p style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: "0.85rem", marginBottom: 24 }}>
          Sign in to Stocks With Gaurav
        </p>

        {error && (
          <div style={{ padding: "8px 12px", marginBottom: 16, borderRadius: 6, background: "rgba(255,71,87,0.1)", border: "1px solid rgba(255,71,87,0.2)", color: "#ff4757", fontSize: "0.82rem" }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <label style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required className="input-dark" style={{ width: "100%" }} placeholder="you@example.com" />
          </div>
          <div>
            <label style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required className="input-dark" style={{ width: "100%" }} placeholder="••••••" minLength={6} />
          </div>
          <button type="submit" disabled={loading} className="btn-accent" style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 6, padding: "10px 16px", marginTop: 8, opacity: loading ? 0.7 : 1 }}>
            <LogIn size={15} /> {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>

        <p style={{ textAlign: "center", fontSize: "0.82rem", color: "var(--text-secondary)", marginTop: 20 }}>
          Don&apos;t have an account?{" "}
          <Link href="/register" style={{ color: "var(--accent)", textDecoration: "none", fontWeight: 600 }}>Register</Link>
        </p>
      </div>
    </div>
  );
}
