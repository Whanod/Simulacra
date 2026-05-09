"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface InfoTooltipProps {
  text: string;
  ariaLabel?: string;
}

const MAX_WIDTH = 260;
const VIEWPORT_MARGIN = 8;

export default function InfoTooltip({ text, ariaLabel }: InfoTooltipProps) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || !btnRef.current) return;
    const rect = btnRef.current.getBoundingClientRect();
    const halfW = MAX_WIDTH / 2;
    const minX = halfW + VIEWPORT_MARGIN;
    const maxX = window.innerWidth - halfW - VIEWPORT_MARGIN;
    const cx = Math.max(minX, Math.min(rect.left + rect.width / 2, maxX));
    setPos({ top: rect.top, left: cx });
  }, [open]);

  return (
    <span className="info-tip">
      <button
        ref={btnRef}
        type="button"
        className="info-tip__btn"
        aria-label={ariaLabel ?? "More information"}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        ?
      </button>
      {open && pos != null && typeof document !== "undefined"
        ? createPortal(
            <span
              className="info-tip__bubble"
              role="tooltip"
              style={{ top: pos.top, left: pos.left }}
            >
              {text}
            </span>,
            document.body,
          )
        : null}
    </span>
  );
}
