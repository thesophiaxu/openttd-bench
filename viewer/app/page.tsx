"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { TraceView } from "@/components/trace-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  agentSeconds,
  formatSeconds,
  parseAtif,
  traceMeta,
  traceId,
  rewardValue,
  traceCompany,
  traceOutcome,
  traceRole,
  type ParsedRun,
  type Episode,
  type Trajectory,
} from "@/lib/trace";

type RunEntry = { path: string; name: string; group: string; mtime: number; size: number; episodes: number };

export default function Page() {
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [root, setRoot] = useState<string>("");
  const [listError, setListError] = useState<string | null>(null);
  const [run, setRun] = useState<ParsedRun | null>(null);
  const [selected, setSelected] = useState<{ episode: number; trace: number }>({ episode: 0, trace: 0 });
  const [loading, setLoading] = useState<string | null>(null);
  // On narrow screens the app is one column: the picker, or the selected trace with a back button.
  const [mobileView, setMobileView] = useState<"list" | "trace">("list");

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/runs");
      const data = (await res.json()) as { root: string; runs: RunEntry[]; error?: string };
      if (data.error) throw new Error(data.error);
      setRuns(data.runs);
      setRoot(data.root);
      setListError(null);
    } catch (error) {
      setListError(String(error));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openRun = useCallback(async (entry: RunEntry) => {
    setLoading(entry.path);
    try {
      const res = await fetch(`/api/traces?path=${encodeURIComponent(entry.path)}`);
      const text = await res.text();
      if (!res.ok) throw new Error(text);
      setRun(parseAtif(text, entry.name));
      setListError(null);
      setSelected({ episode: 0, trace: 0 });
      setMobileView("list");
    } catch (error) {
      setListError(String(error));
    } finally {
      setLoading(null);
    }
  }, []);

  const openFile = useCallback(async (file: File) => {
    try {
      setRun(parseAtif(await file.text(), file.name));
      setSelected({ episode: 0, trace: 0 });
      setMobileView("list");
      setListError(null);
    } catch (error) { setListError(String(error)); }
  }, []);

  const episode: Episode | undefined = run?.episodes[selected.episode];
  const trace: Trajectory | undefined = episode?.traces[selected.trace];
  const sortedTraces = useMemo(
    () => (episode ? episode.traces.map((t, i) => ({ t, i })).sort((a, b) => (traceCompany(a.t) ?? 0) - (traceCompany(b.t) ?? 0)) : []),
    [episode],
  );

  return (
    <div
      className="flex h-screen"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const file = e.dataTransfer.files?.[0];
        if (file) void openFile(file);
      }}
    >
      <aside className={`${mobileView === "trace" && trace ? "hidden" : "flex"} w-full shrink-0 flex-col border-r md:flex md:w-96`}>
        <div className="space-y-2 border-b p-3">
          <div className="flex items-center justify-between">
            <h1 className="font-semibold">OpenTTD bench trajectories</h1>
            <Button size="sm" variant="ghost" onClick={() => void refresh()}>refresh</Button>
          </div>
          <label className="block text-muted-foreground text-xs">
            Drop an <span className="font-mono">ATIF .json</span> anywhere, or pick one:
            <input
              type="file"
              accept=".json"
              className="mt-1 block w-full text-xs"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void openFile(file);
              }}
            />
          </label>
          {root && <div className="truncate text-muted-foreground text-xs" title={root}>runs under {root}</div>}
          {listError && <div className="text-destructive text-xs">{listError}</div>}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {trace && (
            <button className="w-full border-b bg-primary/10 p-2 text-left text-xs md:hidden" onClick={() => setMobileView("trace")}>
              open selected trace: {traceMeta(trace).task?.data?.name ?? traceId(trace).slice(0, 8)} ›
            </button>
          )}
          <div className="p-2">
            <h3 className="px-1 py-1 font-medium text-muted-foreground text-xs uppercase">runs</h3>
            {runs.length === 0 && <div className="px-1 text-muted-foreground text-xs">no converted ATIF runs found</div>}
            {runs.map((entry) => (
              <button
                key={entry.path}
                className={`block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-muted ${run?.name === entry.name ? "bg-muted" : ""}`}
                onClick={() => void openRun(entry)}
              >
                <div className="truncate font-medium" title={entry.path}>{entry.name}</div>
                <div className="text-muted-foreground">{entry.group || "."} · {entry.episodes} episode{entry.episodes === 1 ? "" : "s"} · {new Date(entry.mtime).toLocaleString()}{loading === entry.path ? " · loading…" : ""}</div>
              </button>
            ))}
          </div>
          {run && (
            <div className="border-t p-2">
              <h3 className="px-1 py-1 font-medium text-muted-foreground text-xs uppercase">{run.name}: episodes</h3>
              {run.episodes.map((ep, ei) => (
                <div key={ep.id ?? ei} className="mb-1">
                  <button
                    className={`block w-full rounded px-2 py-1 text-left text-xs hover:bg-muted ${selected.episode === ei ? "bg-muted" : ""}`}
                    onClick={() => setSelected({ episode: ei, trace: 0 })}
                  >
                    <span className="font-medium">{ep.task?.data?.name ?? `episode ${ei + 1}`}</span>{" "}
                    {ep.ok !== undefined && <Badge variant={ep.ok ? "secondary" : "destructive"} className="ml-1 text-[10px]">{ep.ok ? "ok" : "failed"}</Badge>}
                  </button>
                  {selected.episode === ei &&
                    sortedTraces.map(({ t, i }) => {
                      const outcome = traceOutcome(t);
                      const reward = rewardValue(t);
                      return (
                        <button
                          key={traceId(t)}
                          className={`ml-3 block w-[calc(100%-0.75rem)] rounded px-2 py-1 text-left text-xs hover:bg-muted ${selected.trace === i ? "bg-primary/10" : ""}`}
                          onClick={() => {
                            setSelected({ episode: ei, trace: i });
                            setMobileView("trace");
                          }}
                        >
                          <span className="font-mono">{traceRole(t)}</span>
                          {traceCompany(t) !== null && <span className="text-muted-foreground"> c{traceCompany(t)}</span>}
                          <span className="ml-2 font-semibold">{(outcome?.score ?? reward)?.toFixed(3) ?? "–"}</span>
                          {outcome?.values["rank:place"] !== undefined && <span className="text-muted-foreground"> · place {outcome.values["rank:place"]}</span>}
                          <span className="text-muted-foreground"> · {formatSeconds(agentSeconds(t))} · {traceMeta(t).stop_condition ?? (t.extra?.parent_trajectory_id ? "subagent" : traceMeta(t).ok ? "ok" : "unknown")}</span>
                        </button>
                      );
                    })}
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
      <main className={`${mobileView === "trace" && trace ? "block" : "hidden"} min-w-0 flex-1 md:block`}>
        {trace ? (
          <TraceView key={traceId(trace)} trace={trace} onBack={() => setMobileView("list")} />
        ) : (
          <div className="flex h-full items-center justify-center p-8 text-center text-muted-foreground text-sm">
            Pick a run on the left, or drop an ATIF .json file here.
          </div>
        )}
      </main>
    </div>
  );
}
