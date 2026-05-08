"use client";

/**
 * Dynamic agent groups editor (US-012).
 *
 * Replaces the fixed six-role PopulationDesigner with a list of
 * agent groups whose types come from the backend registry contract
 * and whose params are rendered through the generic SchemaForm.
 *
 * Unknown agent types (entries in the registry that the frontend
 * has no hardcoded knowledge of) are first-class: adding them as a
 * group and editing their params flows through the same schema
 * renderer as the built-in types.
 */

import { useEffect, useMemo, useState } from "react";
import Card from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import { SchemaForm } from "@/components/schema/SchemaForm";
import { useRegistryContract } from "@/lib/hooks/useRegistryContract";
import type {
  RegistryContractResponse,
  RegistryEntityDefinition,
} from "@/lib/types/contract";
import type { DraftEntity } from "@/lib/types/drafts";
import type { AgentGroup } from "@/lib/types";

interface AgentGroupsDesignerProps {
  groups: AgentGroup[];
  onGroupsChange: (groups: AgentGroup[]) => void;
  totalAgents: number;
  onTotalChange: (n: number) => void;
  defaultCollateral: number;
  onCollateralChange: (n: number) => void;
}

function findAgentEntity(
  contract: RegistryContractResponse | null,
  type: string,
): RegistryEntityDefinition | undefined {
  if (!contract) return undefined;
  for (const cat of contract.categories) {
    if (cat.key !== "agents" && cat.key !== "reg-agents") continue;
    for (const entity of cat.entities) {
      if (entity.type === type) return entity;
    }
  }
  return undefined;
}

function agentEntities(
  contract: RegistryContractResponse | null,
): RegistryEntityDefinition[] {
  if (!contract) return [];
  const cat = contract.categories.find(
    (c) => c.key === "agents" || c.key === "reg-agents",
  );
  return cat?.entities ?? [];
}

function newGroupId(): string {
  return `g-${Math.random().toString(36).slice(2, 10)}`;
}

function groupToDraftEntity(
  group: AgentGroup,
  entity: RegistryEntityDefinition | undefined,
): DraftEntity {
  return {
    category: "agents",
    type: group.type,
    label: entity?.label ?? group.type,
    configPath: `agents.groups.${group.id}`,
    params: group.params,
    raw: group.params,
    supported: entity?.builderSupported ?? true,
    schema: entity?.schema,
    uiSchema: entity?.uiSchema,
  };
}

function makeGroupFromEntity(entity: RegistryEntityDefinition): AgentGroup {
  return {
    id: newGroupId(),
    type: entity.type,
    weight: 0,
    params: { ...(entity.defaults ?? {}) },
  };
}

interface BalancesEditorProps {
  balances: Record<string, number> | undefined;
  defaultCollateral: number;
  onChange: (next: Record<string, number>) => void;
}

