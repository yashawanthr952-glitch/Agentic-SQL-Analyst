export default function ResultTable({ result }) {
  if (!result) return null;
  const { columns, rows, row_count: rowCount, elapsed_ms: elapsedMs } = result;

  return (
    <section className="panel">
      <h2>
        Result <span className="tag">{rowCount} rows · {elapsedMs} ms</span>
      </h2>

      {rowCount === 0 ? (
        <p className="sub">Query returned no rows.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>{cell === null ? <em>null</em> : String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
