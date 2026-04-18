"use client";

import { motion, AnimatePresence } from "framer-motion";
import type { ReactNode } from "react";

// ── Fade-in on mount ──────────────────────────────────────────────
export function FadeIn({
  children,
  delay = 0,
  duration = 0.4,
  className,
  style,
}: {
  children: ReactNode;
  delay?: number;
  duration?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration, delay, ease: "easeOut" }}
      className={className}
      style={style}
    >
      {children}
    </motion.div>
  );
}

// ── Staggered children ───────────────────────────────────────────
export function StaggerContainer({
  children,
  stagger = 0.06,
  className,
  style,
}: {
  children: ReactNode;
  stagger?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <motion.div
      initial="hidden"
      animate="visible"
      variants={{
        hidden: {},
        visible: { transition: { staggerChildren: stagger } },
      }}
      className={className}
      style={style}
    >
      {children}
    </motion.div>
  );
}

export function StaggerItem({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <motion.div
      variants={{
        hidden: { opacity: 0, y: 16 },
        visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: "easeOut" } },
      }}
      className={className}
      style={style}
    >
      {children}
    </motion.div>
  );
}

// ── Scale-in card (hover lift) ───────────────────────────────────
export function GlassCard({
  children,
  delay = 0,
  className,
  style,
  hover = true,
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
  style?: React.CSSProperties;
  hover?: boolean;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97, y: 10 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: 0.4, delay, ease: "easeOut" }}
      whileHover={hover ? { y: -2, boxShadow: "0 8px 30px rgba(0,0,0,0.3)" } : undefined}
      className={className}
      style={{ transition: "box-shadow 0.2s", ...style }}
    >
      {children}
    </motion.div>
  );
}

// ── Modal overlay + content ──────────────────────────────────────
export function ModalOverlay({
  children,
  onClose,
}: {
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }}
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        background: "rgba(0,0,0,0.65)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
      }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.92, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 10 }}
        transition={{ duration: 0.25, ease: "easeOut" }}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </motion.div>
    </motion.div>
  );
}

// ── AnimatePresence export for convenience ───────────────────────
export { AnimatePresence, motion };

// ── Number counter animation ─────────────────────────────────────
export function CountUp({
  value,
  prefix = "",
  suffix = "",
  decimals = 1,
  style,
}: {
  value: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  style?: React.CSSProperties;
}) {
  return (
    <motion.span
      key={value}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      style={style}
    >
      {prefix}{value.toFixed(decimals)}{suffix}
    </motion.span>
  );
}

// ── Slide-in from side ───────────────────────────────────────────
export function SlideIn({
  children,
  direction = "left",
  delay = 0,
  className,
  style,
}: {
  children: ReactNode;
  direction?: "left" | "right" | "up" | "down";
  delay?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  const offsets = {
    left: { x: -30, y: 0 },
    right: { x: 30, y: 0 },
    up: { x: 0, y: -20 },
    down: { x: 0, y: 20 },
  };
  const { x, y } = offsets[direction];
  return (
    <motion.div
      initial={{ opacity: 0, x, y }}
      animate={{ opacity: 1, x: 0, y: 0 }}
      transition={{ duration: 0.4, delay, ease: "easeOut" }}
      className={className}
      style={style}
    >
      {children}
    </motion.div>
  );
}
