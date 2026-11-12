import { useState } from "react";

import type { Citation, TableRow } from "../types";

/** A product picture when the source has one that still loads; a plain square otherwise (2020 image links often no longer work). */
function Thumbnail({ url }: { url?: string | null }) {
  const [broken, setBroken] = useState(false);
  if (!url || broken) return <span className="product__blank" aria-hidden="true" />;
  return <img src={url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} />;
}

function Price({ value }: { value: string }) {
  const match = value.match(/^\$[\d,]+(?:\.\d{2})?/);
  if (!match) return <span>{value}</span>;
  return (
    <>
      <strong className="price">{match[0]}</strong>
      <span className="price-note">{value.slice(match[0].length).trim()}</span>
    </>
  );
}

function CitationCell({ row, citations }: { row: TableRow; citations: Citation[] }) {
  const support = row.support_refs ?? [];
  const own = citations.filter((c) => c.ref === row.ref || (row.live_url && c.id === row.live_url) || support.includes(c.ref));
  if (own.length === 0) return <span>{row.citation}</span>;
  return (
    <ul className="cites">
      {own.map((c) => (
        <li key={c.id}>
          {c.kind === "catalog"
            ? <span className="docid" title={`Private catalog document, snapshot ${c.as_of}`}>{c.id}</span>
            : <a href={c.url ?? c.id} target="_blank" rel="noreferrer noopener" title={`Retrieved ${c.as_of}`}>{c.source || "live source"}</a>}
        </li>
      ))}
    </ul>
  );
}

/**
 * The comparison the assignment asks for: Product, Price, Rating, Ingredients, Source, Citation, best first.
 * After a follow-up ("is the second one in stock") the list stays as it was, so "the second one" keeps its meaning;
 * the rows the answer is about are the ones with a reason, and those are marked instead of a top pick.
 */
export function ComparisonTable({ rows, citations }: { rows: TableRow[]; citations: Citation[] }) {
  if (rows.length === 0) return null;
  const fresh = rows.every((row) => row.why);
  return (
    <div className="table-wrap">
      <table className="compare">
        <caption className="visually-hidden">{fresh ? "Recommended products, best first" : "The products on screen; the answer is about the marked ones"}</caption>
        <thead>
          <tr>
            <th scope="col">Product</th>
            <th scope="col">Price</th>
            <th scope="col">Rating</th>
            <th scope="col">Ingredients</th>
            <th scope="col">Source</th>
            <th scope="col">Citation</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.ref} className={(fresh ? row.rank === 1 : Boolean(row.why)) ? "top" : undefined}>
              <th scope="row">
                <div className="product">
                  <Thumbnail url={row.image_url} />
                  <div>
                    {fresh && row.rank === 1 && <span className="tag">Top pick</span>}
                    <span className="product__name" title={row.product}>{row.product}</span>
                    {row.why && <span className="product__why">{row.why}</span>}
                    {row.caveat && <span className="product__caveat">{row.caveat}</span>}
                  </div>
                </div>
              </th>
              <td data-label="Price"><div><Price value={row.price} /></div></td>
              <td data-label="Rating">{row.rating}</td>
              <td data-label="Ingredients">{row.ingredients}</td>
              <td data-label="Source">{row.source}</td>
              <td data-label="Citation"><CitationCell row={row} citations={citations} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="table-note">Chosen and ordered by an AI assistant from the sources cited in each row.</p>
    </div>
  );
}
