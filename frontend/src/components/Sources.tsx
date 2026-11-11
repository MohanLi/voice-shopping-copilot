import type { Answer } from "../types";

function when(asOf: string): string {
  if (!asOf) return "";
  const date = new Date(asOf);
  if (asOf.length > 10 && !Number.isNaN(date.getTime())) return `retrieved ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  return `snapshot ${asOf}`;
}

/** Caveats written by the code (not by a model) and every source the answer rests on. */
export function Sources({ answer }: { answer: Answer }) {
  if (answer.notes.length === 0 && answer.citations.length === 0) return null;
  return (
    <section className="sources" aria-label="Notes and sources">
      {answer.notes.length > 0 && (
        <>
          <h2>Good to know</h2>
          <ul className="notes">
            {answer.notes.map((note) => <li key={note}>{note}</li>)}
          </ul>
        </>
      )}
      {answer.citations.length > 0 && (
        <>
          <h2>Sources</h2>
          <ol className="source-list">
            {answer.citations.map((c) => (
              <li key={`${c.ref}-${c.id}`}>
                {c.kind === "catalog" ? (
                  <>
                    <span className="docid">{c.id}</span> {c.title}
                    <span className="source-meta"> Private catalog, {when(c.as_of)}.</span>
                  </>
                ) : (
                  <>
                    <a href={c.url ?? c.id} target="_blank" rel="noreferrer noopener">{c.title}</a>
                    <span className="source-meta"> {c.source}, {when(c.as_of)}.</span>
                  </>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
