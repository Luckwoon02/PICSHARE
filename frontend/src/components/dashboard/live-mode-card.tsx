"use client";

import React, { useEffect, useState } from "react";
import { Loader2, Radio } from "lucide-react";
import { toast } from "sonner";
import { api, type EventItem } from "@/lib/api";

interface LiveModeCardProps {
    eventId: string;
    enabled: boolean;
    /** photos that arrived over FTP/SFTP so far */
    liveCount: number;
    /** ISO time (UTC) of the newest one */
    lastLiveAt: string | null;
    onChanged: (event: EventItem) => void;
}

/** "just now", "3 min ago", ... for a past time, given the current time. */
function ago(from: string, now: number): string {
    const seconds = Math.max(0, Math.round((now - new Date(from).getTime()) / 1000));
    if (seconds < 45) return "just now";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours} h ago`;
    return `${Math.round(hours / 24)} days ago`;
}

export function LiveModeCard({ eventId, enabled, liveCount, lastLiveAt, onChanged }: LiveModeCardProps) {
    const [saving, setSaving] = useState(false);
    const [now, setNow] = useState(() => Date.now());

    // Keep "2 min ago" moving while live mode is on, and bring the clock up to date when a new photo arrives
    // (the 30 s timer can be behind, e.g. in a background tab)
    useEffect(() => {
        if (!enabled) return;
        const soon = setTimeout(() => setNow(Date.now()), 0);
        const timer = setInterval(() => setNow(Date.now()), 30000);
        return () => {
            clearTimeout(soon);
            clearInterval(timer);
        };
    }, [enabled, lastLiveAt]);

    const toggle = async () => {
        setSaving(true);
        try {
            const updated = await api<EventItem>(`/events/${eventId}/live`, {
                method: "PATCH",
                body: JSON.stringify({ enabled: !enabled }),
            });
            onChanged(updated);
            toast.success(updated.live_enabled ? "Live mode is on" : "Live mode is off");
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't change live mode");
        } finally {
            setSaving(false);
        }
    };

    return (
        <section className="rounded-2xl border border-border bg-card p-5 flex flex-wrap items-center justify-between gap-4">
            <div className="space-y-1 min-w-0">
                <div className="flex items-center gap-2">
                    <Radio className={`w-4 h-4 ${enabled ? "text-red-500" : "text-muted-foreground"}`} />
                    <h2 className="font-bold">Live mode</h2>
                    {enabled && (
                        <span className="inline-flex items-center rounded-full bg-red-50 dark:bg-red-950/40 text-red-600 dark:text-red-300 px-2 py-0.5 text-xs font-semibold">
                            On
                        </span>
                    )}
                </div>
                <p className="text-sm text-muted-foreground">
                    {enabled
                        ? "Photos your camera sends over FTP appear here automatically while the event runs."
                        : "Turn this on to have photos from your camera (sent over FTP) added to this event as they arrive."}
                </p>
                {enabled && (
                    <p className="text-sm">
                        <span className="font-semibold">{liveCount}</span> live photo{liveCount === 1 ? "" : "s"} received
                        {lastLiveAt ? `, the latest ${ago(lastLiveAt, now)}` : ". Waiting for the first one."}
                    </p>
                )}
                {enabled && (
                    <p className="text-xs text-muted-foreground">
                        Camera connection details (server, username and password) will appear here once the FTP server is set up.
                    </p>
                )}
            </div>
            <button
                type="button"
                role="switch"
                aria-checked={enabled}
                aria-label="Live mode"
                disabled={saving}
                onClick={toggle}
                className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 disabled:opacity-60 ${
                    enabled ? "bg-indigo-600" : "bg-muted-foreground/30"
                }`}
            >
                {saving ? (
                    <Loader2 className="mx-auto w-4 h-4 animate-spin text-white" />
                ) : (
                    <span
                        className={`inline-block h-5 w-5 rounded-full bg-white shadow transition-transform ${
                            enabled ? "translate-x-6" : "translate-x-1"
                        }`}
                    />
                )}
            </button>
        </section>
    );
}
