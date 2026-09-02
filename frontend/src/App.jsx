import { useState } from "react";
import { runQuery } from "./api";
import QueryBox from "./components/QueryBox";
import TraceView from "./components/TraceView";
import SqlPanel from "./components/SqlPanel";
import ResultTable from "./components/ResultTable";

const EXAMPLES = [
  "What are the top 10 customers by total revenue?",
  "How many orders were refunded in each region?",
  "Which product category has the highest average order value?",
  "Show monthly order counts for the last 12 months",
];

export default function App() {
  const [response, setResponse] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function submit(question) {
    setLoading(true);
    setError(null);
    setResponse(null);
    try {
      setResponse(await runQuery(question));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Agentic SQL Analyst</h1>
        <p className="sub">
          plan → generate → validate → execute → repair, against PostgreSQL
        </p>
      </header>

      <QueryBox onSubmit={submit} loading={loading} examples={EXAMPLES} />

      {error && <div className="panel error">Request failed — {error}</div>}

      {response && (
        <>
          <TraceView response={response} />
          <SqlPanel response={response} />
          {response.status === "succeeded" ? (
            <ResultTable result={response.result} />
          ) : (
            <div className="panel error">
              <h2>Agent gave up</h2>
              <p>{response.failure_reason || "No result was produced."}</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
