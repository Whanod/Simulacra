"use client";

import Link from "next/link";
import { useAsync } from "@/lib/hooks/useAsync";
import { simulationService } from "@/lib/services/simulationService";
import { syntheticHelpView } from "./helpView";

const TRACKER_GITHUB_URL =
  "https://github.com/umbralabs/defi-sim/blob/main/solana-plans/synthetic-mode-tracker.md";

export default function SyntheticModeHelpPage() {
  const templatesState = useAsync(() => simulationService.getTemplates(), []);
  const view = syntheticHelpView(templatesState.data ?? []);

  return (
    <main className="help-page" data-help-page="synthetic-mode">
      <header className="help-page__header">
        <Link href="/dashboard" className="help-page__back">
          ← Back to studio
        </Link>
        <h1>Synthetic mode</h1>
        <p className="help-page__lede">
          Templates marked <strong>synthetic</strong> run on engine math that is
          not yet calibrated against the real Solana protocol they reference.
          The shape of the math may differ — not just the constants — so
          conclusions drawn from a synthetic template may not transfer to
          mainnet.
        </p>
      </header>

      <section className="help-page__section">
        <h2>Wrong constants vs. wrong shape</h2>
        <p>
          A synthetic template can mislead in two distinct ways. Wrong
          constants — fee rates, liquidity depth, signer counts — can be
          recalibrated and produce qualitatively similar results. Wrong shape —
          the wrong invariant entirely — produces results that diverge in
          direction and ranking, not just magnitude. The badge on each template
          names the math model the engine actually runs so you can judge which
          situation applies.
        </p>
      </section>

      <section className="help-page__section" data-section="math-models">
        <h2>Math models in use</h2>
        <ul className="help-page__model-list">
          {view.mathModelSections.map((m) => (
            <li
              key={m.id}
              data-math-model-id={m.id}
              className="help-page__model"
            >
              <h3>{m.displayName}</h3>
              <p>{m.invariantPlain}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="help-page__section" data-section="templates">
        <h2>What doesn&apos;t transfer to mainnet, by template</h2>
        {templatesState.loading ? (
          <p>Loading templates…</p>
        ) : templatesState.error ? (
          <p className="help-page__error">
            Could not load template metadata. The general guidance above still
            applies.
          </p>
        ) : view.templateSections.length === 0 ? (
          <p>No synthetic templates are currently registered.</p>
        ) : (
          <ul className="help-page__template-list">
            {view.templateSections.map((t) => (
              <li
                key={t.id}
                data-template-id={t.id}
                className="help-page__template"
              >
                <h3>
                  {t.name}{" "}
                  <span className="help-page__template-model">
                    ({t.mathModelDisplayName})
                  </span>
                </h3>
                <ul className="help-page__conclusions">
                  {t.conclusions.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="help-page__section">
        <h2>Why a template still has the badge</h2>
        <p>
          The badge is removed once the template is running real protocol
          math (real Whirlpool, Raydium, Meteora DLMM, etc.) against real
          captured pool state. The list of templates still waiting on a
          real protocol implementation is tracked in the project repo at{" "}
          <a href={TRACKER_GITHUB_URL} target="_blank" rel="noreferrer">
            synthetic-mode-tracker.md
          </a>
          .
        </p>
      </section>
    </main>
  );
}
