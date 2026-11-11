import { type ReactNode, useEffect, useRef, useState } from "react";

import { getEvaluation, getTools } from "../api";
import type { EvalReport, ToolSpec } from "../types";

/** Markdown as the evaluation reports use it: paragraphs and pipe tables. Nothing else is interpreted. */
function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (line.startsWith("|")) {
      const table: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
        if (!cells.every((cell) => /^:?-+:?$/.test(cell))) table.push(cells);
        i += 1;
      }
      const [head, ...body] = table;
      blocks.push(
        <div className="table-wrap" key={i}>
          <table className="numbers">
            <thead><tr>{head.map((cell, index) => <th key={index} scope="col">{cell}</th>)}</tr></thead>
            <tbody>{body.map((row, r) => <tr key={r}>{row.map((cell, c) => (c === 0 ? <th key={c} scope="row">{cell}</th> : <td key={c}>{cell}</td>))}</tr>)}</tbody>
          </table>
        </div>,
      );
    } else {
      if (line) blocks.push(<p key={i}>{line.replace(/[*_`]/g, "")}</p>);
      i += 1;
    }
  }
  return <>{blocks}</>;
}

function Evaluation() {
  const [reports, setReports] = useState<EvalReport[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    getEvaluation().then((body) => setReports(body.reports)).catch(() => setFailed(true));
  }, []);
  if (failed) return <p className="problem">The evaluation reports could not be loaded.</p>;
  if (!reports) return <p>Loading the reports.</p>;
  const written = reports.filter((report) => report.kind === "markdown" && report.text);
  if (written.length === 0) return <p>No evaluation report has been written yet. Run <code>voiceshop eval retrieval</code> to create the first one.</p>;
  return (
    <>
      {written.map((report) => (
        <article key={report.name} className="report">
          <h3>{report.name.replace(/\.md$/, "").replace(/[/_]/g, " ")}</h3>
          <Markdown text={report.text ?? ""} />
        </article>
      ))}
    </>
  );
}

function Tools() {
  const [tools, setTools] = useState<{ transport: string; tools: ToolSpec[] } | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    getTools().then(setTools).catch(() => setFailed(true));
  }, []);
  if (failed) return <p className="problem">The tool list could not be loaded.</p>;
  if (!tools) return <p>Asking the MCP server which tools it offers.</p>;
  return (
    <>
      <p>The agents found these tools by asking the MCP server ({tools.transport}); nothing about them is written into the agent code.</p>
      {tools.tools.map((tool) => (
        <article key={tool.name} className="report">
          <h3>{tool.name}</h3>
          {(tool.description ?? "").split("\n\n").slice(0, 2).map((paragraph, index) => <p key={index}>{paragraph}</p>)}
          <table className="numbers">
            <thead><tr><th scope="col">Argument</th><th scope="col">What it is</th></tr></thead>
            <tbody>
              {Object.entries(tool.input_schema?.properties ?? {}).map(([name, spec]) => (
                <tr key={name}>
                  <th scope="row">{name}{tool.input_schema?.required?.includes(name) ? " (required)" : ""}</th>
                  <td>{spec.description ?? spec.type ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      ))}
    </>
  );
}

export type Panel = "evaluation" | "tools";

/** A side sheet for the two things a reviewer looks for besides the answer: the evidence and the tools. */
export function Drawer({ panel, onClose }: { panel: Panel; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);
  return (
    <dialog ref={dialog} className="drawer" onClose={onClose} onClick={(event) => { if (event.target === dialog.current) onClose(); }}>
      <div className="drawer__body">
        <header>
          <h2>{panel === "evaluation" ? "Evaluation results" : "Tools the agents can call"}</h2>
          <button type="button" className="button button--quiet" onClick={onClose}>Close</button>
        </header>
        {panel === "evaluation" ? <Evaluation /> : <Tools />}
      </div>
    </dialog>
  );
}
