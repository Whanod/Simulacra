"use client";

import { Fragment } from "react";
import { usePathname, useRouter } from "next/navigation";
import WalletArtifactsPanel from "@/components/wallet/WalletArtifactsPanel";
import WalletPositionsPanel from "@/components/wallet/WalletPositionsPanel";

type NavItem = {
  key: string;
  href: string;
  label: string;
  icon: React.ReactNode;
};

type NavGroup = {
  key: string;
  label?: string;
  variant?: "primary" | "muted";
  items: NavItem[];
};

const ICON_DASHBOARD = (
  <>
    <rect x="1" y="1" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
    <rect x="10" y="1" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
    <rect x="1" y="10" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
    <rect x="10" y="10" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
  </>
);

const ICON_REPLAY = (
  <>
    <path
      d="M4 9A5 5 0 1 1 6.2 13"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
    <path
      d="M3 13H7V9"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </>
);

const ICON_BUILD = (
  <>
    <path
      d="M2 6L9 2L16 6L16 13L9 17L2 13L2 6Z"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
      fill="none"
    />
    <path
      d="M2 6L9 10L16 6M9 10V17"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
      fill="none"
    />
  </>
);

const ICON_COMPARE = (
  <>
    <path d="M2 9h14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    <path
      d="M5 5l-3 4 3 4"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <path
      d="M13 5l3 4-3 4"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </>
);

const ICON_SWEEPS = (
  <>
    <path
      d="M1 14L6 9L10 13L17 4"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <path
      d="M12 4H17V9"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </>
);

const ICON_REGISTRY = (
  <>
    <rect x="2" y="2" width="14" height="14" rx="2" stroke="currentColor" strokeWidth="1.5" />
    <path d="M6 6h6M6 9h4M6 12h5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
  </>
);

const ICON_CALIBRATION = (
  <>
    <circle cx="9" cy="9" r="7" stroke="currentColor" strokeWidth="1.5" fill="none" />
    <path
      d="M9 9 L13 6"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
    <circle cx="9" cy="9" r="1.2" fill="currentColor" />
  </>
);

const NAV_GROUPS: NavGroup[] = [
  {
    key: "home",
    items: [
      { key: "dashboard", href: "/dashboard", label: "Dashboard", icon: ICON_DASHBOARD },
    ],
  },
  {
    key: "workspace",
    label: "Workspace",
    variant: "primary",
    items: [
      { key: "replay", href: "/replay", label: "Replay a slot", icon: ICON_REPLAY },
      { key: "builder", href: "/builder", label: "Build a scenario", icon: ICON_BUILD },
    ],
  },
  {
    key: "analyze",
    label: "Analyze",
    items: [
      { key: "compare", href: "/compare", label: "Compare", icon: ICON_COMPARE },
      { key: "sweeps", href: "/sweeps", label: "Sweeps", icon: ICON_SWEEPS },
    ],
  },
  {
    key: "reference",
    label: "Reference",
    variant: "muted",
    items: [
      { key: "registry", href: "/registry", label: "Registry", icon: ICON_REGISTRY },
      { key: "calibration", href: "/calibration", label: "Calibration", icon: ICON_CALIBRATION },
    ],
  },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  const allItems = NAV_GROUPS.flatMap((g) => g.items);
  const activeKey = allItems.find((item) => pathname.startsWith(item.href))?.key;

  return (
    <aside id="sidebar">
      <div className="logo">
        <svg viewBox="0 0 28 28" fill="none">
          <rect x="2" y="2" width="24" height="24" rx="6" stroke="var(--accent)" strokeWidth="2" />
          <path
            d="M8 18L12 10L16 15L20 8"
            stroke="var(--green)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <circle cx="12" cy="10" r="2" fill="var(--accent)" />
          <circle cx="16" cy="15" r="2" fill="var(--accent)" />
        </svg>
        <div>
          <h1>Simulacra</h1>
        </div>
      </div>
      <nav>
        {NAV_GROUPS.map((group) => (
          <Fragment key={group.key}>
            {group.label ? (
              <div className="nav-group-label">{group.label}</div>
            ) : null}
            {group.items.map(({ key, href, label, icon }) => {
              const classes = ["nav-item"];
              if (activeKey === key) classes.push("active");
              if (group.variant === "primary") classes.push("nav-item--primary");
              if (group.variant === "muted") classes.push("nav-item--muted");
              return (
                <button
                  key={key}
                  className={classes.join(" ")}
                  onClick={() => router.push(href)}
                >
                  <svg viewBox="0 0 18 18" fill="none">
                    {icon}
                  </svg>
                  {label}
                </button>
              );
            })}
          </Fragment>
        ))}
      </nav>
      <WalletPositionsPanel />
      <WalletArtifactsPanel />
      <div className="sidebar-footer">
        <div className="status">
          <span className="dot" /> Engine ready
        </div>
        <div className="status" style={{ color: "var(--text-2)" }}>
          v0.1.0
        </div>
      </div>
    </aside>
  );
}