function BalancesEditor({
  balances,
  defaultCollateral,
  onChange,
}: BalancesEditorProps) {
  const entries = Object.entries(balances ?? {});
  const updateEntry = (
    oldKey: string | null,
    newKey: string,
    value: number,
  ) => {
    const next: Record<string, number> = { ...(balances ?? {}) };
    if (oldKey !== null && oldKey !== newKey) delete next[oldKey];
    if (newKey) next[newKey] = value;
    onChange(next);
  };
  const removeEntry = (key: string) => {
    const next: Record<string, number> = { ...(balances ?? {}) };
    delete next[key];
    onChange(next);
  };
  const addToken = () => {
    const next: Record<string, number> = { ...(balances ?? {}) };
    let id = "TOKEN";
    let n = 1;
    while (id in next) {
      id = `TOKEN${n++}`;
    }
    next[id] = entries.length === 0 ? defaultCollateral : 0;
    onChange(next);
  };
  return (
    <div>
      <label
        style={{
          fontSize: ".78rem",
          color: "var(--text-2)",
          display: "block",
          marginBottom: 4,
        }}
      >
        Initial balances
      </label>
      {entries.length === 0 ? (
        <p className="hint" style={{ margin: 0, marginBottom: 6 }}>
          No per-agent balances set — the adapter falls back to{" "}
          <code>{defaultCollateral.toLocaleString()}</code> of the spec's
          collateral token.
        </p>
      ) : null}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {entries.map(([token, amount]) => (
          <div
            key={token}
            style={{ display: "flex", gap: 6, alignItems: "center" }}
          >
            <input
              type="text"
              value={token}
              aria-label="Token id"
              onChange={(e) => updateEntry(token, e.target.value, amount)}
              style={{ flex: "0 0 120px" }}
            />
            <input
              type="number"
              value={amount}
              aria-label={`${token} balance`}
              onChange={(e) =>
                updateEntry(token, token, parseFloat(e.target.value) || 0)
              }
              style={{ flex: 1 }}
            />
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              aria-label={`Remove ${token} balance`}
              onClick={() => removeEntry(token)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        onClick={addToken}
        style={{ marginTop: 6 }}
      >
        + token
      </button>
    </div>
  );
}

export default function AgentGroupsDesigner({
  groups,
  onGroupsChange,
  totalAgents,
  onTotalChange,
  defaultCollateral,
  onCollateralChange,
}: AgentGroupsDesignerProps) {
  const { contract, error: contractError } = useRegistryContract();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [addType, setAddType] = useState<string>("");
  // Per-group UI preference: which input to render. Defaults to the
  // weight slider for every group (including templates that hydrate
  // an explicit `count`) so users always see the slider first.
  // Toggling to "count" surfaces the number input.
  const [explicitMode, setExplicitMode] = useState<Record<string, boolean>>(
    {},
  );
  const isExplicitFor = (id: string) => Boolean(explicitMode[id]);

  const availableAgents = useMemo(() => agentEntities(contract), [contract]);

  useEffect(() => {
    if (addType === "" && availableAgents.length > 0) {
      setAddType(availableAgents[0].type);
    }
  }, [addType, availableAgents]);

  const weightSum = groups.reduce((s, g) => s + (g.weight || 0), 0);

  const updateGroup = (id: string, patch: Partial<AgentGroup>) => {
    onGroupsChange(
      groups.map((g) => (g.id === id ? { ...g, ...patch } : g)),
    );
  };

  const updateGroupParams = (id: string, params: Record<string, unknown>) => {
    onGroupsChange(
      groups.map((g) => (g.id === id ? { ...g, params } : g)),
    );
  };

  const removeGroup = (id: string) => {
    onGroupsChange(groups.filter((g) => g.id !== id));
    if (expandedId === id) setExpandedId(null);
  };

  // Toggle a group's input between weight slider and explicit-count
  // number input. The data model carries both fields so toggling is a
  // pure UI change — except when entering explicit mode without a
  // count, we derive one from the current weight share so the user
  // sees a sensible starting value.
  const toggleGroupMode = (id: string) => {
    setExplicitMode((cur) => {
      const next = !cur[id];
      // When turning explicit mode ON, ensure the group has a count.
      if (next) {
        const target = groups.find((g) => g.id === id);
        if (target && typeof target.count !== "number") {
          const derived =
            weightSum > 0
              ? Math.round((target.weight / weightSum) * totalAgents)
              : 1;
          updateGroup(id, { count: Math.max(1, derived) });
        }
      }
      return { ...cur, [id]: next };
    });
  };

  // Slider movements are an explicit "I want proportional weighting"
  // signal, so clear the group's count override too. specToApi's
  // useExplicitCounts branch keys off `typeof g.count === "number"`,
  // so dropping it lets the new weight actually drive the emitted
  // population.
  const handleWeightChange = (id: string, weight: number) => {
    onGroupsChange(
      groups.map((g) => {
        if (g.id !== id) return g;
        const next: AgentGroup = { ...g, weight };
        delete next.count;
        return next;
      }),
    );
  };

  const updateGroupBalances = (
    id: string,
    balances: Record<string, number>,
  ) => {
    const next = { ...balances };
    onGroupsChange(
      groups.map((g) => (g.id === id ? { ...g, initialBalances: next } : g)),
    );
  };

  const addGroup = () => {
    if (!addType) return;
    const entity = findAgentEntity(contract, addType);
    const next: AgentGroup = entity
      ? makeGroupFromEntity(entity)
      : { id: newGroupId(), type: addType, weight: 0, params: {} };
    // Give the first group a sensible default weight so users don't
    // start with a zero-sum mix.
    if (groups.length === 0) next.weight = 100;
    onGroupsChange([...groups, next]);
    setExpandedId(next.id);
  };

  return (
    <Card
      title="Agent Population"
      badge={<Badge variant="blue">{totalAgents} agents</Badge>}
    >
      <div className="form-row">
        <div className="form-group">
          <label>Total Agents</label>
          <input
            type="number"
            value={totalAgents}
            min={1}
            onChange={(e) => onTotalChange(parseInt(e.target.value) || 0)}
          />
        </div>
        <div className="form-group">
          <label>Default Collateral</label>
          <input
            type="number"
            value={defaultCollateral}
            onChange={(e) =>
              onCollateralChange(parseInt(e.target.value) || 0)
            }
          />
        </div>
      </div>

      {contractError ? (
        <p
          className="hint"
          style={{ color: "var(--red)", marginTop: 8 }}
          role="alert"
        >
          Failed to load agent types from registry: {contractError}
        </p>
      ) : null}

      <div
        style={{
          marginTop: 12,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {groups.length === 0 ? (
          <p className="hint" style={{ margin: 0 }}>
            No agent groups yet. Add one below to start building the
            population.
          </p>
        ) : null}
        {groups.map((group) => {
          const entity = findAgentEntity(contract, group.type);
          const isExpanded = expandedId === group.id;
          const isExplicit = isExplicitFor(group.id);
          const derivedCount =
            weightSum > 0
              ? Math.round((group.weight / weightSum) * totalAgents)
              : 0;
          // The "×N" indicator follows the active input mode: in
          // explicit mode it reflects the user-typed count directly;
          // in slider mode it reflects what specToApi will allocate
          // from the weight share. When slider mode hasn't cleared a
          // template-hydrated count yet, prefer the count so the
          // displayed population matches what the backend gets.
          const populationCount = isExplicit
            ? Math.max(0, group.count ?? 0)
            : typeof group.count === "number"
              ? group.count
              : derivedCount;
          const draft = groupToDraftEntity(group, entity);
          const unsupported = entity ? !entity.builderSupported : false;
          return (
            <div
              key={group.id}
              data-testid="agent-group-card"
              data-group-type={group.type}
              style={{
                background: "var(--bg-2)",
                border: "1px solid var(--border)",
                borderColor: isExpanded
                  ? "var(--border-active)"
                  : "var(--border)",
                borderRadius: "var(--radius)",
                padding: "10px 14px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  cursor: "pointer",
                }}
                onClick={() => setExpandedId(isExpanded ? null : group.id)}
              >
                <span
                  style={{
                    fontSize: ".82rem",
                    fontWeight: 500,
                    minWidth: 140,
                  }}
                >
                  {entity?.label ?? group.type}
                </span>
                {unsupported ? (
                  <Badge variant="yellow">unsupported</Badge>
                ) : null}
                {!entity && contract ? (
                  <Badge variant="yellow">unknown</Badge>
                ) : null}
                {isExplicit ? (
                  <input
                    type="number"
                    min={0}
                    value={group.count ?? 0}
                    aria-label={`${group.type} count`}
                    onChange={(e) =>
                      updateGroup(group.id, {
                        count: Math.max(0, parseInt(e.target.value) || 0),
                      })
                    }
                    onClick={(e) => e.stopPropagation()}
                    style={{ flex: 1, maxWidth: 120 }}
                  />
                ) : (
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={group.weight}
                    aria-label={`${group.type} weight`}
                    onChange={(e) =>
                      handleWeightChange(
                        group.id,
                        parseInt(e.target.value) || 0,
                      )
                    }
                    onClick={(e) => e.stopPropagation()}
                    style={{ flex: 1, accentColor: "var(--accent)" }}
                  />
                )}
                {!isExplicit ? (
                  <span
                    className="mono"
                    style={{
                      minWidth: 36,
                      textAlign: "right",
                      fontSize: ".82rem",
                    }}
                  >
                    {group.weight}%
                  </span>
                ) : null}
                <span
                  style={{
                    fontSize: ".72rem",
                    color: "var(--text-2)",
                    minWidth: 40,
                  }}
                >
                  ×{populationCount}
                </span>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  aria-label={`Toggle ${group.type} count mode`}
                  title={
                    isExplicit
                      ? "Switch to weighted mode (proportional split of total agents)"
                      : "Switch to explicit count (verbatim population)"
                  }
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleGroupMode(group.id);
                  }}
                >
                  {isExplicit ? "weight" : "count"}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  aria-label={`Remove ${group.type} group`}
                  onClick={(e) => {
                    e.stopPropagation();
                    removeGroup(group.id);
                  }}
                >
                  Remove
                </button>
              </div>

              {isExpanded ? (
                <div
                  style={{
                    marginTop: 10,
                    paddingTop: 10,
                    borderTop: "1px solid var(--border)",
                  }}
                  onClick={(e) => e.stopPropagation()}
                >
                  {entity?.description ? (
                    <p
                      className="hint"
                      style={{ marginTop: 0, marginBottom: 10 }}
                    >
                      {entity.description}
                    </p>
                  ) : null}
                  <BalancesEditor
                    balances={group.initialBalances}
                    defaultCollateral={defaultCollateral}
                    onChange={(b) => updateGroupBalances(group.id, b)}
                  />
                  <details style={{ marginTop: 10 }}>
                    <summary
                      style={{
                        fontSize: ".78rem",
                        color: "var(--text-2)",
                        cursor: "pointer",
                      }}
                    >
                      Advanced: agent_id prefix
                    </summary>
                    <div className="form-group" style={{ marginTop: 6 }}>
                      <input
                        type="text"
                        value={group.agentIdPrefix ?? ""}
                        placeholder={group.type}
                        onChange={(e) =>
                          updateGroup(group.id, {
                            agentIdPrefix: e.target.value || undefined,
                          })
                        }
                      />
                      <p className="hint" style={{ marginTop: 4 }}>
                        Backend agent_id stem. Defaults to the agent type
                        when blank. With count &gt; 1 the adapter appends
                        <code> -1</code>, <code>-2</code>, …
                      </p>
                    </div>
                  </details>
                  <div style={{ marginTop: 10 }}>
                    <SchemaForm
                      entity={draft}
                      standalone={false}
                      onChange={(params) => updateGroupParams(group.id, params)}
                    />
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {/* Mix bar */}
      {groups.length > 0 ? (
        <div className="mix-bar" style={{ marginTop: 12 }}>
          {groups.map((g) => (
            <div
              key={g.id}
              style={{
                width: weightSum > 0 ? `${(g.weight / weightSum) * 100}%` : 0,
                background: "var(--accent)",
                opacity: 0.65,
              }}
            />
          ))}
        </div>
      ) : null}
      <p className="hint" style={{ marginTop: 6 }}>
        Group weights sum to{" "}
        <span
          style={{
            color:
              weightSum === 100 || weightSum === 0
                ? "var(--text-2)"
                : "var(--yellow)",
          }}
        >
          {weightSum}%
        </span>
        . Weights are normalized against the total when the spec is built.
      </p>

      {/* Add group row */}
      <div
        style={{
          marginTop: 12,
          display: "flex",
          alignItems: "flex-end",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <div className="form-group" style={{ flex: "1 1 200px" }}>
          <label htmlFor="agent-group-add-type">Agent Type</label>
          <select
            id="agent-group-add-type"
            value={addType}
            onChange={(e) => setAddType(e.target.value)}
            disabled={availableAgents.length === 0}
          >
            {availableAgents.length === 0 ? (
              <option value="">Loading agent types…</option>
            ) : (
              availableAgents.map((entity) => (
                <option key={entity.type} value={entity.type}>
                  {entity.label}
                  {entity.builderSupported ? "" : " (unsupported)"}
                </option>
              ))
            )}
          </select>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={addGroup}
          disabled={!addType}
        >
          Add group
        </button>
      </div>
    </Card>
  );
}
