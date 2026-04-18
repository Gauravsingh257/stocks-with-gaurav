"use client";

import { motion, AnimatePresence } from "framer-motion";
import type { ReactNode } from "react";

// ── Fade-in on mount ──────────────────────────────────────────────
export function FadeIn({
  children,
  delay = 0,
  duration = 0.5,
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
      initial={{ opacity: 0, y: 30 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration, delay, ease: [0.25, 0.46, 0.45, 0.94] }}
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
  stagger = 0.1,
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
        visible: { transition: { staggerChildren: stagger, delayChildren: 0.1 } },
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
        hidden: { opacity: 0, y: 40, scale: 0.97 },
        visible: {
          opacity: 1,
          y: 0,
          scale: 1,
          transition: { duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] },
        },
      }}
      className={className}
      style={style}
    >
      {children}
    </motion.div>
  );
}

// ── Scale-in card with hover lift ────────────────────────────────
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
      initial={{ opacity: 0, scale: 0.93, y: 20 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: 0.5, delay, ease: [0.25, 0.46, 0.45, 0.94] }}
      whileHover={
        hover
          ? {
              y: -4,
              scale: 1.01,
              boxShadow: "0 12px 40px rgba(0,212,255,0.12), 0 4px 16px rgba(0,0,0,0.3)",
              borderColor: "rgba(0,212,255,0.2)",
              transition: { duration: 0.25, ease: "easeOut" },
            }
          : undefined
      }
      className={className}
      style={style}
    >
      {children}
    </motion.div>
  );
}

// ── Animated glass card for grids (stagger-aware + hover) ────────
export function AnimatedCard({
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
        hidden: { opacity: 0, y: 30, scale: 0.95 },
        visible: {
          opacity: 1,
          y: 0,
          scale: 1,
          transition: { duration: 0.45, ease: [0.25, 0.46, 0.45, 0.94] },
        },
      }}
      whileHover={{
        y: -4,
        scale: 1.015,
        boxShadow: "0 12px 40px rgba(0,212,255,0.12), 0 4px 16px rgba(0,0,0,0.3)",
        transition: { duration: 0.25, ease: "easeOut" },
      }}
      className={className}
      style={style}
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
        backdropFilter: "blur(6px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
      }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.88, y: 30 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.92, y: 20 }}
        transition={{ duration: 0.3, ease: [0.25, 0.46, 0.45, 0.94] }}
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
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
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
    left: { x: -50, y: 0 },
    right: { x: 50, y: 0 },
    up: { x: 0, y: -30 },
    down: { x: 0, y: 30 },
  };
  const { x, y } = offsets[direction];
  return (
    <motion.div
      initial={{ opacity: 0, x, y }}
      animate={{ opacity: 1, x: 0, y: 0 }}
      transition={{ duration: 0.5, delay, ease: [0.25, 0.46, 0.45, 0.94] }}
      className={className}
      style={style}
    >
      {children}
    </motion.div>
  );
}

// ── Hover glow button ────────────────────────────────────────────
export function AnimatedButton({
  children,
  onClick,
  className,
  style,
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
  style?: React.CSSProperties;
  disabled?: boolean;
}) {
  return (
    <motion.button
      whileHover={!disabled ? { scale: 1.04, boxShadow: "0 0 20px rgba(0,212,255,0.25)" } : undefined}
      whileTap={!disabled ? { scale: 0.97 } : undefined}
      transition={{ duration: 0.15 }}
      onClick={onClick}
      className={className}
      style={style}
      disabled={disabled}
    >
      {children}
    </motion.button>
  );
}
