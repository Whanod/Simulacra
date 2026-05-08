import Link from "next/link";

export default function LighthouseScenarioHelpPage() {
  return (
    <main className="help-page" data-help-page="lighthouse-scenario">
      <header className="help-page__header">
        <Link href="/dashboard" className="help-page__back">
          ← Back to studio
        </Link>
        <h1>Lighthouse scenario</h1>
        <p className="help-page__lede">
          A sandwich attack run against the canonical SOL/USDC Orca Whirlpool
          on Solana. The pool, its tick arrays, and its token vaults come
          from a captured mainnet snapshot — the simulation forks that
          snapshot, then runs noise traders, victims, a sandwich bot, and a
          Jito searcher on top of the real liquidity.
        </p>
      </header>

      <section className="help-page__section" data-section="modeled">
        <h2>What this run reflects</h2>
        <ul>
          <li>
            <strong>The real Whirlpool swap engine.</strong> Sqrt-price
            updates, tick crossings, fee tiers, and protocol-fee splits all
            execute exactly as they do on Solana.
          </li>
          <li>
            <strong>Real pool depth.</strong> The starting liquidity, tick
            distribution, and vault balances are bytes captured from
            mainnet — not made-up numbers.
          </li>
          <li>
            <strong>Solana-style execution.</strong> Priority ordering,
            compute-unit budgets per transaction / per slot / per writable
            account, slot clock, leader schedule, address-lookup-table
            compression — everything a sandwich bundle would touch on
            mainnet.
          </li>
          <li>
            <strong>The Jito bundle auction.</strong> Tip position matters,
            instruction-location tips are lost on partial-failure revert,
            and the standard 8 Jito tip accounts are wired in. The sandwich
            searcher front-runs and back-runs victim swaps it sees in the
            same slot.
          </li>
          <li>
            <strong>A live priority-fee market.</strong> Per-account rolling
            CU-price distribution, pre-warmed for 200 slots so the searcher
            doesn&apos;t quote a degenerate floor on its first attempt.
          </li>
        </ul>
      </section>

      <section className="help-page__section" data-section="not-modeled">
        <h2>What it doesn&apos;t reflect yet</h2>
        <ul>
          <li>
            <strong>The landing-rate numbers are illustrative.</strong> How
            often any given bundle actually lands — and how much tip you
            need to land it — has not been measured against real Jito
            auction outcomes. The mechanics are right; the absolute
            probabilities are not yet a forecast of mainnet.
          </li>
          <li>
            <strong>The pool is a frozen snapshot.</strong> Once the run
            starts stepping forward, the simulated pool drifts away from
            live mainnet. Re-capturing a fresher slot is a separate step.
          </li>
          <li>
            <strong>The other traders are simulated.</strong> Noise
            traders, the manipulator, and the victim swap-flow are
            engine-driven actors that exist to keep the pool busy enough
            for sandwich attempts to land. They are not replays of real
            mainnet wallets.
          </li>
        </ul>
      </section>

      <section className="help-page__section" data-section="how-to-read">
        <h2>How to read the run</h2>
        <p>
          Open the Solana tab on the results page. Bundle outcomes show how
          many sandwich attempts landed, how many reverted on partial
          failure, and how many got dropped at the auction stage. The
          JitoSearcher card shows landing rate and tip ROI; it carries an
          &ldquo;uncalibrated landing rate&rdquo; marker because that
          number is the part still being tuned. Tweak the searcher&apos;s
          tip-curve slope or the per-slot compute budget in the builder
          and re-run to see those numbers move.
        </p>
      </section>
    </main>
  );
}
