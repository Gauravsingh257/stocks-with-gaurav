import { ImageResponse } from "next/og";

export const alt = "Stocks With Gaurav SMC research dashboard";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          background: "#07111f",
          color: "#e6f5ff",
          padding: 64,
          fontFamily: "Inter, Arial, sans-serif",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            width: "100%",
            border: "1px solid rgba(0, 212, 255, 0.35)",
            borderRadius: 36,
            padding: 54,
            background: "linear-gradient(135deg, rgba(0,212,255,0.14), rgba(0,224,150,0.08))",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 18, fontSize: 28, color: "#7dd3fc" }}>
            <div
              style={{
                width: 58,
                height: 58,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                borderRadius: 16,
                background: "#00d4ff",
                color: "#05101e",
                fontWeight: 900,
              }}
            >
              SG
            </div>
            Stocks With Gaurav
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div style={{ fontSize: 74, lineHeight: 1.02, fontWeight: 900, letterSpacing: -2 }}>
              Smart Money Concepts Research Dashboard
            </div>
            <div style={{ marginTop: 24, fontSize: 30, color: "#a7f3d0" }}>
              Educational NSE market structure, watchlists, journal analytics, and transparent research outcomes.
            </div>
          </div>
          <div style={{ fontSize: 22, color: "#94a3b8" }}>
            Educational only. Not SEBI-registered. No investment advice.
          </div>
        </div>
      </div>
    ),
    size,
  );
}
