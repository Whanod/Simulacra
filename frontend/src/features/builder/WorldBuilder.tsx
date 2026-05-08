"use client";

import { useState, useCallback, useRef, useEffect } from "react";

export interface MarketBlock {
  id: string;
  type: "cfamm" | "clob";
  label: string;
  x: number;
  y: number;
  tokens: string[];
}

export interface MarketLink {
  from: string;
  to: string;
  token: string;
}

interface WorldBuilderProps {
  onSpecChange: (markets: MarketBlock[], links: MarketLink[]) => void;
}

const BLOCK_W = 140;
const BLOCK_H = 70;

export default function WorldBuilder({ onSpecChange }: WorldBuilderProps) {
  const [blocks, setBlocks] = useState<MarketBlock[]>([
    { id: "m1", type: "cfamm", label: "CFAMM-ETH", x: 60, y: 40, tokens: ["ETH", "USDC"] },
    { id: "m2", type: "clob", label: "CLOB-BTC", x: 280, y: 40, tokens: ["BTC", "USDC"] },
  ]);
  const [links, setLinks] = useState<MarketLink[]>([
    { from: "m1", to: "m2", token: "USDC" },
  ]);
  const [dragging, setDragging] = useState<string | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const canvasRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    onSpecChange(blocks, links);
  }, [blocks, links, onSpecChange]);

  const addBlock = useCallback((type: "cfamm" | "clob") => {
    const id = `m${Date.now()}`;
    const label = type === "cfamm" ? `CFAMM-${id.slice(-3)}` : `CLOB-${id.slice(-3)}`;
    setBlocks((prev) => [
      ...prev,
      { id, type, label, x: 60 + Math.random() * 200, y: 40 + Math.random() * 100, tokens: ["TKN-A", "TKN-B"] },
    ]);
  }, []);

  const removeBlock = useCallback((id: string) => {
    setBlocks((prev) => prev.filter((b) => b.id !== id));
    setLinks((prev) => prev.filter((l) => l.from !== id && l.to !== id));
  }, []);

  const handleMouseDown = useCallback(
    (id: string, e: React.MouseEvent) => {
      const block = blocks.find((b) => b.id === id);
      if (!block || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      setDragging(id);
      setDragOffset({ x: e.clientX - rect.left - block.x, y: e.clientY - rect.top - block.y });
    },
    [blocks],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      const x = Math.max(0, Math.min(e.clientX - rect.left - dragOffset.x, rect.width - BLOCK_W));
      const y = Math.max(0, Math.min(e.clientY - rect.top - dragOffset.y, rect.height - BLOCK_H));
      setBlocks((prev) => prev.map((b) => (b.id === dragging ? { ...b, x, y } : b)));
    },
    [dragging, dragOffset],
  );

  const handleMouseUp = useCallback(() => setDragging(null), []);

  const addLink = useCallback(() => {
    if (blocks.length < 2) return;
    const from = blocks[0].id;
    const to = blocks[blocks.length - 1].id;
    if (!links.find((l) => l.from === from && l.to === to)) {
      setLinks((prev) => [...prev, { from, to, token: "TKN" }]);
    }
  }, [blocks, links]);

  return (
    <div className="card">
      <div className="card-header">
        <h3>World Builder</h3>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn btn-secondary btn-sm" onClick={() => addBlock("cfamm")}>
            + CFAMM
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => addBlock("clob")}>
            + CLOB
          </button>
          <button className="btn btn-secondary btn-sm" onClick={addLink}>
            Link
          </button>
        </div>
      </div>
      <div
        ref={canvasRef}
        style={{
          position: "relative",
          height: 220,
          background: "var(--bg-2)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          overflow: "hidden",
          cursor: dragging ? "grabbing" : "default",
        }}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {/* SVG for links */}
        <svg
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
        >
          {links.map((link, i) => {
            const fromBlock = blocks.find((b) => b.id === link.from);
            const toBlock = blocks.find((b) => b.id === link.to);
            if (!fromBlock || !toBlock) return null;
            return (
              <g key={i}>
                <line
                  x1={fromBlock.x + BLOCK_W / 2}
                  y1={fromBlock.y + BLOCK_H / 2}
                  x2={toBlock.x + BLOCK_W / 2}
                  y2={toBlock.y + BLOCK_H / 2}
                  stroke="var(--accent)"
                  strokeWidth="1.5"
                  strokeDasharray="6 3"
                  opacity="0.5"
                />
                <text
                  x={(fromBlock.x + toBlock.x + BLOCK_W) / 2}
                  y={(fromBlock.y + toBlock.y + BLOCK_H) / 2 - 6}
                  fill="var(--text-2)"
                  fontSize="10"
                  textAnchor="middle"
                >
                  {link.token}
                </text>
              </g>
            );
          })}
        </svg>
        {/* Blocks */}
        {blocks.map((block) => (
          <div
            key={block.id}
            style={{
              position: "absolute",
              left: block.x,
              top: block.y,
              width: BLOCK_W,
              height: BLOCK_H,
              background: block.type === "cfamm" ? "var(--accent-dim)" : "var(--purple-dim)",
              border: `1px solid ${block.type === "cfamm" ? "var(--accent)" : "var(--purple)"}`,
              borderRadius: "var(--radius)",
              padding: "8px 10px",
              cursor: dragging === block.id ? "grabbing" : "grab",
              userSelect: "none",
              zIndex: 1,
            }}
            onMouseDown={(e) => handleMouseDown(block.id, e)}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: ".75rem", fontWeight: 600, color: block.type === "cfamm" ? "var(--accent)" : "var(--purple)" }}>
                {block.type.toUpperCase()}
              </span>
              <span
                style={{ fontSize: ".7rem", color: "var(--text-2)", cursor: "pointer" }}
                onClick={(e) => {
                  e.stopPropagation();
                  removeBlock(block.id);
                }}
              >
                ×
              </span>
            </div>
            <div style={{ fontSize: ".72rem", color: "var(--text-1)", marginTop: 4 }}>{block.label}</div>
            <div style={{ fontSize: ".65rem", color: "var(--text-2)", marginTop: 2 }}>
              {block.tokens.join(" / ")}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
