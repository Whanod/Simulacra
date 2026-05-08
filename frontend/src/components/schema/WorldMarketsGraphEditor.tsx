"use client";

/**
 * `world-markets-graph` special editor plugin (US-011).
 *
 * Ports the existing `src/features/builder/WorldBuilder.tsx` graph
 * editor into the schema-driven plugin layer. The plugin owns no
 * application state of its own — it reads `entity.params.markets`
 * and `entity.params.links`, surfaces a draggable canvas for
 * visually editing the topology, and writes every change back
 * through `onChange({ ...params, markets, links })`.
 *
 * What *is* persisted in the draft params:
 *   - `markets: WorldMarketBlock[]`  (id, type, label, tokens)
 *   - `links:   WorldMarketLink[]`   (from, to, token)
 *
 * What is *not* persisted:
 *   - `(x, y)` block positions. These are view-only; the plugin
 *     seeds a deterministic layout from the block index on mount
 *     so the graph looks the same on every open, and keeps the
 *     current drag position in component state only.
 *
 * Fallback: the plugin never crashes on malformed params. Unknown
 * fields in `entity.params` pass through untouched on `onChange`
 * via the spread, so partial/extended shapes from future backend
 * versions survive round-tripping through this editor.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { SpecialEditorProps } from "./specialEditors";
import {
  makeBlockId,
  readBlocks,
  readLinks,
  seedLayout,
  type WorldMarketBlock,
  type WorldMarketLink,
} from "./worldMarkets";

interface Position {
  x: number;
  y: number;
}

const BLOCK_W = 140;
const BLOCK_H = 76;

export function WorldMarketsGraphEditor({ entity, onChange }: SpecialEditorProps) {
  const blocks = useMemo(() => readBlocks(entity.params), [entity.params]);
  const links = useMemo(() => readLinks(entity.params), [entity.params]);

  // View-only drag positions. Re-seed when the set of blocks in the
  // params changes (id-set hashing keeps the effect from thrashing
  // on every keystroke in an adjacent form).
  const blockIdsKey = blocks.map((b) => b.id).join("|");
  const [positions, setPositions] = useState<Record<string, Position>>(() =>
    seedLayout(blocks),
  );
  useEffect(() => {
    setPositions((prev) => {
      const next: Record<string, Position> = {};
      const seeded = seedLayout(blocks);
      for (const block of blocks) {
        next[block.id] = prev[block.id] ?? seeded[block.id];
      }
      return next;
    });
    // We key off the stable id list, not the blocks array identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blockIdsKey]);

  const [dragging, setDragging] = useState<string | null>(null);
  const [dragOffset, setDragOffset] = useState<Position>({ x: 0, y: 0 });
  const canvasRef = useRef<HTMLDivElement>(null);

  const writeParams = useCallback(
    (nextBlocks: WorldMarketBlock[], nextLinks: WorldMarketLink[]) => {
      onChange({
        ...entity.params,
        markets: nextBlocks,
        links: nextLinks,
      });
    },
    [entity.params, onChange],
  );

  const addBlock = useCallback(
    (type: string) => {
      const id = makeBlockId(blocks);
      const label = `${type.toUpperCase()}-${id.slice(-3)}`;
      const next: WorldMarketBlock = {
        id,
        type,
        label,
        tokens: ["TKN-A", "TKN-B"],
      };
      writeParams([...blocks, next], links);
    },
    [blocks, links, writeParams],
  );

  const removeBlock = useCallback(
    (id: string) => {
      writeParams(
        blocks.filter((b) => b.id !== id),
        links.filter((l) => l.from !== id && l.to !== id),
      );
    },
    [blocks, links, writeParams],
  );

  const addLink = useCallback(() => {
    if (blocks.length < 2) return;
    const from = blocks[0].id;
    const to = blocks[blocks.length - 1].id;
    const exists = links.some((l) => l.from === from && l.to === to);
    if (exists) return;
    writeParams(blocks, [...links, { from, to, token: "TKN" }]);
  }, [blocks, links, writeParams]);

  const updateBlockLabel = useCallback(
    (id: string, label: string) => {
      writeParams(
        blocks.map((b) => (b.id === id ? { ...b, label } : b)),
        links,
      );
    },
    [blocks, links, writeParams],
  );

  const handleMouseDown = useCallback(
    (id: string, e: React.MouseEvent) => {
      const pos = positions[id];
      if (!pos || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      setDragging(id);
      setDragOffset({
        x: e.clientX - rect.left - pos.x,
        y: e.clientY - rect.top - pos.y,
      });
    },
    [positions],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      const x = Math.max(
        0,
        Math.min(e.clientX - rect.left - dragOffset.x, rect.width - BLOCK_W),
      );
      const y = Math.max(
        0,
        Math.min(e.clientY - rect.top - dragOffset.y, rect.height - BLOCK_H),
      );
      setPositions((prev) => ({ ...prev, [dragging]: { x, y } }));
    },
    [dragging, dragOffset],
  );

  const handleMouseUp = useCallback(() => setDragging(null), []);

  return (
    <div
      className="special-editor world-markets-graph"
      data-special-editor="world-markets-graph"
    >
      <div
        style={{
          display: "flex",
          gap: 6,
          marginBottom: 10,
          justifyContent: "flex-end",
        }}
      >
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          data-action="add-cfamm"
          onClick={() => addBlock("cfamm")}
        >
          + CFAMM
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          data-action="add-clob"
          onClick={() => addBlock("clob")}
        >
          + CLOB
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          data-action="add-link"
          onClick={addLink}
          disabled={blocks.length < 2}
        >
          Link
        </button>
      </div>

      <div
        ref={canvasRef}
        data-testid="world-markets-canvas"
        style={{
          position: "relative",
          height: 240,
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
        {blocks.length === 0 ? (
          <div
            className="hint"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
              padding: 24,
            }}
          >
            No markets yet. Add a CFAMM or CLOB to start.
          </div>
        ) : null}

        <svg
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            pointerEvents: "none",
          }}
        >
          {links.map((link, i) => {
            const fromPos = positions[link.from];
            const toPos = positions[link.to];
            if (!fromPos || !toPos) return null;
            return (
              <g key={`${link.from}:${link.to}:${i}`}>
                <line
                  x1={fromPos.x + BLOCK_W / 2}
                  y1={fromPos.y + BLOCK_H / 2}
                  x2={toPos.x + BLOCK_W / 2}
                  y2={toPos.y + BLOCK_H / 2}
                  stroke="var(--accent)"
                  strokeWidth={1.5}
                  strokeDasharray="6 3"
                  opacity={0.6}
                />
                <text
                  x={(fromPos.x + toPos.x + BLOCK_W) / 2}
                  y={(fromPos.y + toPos.y + BLOCK_H) / 2 - 6}
                  fill="var(--text-2)"
                  fontSize={10}
                  textAnchor="middle"
                >
                  {link.token}
                </text>
              </g>
            );
          })}
        </svg>

        {blocks.map((block) => {
          const pos = positions[block.id] ?? { x: 40, y: 40 };
          const accent =
            block.type === "clob" ? "var(--purple)" : "var(--accent)";
          const accentDim =
            block.type === "clob" ? "var(--purple-dim)" : "var(--accent-dim)";
          return (
            <div
              key={block.id}
              data-market-id={block.id}
              data-market-type={block.type}
              style={{
                position: "absolute",
                left: pos.x,
                top: pos.y,
                width: BLOCK_W,
                height: BLOCK_H,
                background: accentDim,
                border: `1px solid ${accent}`,
                borderRadius: "var(--radius)",
                padding: "8px 10px",
                cursor: dragging === block.id ? "grabbing" : "grab",
                userSelect: "none",
                zIndex: 1,
              }}
              onMouseDown={(e) => handleMouseDown(block.id, e)}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <span
                  style={{
                    fontSize: ".7rem",
                    fontWeight: 600,
                    color: accent,
                    textTransform: "uppercase",
                  }}
                >
                  {block.type}
                </span>
                <button
                  type="button"
                  aria-label={`Remove ${block.label}`}
                  style={{
                    background: "transparent",
                    color: "var(--text-2)",
                    fontSize: ".8rem",
                    padding: 0,
                    lineHeight: 1,
                  }}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    removeBlock(block.id);
                  }}
                >
                  {"\u00d7"}
                </button>
              </div>
              <input
                type="text"
                value={block.label}
                aria-label={`Label for ${block.id}`}
                onMouseDown={(e) => e.stopPropagation()}
                onChange={(e) => updateBlockLabel(block.id, e.target.value)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--text-1)",
                  padding: 0,
                  marginTop: 4,
                  fontSize: ".72rem",
                  width: "100%",
                }}
              />
              <div
                style={{
                  fontSize: ".65rem",
                  color: "var(--text-2)",
                  marginTop: 2,
                }}
              >
                {block.tokens.join(" / ")}
              </div>
            </div>
          );
        })}
      </div>

      <p className="hint" style={{ marginTop: 8 }}>
        {blocks.length} market{blocks.length === 1 ? "" : "s"}, {links.length}{" "}
        link{links.length === 1 ? "" : "s"} — positions are view-only, topology
        is persisted.
      </p>
    </div>
  );
}
