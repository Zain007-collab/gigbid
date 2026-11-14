// Thin wrapper over the client generated from FastAPI's OpenAPI schema (src/lib/api/schema.d.ts).
import createClient from "openapi-fetch";
import type { components, paths } from "./api/schema";

export const api = createClient<paths>({ baseUrl: "/", credentials: "include" });

export type Me = components["schemas"]["Me"];
export type InboxItem = components["schemas"]["InboxItem"];
export type JobDetail = components["schemas"]["JobDetail"];
export type Settings = components["schemas"]["Settings"];
export type PipelineCard = components["schemas"]["PipelineCard"];
export type Conversation = components["schemas"]["Conversation"];
export type MemoryItem = components["schemas"]["MemoryItem"];
export type Analytics = components["schemas"]["Analytics"];
export type Health = components["schemas"]["Health"];

export function errText(e: unknown): string {
  if (!e) return "Something went wrong";
  if (typeof e === "string") return e;
  const d = (e as { detail?: unknown }).detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => (x as { msg?: string }).msg).join("; ");
  return "Something went wrong";
}

export const pct = (x?: number | null) => (x == null ? "—" : `${Math.round(x * 100)}%`);
export const money = (x?: number | null) => (x == null ? "—" : `$${Math.round(x).toLocaleString()}`);
