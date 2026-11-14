import { useState } from "react";

export type Decision = { action: "approve" | "edit" | "reject"; edited_text?: string; reason?: string };

/** Approve / edit / reject. Edits are diffed and re-checked server-side before anything is sent. */
export default function ApprovalPanel({ text, preview, onDecide, busy, kind = "proposal" }: {
  text: string; preview: Record<string, any>; onDecide: (d: Decision) => Promise<void>; busy: boolean; kind?: "proposal" | "message";
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  return (
    <section className="card grid" aria-label="Approval" data-testid="approval-panel">
      <h2>Your decision</h2>
      <div className="small"><strong>Dry-run preview</strong> — exactly what will be sent to <code>{preview?.marketplace}</code>:
        <pre style={{ whiteSpace: "pre-wrap", background: "var(--bg)", padding: 8, borderRadius: 6 }} data-testid="dry-run">{JSON.stringify(preview?.payload, null, 2)}</pre>
        {kind === "proposal" && <p>Submitting costs <strong data-testid="connects-cost">{preview?.submit_connects_cost ?? preview?.connects_cost}</strong> Connects
          {preview?.connects_balance != null && <> (balance {preview.connects_balance})</>}.</p>}</div>
      {editing && <label>Edit the text<textarea rows={8} value={draft} onChange={(e) => setDraft(e.target.value)} data-testid="edit-text" /></label>}
      {rejecting && <label>Why are you rejecting it? (helps GigBid learn)<input value={reason} onChange={(e) => setReason(e.target.value)} data-testid="reject-reason" /></label>}
      <div className="row">
        {!editing && !rejecting && <>
          <button className="primary" disabled={busy} onClick={() => onDecide({ action: "approve" })} data-testid="approve">Approve and {kind === "proposal" ? "submit" : "send"}</button>
          <button disabled={busy} onClick={() => setEditing(true)} data-testid="edit">Edit</button>
          <button className="danger" disabled={busy} onClick={() => setRejecting(true)} data-testid="reject">Reject</button></>}
        {editing && <>
          <button className="primary" disabled={busy || !draft.trim()} onClick={() => onDecide({ action: "edit", edited_text: draft })} data-testid="save-edit">Save edit and approve</button>
          <button onClick={() => { setEditing(false); setDraft(text); }}>Cancel</button></>}
        {rejecting && <>
          <button className="danger" disabled={busy} onClick={() => onDecide({ action: "reject", reason })} data-testid="confirm-reject">Reject</button>
          <button onClick={() => setRejecting(false)}>Cancel</button></>}
      </div>
      <p className="small muted">Nothing leaves GigBid until you approve.{kind === "proposal" ? " In live Upwork mode you also confirm the draft on Upwork." : ""}</p>
    </section>
  );
}
