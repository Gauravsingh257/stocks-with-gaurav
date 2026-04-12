"use client";

import React from "react";

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          minHeight: "60vh", gap: 16, padding: 40, textAlign: "center",
        }}>
          <div style={{ fontSize: "2rem" }}>⚠️</div>
          <h2 style={{ margin: 0, color: "var(--text-primary)", fontSize: "1.2rem" }}>Something went wrong</h2>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", maxWidth: 420 }}>
            {this.state.error?.message ?? "An unexpected error occurred."}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{
              padding: "8px 20px", borderRadius: 8, border: "1px solid var(--accent)",
              background: "transparent", color: "var(--accent)", cursor: "pointer", fontSize: "0.85rem",
            }}
          >
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
