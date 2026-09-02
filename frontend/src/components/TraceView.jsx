/**
 * The graph trace: plan, then every attempt with the stage it reached and the
 * error that bounced it. This is the append-only `attempts` list from graph
 * state -- if it were a last-write-wins field, only the final attempt would
 * ever be visible here.
 */
export default function TraceView({ response }) {
  const { plan, attempts = [], retry_count: retryCount, status } = response;

  return (
    <section className="panel">
      <h2>Trace</h2>

      <ol className="trace">
        <li className="step ok">
          <div className="step-head">
            <span className="badge">plan</span>
          </div>
          <pre className="plan">{plan || "(no plan)"}</pre>
        </li>

        {attempts.map((attempt, index) => (
          <li key={index} className={`step ${attempt.ok ? "ok" : "bad"}`}>
            <div className="step-head">
              <span className="badge">
                attempt {attempt.n} · {attempt.stage}
              </span>
              <span className={attempt.ok ? "tag pass" : "tag fail"}>
                {attempt.ok ? "passed" : "rejected"}
              </span>
            </div>
            <pre className="sql">{attempt.sql || "(empty)"}</pre>
            {attempt.error && <p className="err">{attempt.error}</p>}
          </li>
        ))}

        <li className={`step ${status === "succeeded" ? "ok" : "bad"}`}>
          <div className="step-head">
            <span className="badge">{status}</span>
            <span className="tag">
              {retryCount} {retryCount === 1 ? "repair" : "repairs"}
            </span>
          </div>
        </li>
      </ol>
    </section>
  );
}
