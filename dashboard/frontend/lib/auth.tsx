"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { getBackendBase } from "./api";
import { track, identify, endSession } from "./analytics";

export interface AuthUser {
  id: number;
  email: string;
  name: string;
  role: "FREE" | "PREMIUM" | "ADMIN";
}

interface AuthContextType {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  token: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  logout: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

const BASE = getBackendBase();

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const saved = localStorage.getItem("swg-auth-token");
    if (saved) {
      setToken(saved);
      fetch(`${BASE}/api/auth/me`, { cache: "no-store", headers: { Authorization: `Bearer ${saved}` } })
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((data) => setUser(data))
        .catch(() => {
          localStorage.removeItem("swg-auth-token");
          setToken(null);
          endSession("token_expired");
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Login failed");
    }
    const data = await res.json();
    localStorage.setItem("swg-auth-token", data.token);
    setToken(data.token);
    setUser(data.user);
    if (data.user?.id != null) identify(data.user.id, { role: data.user.role });
    track("login", { role: data.user?.role });
  }, []);

  const register = useCallback(async (email: string, password: string, name: string) => {
    const res = await fetch(`${BASE}/api/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, name }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Registration failed");
    }
    const data = await res.json();
    localStorage.setItem("swg-auth-token", data.token);
    setToken(data.token);
    setUser(data.user);
    if (data.user?.id != null) identify(data.user.id, { role: data.user.role });
    track("signup", { role: data.user?.role });
  }, []);

  const logout = useCallback(() => {
    endSession("logout");
    localStorage.removeItem("swg-auth-token");
    setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
