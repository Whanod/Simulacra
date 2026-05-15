"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ChainBadge from "@/components/ChainBadge";
import SlotTicker from "@/components/SlotTicker";
import SyntheticBadge, {
  type SyntheticBadgeInput,
} from "@/components/SyntheticBadge";
import UserChip from "@/components/shell/UserChip";
import type { RunSpec } from "@/lib/types/simulations";

type SpecLike =
  | Pick<RunSpec, "execution">
  | { execution?: { model?: string } | null }
  | null;

interface TopbarProps {
  title: string;
  spec?: SpecLike;
  template?: SyntheticBadgeInput | null;
}

const NEW_VERBS = [
  {
    key: "replay",
    href: "/replay",
    label: "Replay a slot",
    hint: "Counterfactual on a mainnet slot",
  },
  {
    key: "builder",
    href: "/builder",
    label: "Build a scenario",
    hint: "Synthetic markets and agents",
  },
];

export default function Topbar({ title, spec, template }: TopbarProps) {
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  return (
    <header id="topbar">
      <h2>{title}</h2>
      <div className="topbar-actions">
        {spec !== undefined ? <SlotTicker spec={spec} /> : null}
        {spec !== undefined ? <ChainBadge spec={spec} /> : null}
        {template ? <SyntheticBadge template={template} /> : null}
        <div className="new-verb-menu" ref={menuRef}>
          <button
            className="btn btn-primary btn-sm cta-primary"
            onClick={() => setMenuOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            + New <span className="caret" aria-hidden>▾</span>
          </button>
          {menuOpen ? (
            <div className="new-verb-dropdown" role="menu">
              {NEW_VERBS.map((verb) => (
                <button
                  key={verb.key}
                  className="new-verb-item"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    router.push(verb.href);
                  }}
                >
                  <strong>{verb.label}</strong>
                  <span>{verb.hint}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <UserChip />
      </div>
    </header>
  );
}
