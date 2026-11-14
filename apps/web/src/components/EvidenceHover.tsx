import { useId, useState } from "react";

type Ev = { id: string; type: string; project?: string | null; text: string };
export type ClaimView = { text: string; status: string; evidence_ids: string[]; evidence?: Ev[]; note?: string | null };

/** A proposal sentence that reveals the portfolio evidence behind it on hover or keyboard focus. */
export default function EvidenceHover({ claim, index }: { claim: ClaimView; index: number }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const has = (claim.evidence?.length ?? 0) > 0;
  const cls = claim.note === "not a factual claim" ? "none" : claim.status;
  return (
    <span
      className={`claim ${cls}`} tabIndex={0} aria-describedby={open ? id : undefined} data-testid={`claim-${index}`}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
    >
      {claim.text}{" "}
      {open && (
        <span className="pop" role="tooltip" id={id}>
          <strong>{claim.status === "supported" ? "Supported" : claim.status === "partial" ? "Partly supported" : claim.note === "not a factual claim" ? "Not a factual claim" : "Unsupported"}</strong>
          {has ? claim.evidence!.map((e) => (
            <span key={e.id} style={{ display: "block", marginTop: 6 }}>
              <span className="badge">{e.type}</span> <code>{e.id}</code>{"\n"}{e.text.slice(0, 280)}…
            </span>
          )) : <span style={{ display: "block", marginTop: 6 }} className="muted">No evidence cited.</span>}
        </span>
      )}
    </span>
  );
}
