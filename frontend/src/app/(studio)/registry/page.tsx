"use client";

import { useCallback, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Topbar from "@/components/shell/Topbar";
import { useToast } from "@/components/feedback/ToastProvider";
import Tabs from "@/components/ui/Tabs";
import Badge from "@/components/ui/Badge";
import Skeleton from "@/components/feedback/Skeleton";
import { registryService } from "@/lib/services/registryService";
import { useAsync } from "@/lib/hooks/useAsync";
import type { RegistryCategory, RegTab } from "@/lib/types";

interface BuilderSeed {
  category: string;
  type: string;
}

function stageBuilderSeed(seed: BuilderSeed) {
  try {
    sessionStorage.setItem("builder-seed-registry", JSON.stringify(seed));
  } catch {
    /* ignore — non-fatal if storage is unavailable */
  }
}

export default function RegistryPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { showToast } = useToast();

  const categoriesState = useAsync<RegistryCategory[]>(
    () => registryService.getCategories(),
    [],
  );
  const categories = categoriesState.data ?? [];

  const tabItems = useMemo(
    () => categories.map((c) => ({ key: c.key, label: c.label })),
    [categories],
  );
  const validTabs = useMemo(
    () => new Set(tabItems.map((t) => t.key)),
    [tabItems],
  );

  const defaultTab: RegTab = tabItems[0]?.key ?? "reg-markets";
  const tabParam = searchParams.get("tab") ?? defaultTab;
  const activeTab: RegTab = validTabs.has(tabParam as RegTab)
    ? (tabParam as RegTab)
    : defaultTab;

  const setActiveTab = useCallback(
    (tab: RegTab) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("tab", tab);
      router.replace(`/registry?${params.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  const category = categories.find((c) => c.key === activeTab);

  return (
    <>
      <Topbar title="Registry" />
      <div id="content" className="fade-in">
        {categoriesState.loading && (
          <div>
            <Skeleton height={28} width="60%" />
            <div style={{ marginTop: 16 }} className="grid-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="card">
                  <Skeleton height={18} width="70%" />
                  <div style={{ marginTop: 8 }}>
                    <Skeleton height={12} />
                  </div>
                  <div style={{ marginTop: 6 }}>
                    <Skeleton height={12} width="85%" />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {!categoriesState.loading && categoriesState.error != null && (
          <div>
            <p style={{ color: "var(--red)", fontSize: ".9rem" }}>
              Failed to load registry:{" "}
              {categoriesState.error instanceof Error
                ? categoriesState.error.message
                : "unknown error"}
            </p>
            <button
              className="btn btn-secondary btn-sm"
              onClick={categoriesState.refetch}
              style={{ marginTop: 8 }}
            >
              Retry
            </button>
          </div>
        )}

        {!categoriesState.loading &&
          categoriesState.error == null &&
          tabItems.length > 0 && (
            <>
              <Tabs
                items={tabItems}
                active={activeTab}
                onChange={setActiveTab}
              />

              {category && (
                <div
                  className={activeTab === "reg-exec" ? "grid-2" : "grid-3"}
                  data-testid="registry-grid"
                >
                  {category.entries.map((entry) => (
                    <div
                      key={entry.type}
                      className="card"
                      data-testid="registry-entry"
                      data-entry-name={entry.name}
                      data-entry-type={entry.type}
                      style={{
                        cursor: entry.disabled ? "default" : "pointer",
                        opacity: entry.disabled ? 0.5 : 1,
                      }}
                      onClick={() => {
                        if (entry.disabled) return;
                        showToast(
                          `Opening ${entry.name} documentation`,
                          "info",
                        );
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "flex-start",
                          marginBottom: 4,
                        }}
                      >
                        <h3 style={{ marginBottom: 4 }}>{entry.name}</h3>
                        {!entry.disabled && (
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              // US-016: seed uses the raw backend type
                              // so the builder's applyRegistrySeed
                              // matches without coercing through the
                              // human label.
                              stageBuilderSeed({
                                category: category.key,
                                type: entry.type,
                              });
                              showToast(
                                `Seeding builder with ${entry.name} config`,
                                "success",
                              );
                              router.push(
                                `/builder?seed=${encodeURIComponent(
                                  `${category.key}:${entry.type}`,
                                )}`,
                              );
                            }}
                          >
                            Start from this
                          </button>
                        )}
                      </div>
                      <p
                        style={{
                          fontSize: ".82rem",
                          color: "var(--text-2)",
                          marginBottom: entry.badges || entry.params ? 12 : 0,
                        }}
                      >
                        {entry.description}
                      </p>
                      {entry.params && (
                        <div
                          style={{
                            fontSize: ".78rem",
                            color: "var(--text-2)",
                            marginBottom: 8,
                          }}
                        >
                          <strong>Params:</strong> {entry.params}
                        </div>
                      )}
                      {entry.badges && (
                        <div
                          style={{
                            display: "flex",
                            gap: 4,
                            flexWrap: "wrap",
                          }}
                        >
                          {entry.badges.map((b) => (
                            <Badge key={b.label} variant={b.variant}>
                              {b.label}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

        {!categoriesState.loading &&
          categoriesState.error == null &&
          tabItems.length === 0 && (
            <p style={{ color: "var(--text-2)", fontSize: ".9rem" }}>
              No registry categories available from the backend.
            </p>
          )}
      </div>
    </>
  );
}
