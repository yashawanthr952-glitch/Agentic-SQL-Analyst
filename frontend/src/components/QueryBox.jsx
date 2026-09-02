import { useState } from "react";

export default function QueryBox({ onSubmit, loading, examples }) {
  const [value, setValue] = useState("");

  function handleSubmit(event) {
    event.preventDefault();
    const question = value.trim();
    if (question && !loading) onSubmit(question);
  }

  return (
    <form className="panel querybox" onSubmit={handleSubmit}>
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit(e);
        }}
        placeholder="Ask a question about the data…"
        rows={3}
        disabled={loading}
      />
      <div className="row">
        <button type="submit" disabled={loading || !value.trim()}>
          {loading ? "Running…" : "Run"}
        </button>
        <span className="hint">⌘/Ctrl + Enter</span>
      </div>
      <div className="examples">
        {examples.map((example) => (
          <button
            key={example}
            type="button"
            className="chip"
            disabled={loading}
            onClick={() => {
              setValue(example);
              onSubmit(example);
            }}
          >
            {example}
          </button>
        ))}
      </div>
    </form>
  );
}
