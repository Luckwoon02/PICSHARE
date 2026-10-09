"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, CalendarDays, Check, Copy, ExternalLink, Loader2, Lock, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, formatBytes, formatDateRange, planInfo, type EventItem } from "@/lib/api";
import { useAuth } from "@/lib/use-auth";
import { StatusBadge } from "@/components/dashboard/status-badge";
import { UploadPanel } from "@/components/dashboard/upload-panel";
import { PhotoGrid, type Photo } from "@/components/dashboard/photo-grid";
import { GuestsTable, type Guest } from "@/components/dashboard/guests-table";

interface Storage {
    capacity_bytes: number;
    used_bytes: number;
    photo_count: number;
    guest_count: number;
}

interface Progress {
    total: number;
    pending: number;
    /** stored and waiting for, or in, the worker (older servers don't send it; `pending` is used then) */
    processing?: number;
    /** uploaded photos still waiting for their upload batch to finish */
    held?: number;
    /** photos whose upload link was handed out but whose file hasn't arrived */
    awaiting_upload?: number;
    processed: number;
    errors: number;
    total_faces: number;
}

// While photos are being processed the page checks the (tiny) status endpoint, and only reloads
// the whole gallery when something has actually changed.
const POLL_START_MS = 5000;
const POLL_MAX_MS = 20000; // the check slows down to this while nothing changes or the tab is hidden
const GALLERY_EVERY_MS = 15000; // the gallery is reloaded at most this often
const STALL_MS = 90000; // nothing finishing for this long, while photos are waiting, is reported as a problem

const reason = (err: unknown) => (err instanceof Error ? err.message : String(err));

const finishedCount = (p: Progress | null) => (p ? p.processed + p.errors : 0);

type Tab = "photos" | "guests";

export default function EventDetailClient() {
    const { id } = useParams<{ id: string }>();
    const router = useRouter();
    const { user, loading: authLoading } = useAuth({ required: true });

    const [event, setEvent] = useState<EventItem | null>(null);
    const [storage, setStorage] = useState<Storage | null>(null);
    const [progress, setProgress] = useState<Progress | null>(null);
    const [photos, setPhotos] = useState<Photo[]>([]);
    const [guests, setGuests] = useState<Guest[]>([]);
    const [loadingPhotos, setLoadingPhotos] = useState(true);
    const [loadingGuests, setLoadingGuests] = useState(true);
    const [tab, setTab] = useState<Tab>("photos");
    const [copied, setCopied] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [statusError, setStatusError] = useState<string | null>(null); // why progress couldn't be read
    const [stalled, setStalled] = useState(false); // photos are waiting but nothing has finished for a while

    const loadPhotos = useCallback(async () => {
        try {
            const data = await api<{ photos: Photo[] }>(`/photos/event/${id}/gallery?limit=1000`);
            setPhotos(data.photos);
            // Say so in the console whenever the gallery has photos that won't look right
            const bad = data.photos.filter(
                (p) => p.status === "error" || (p.status === "processed" && (p.error_detail || !p.presigned_thumbnail_url))
            );
            if (bad.length > 0) {
                console.warn(
                    `[pixello] ${bad.length} of ${data.photos.length} photo(s) have a problem (first few):`,
                    bad.slice(0, 5).map((p) => `${p.original_file_name}: ${p.error_detail ?? (p.status === "error" ? "failed" : "no preview")}`)
                );
            }
        } catch (err) {
            console.warn(`[pixello] couldn't load the photo list: ${reason(err)}`);
            toast.error(err instanceof Error ? err.message : "Couldn't load photos");
        } finally {
            setLoadingPhotos(false);
        }
    }, [id]);

    const progressRef = useRef<Progress | null>(null);

    // Just the processing counts: cheap, so it's what the polling uses
    const loadStatus = useCallback(async () => {
        try {
            const p = await api<Progress>(`/photos/status/${id}`);
            progressRef.current = p;
            // Same numbers as before: keep the old object so the page doesn't redraw for nothing
            setProgress((prev) => (prev && JSON.stringify(prev) === JSON.stringify(p) ? prev : p));
            setStatusError(null);
            return p;
        } catch (err) {
            console.warn(`[pixello] couldn't read upload progress: ${reason(err)}`);
            setStatusError(reason(err));
            return null;
        }
    }, [id]);

    // Counts plus storage usage (which makes the server list the event's files in S3)
    const loadStats = useCallback(async () => {
        try {
            const [s, p] = await Promise.all([api<Storage>(`/events/${id}/storage`), loadStatus()]);
            setStorage(s);
            return p;
        } catch (err) {
            console.warn(`[pixello] couldn't read storage usage: ${reason(err)}`);
            return null;
        }
    }, [id, loadStatus]);

    // Photos that are stored but "waiting for the upload to finish", found when the page loads, belong to an
    // upload that was interrupted (page reloaded or closed): nothing will ever finish it. Process them now
    // instead of leaving them to a timeout, and recover any that reached storage without being confirmed.
    const releaseInterrupted = useCallback(
        async (held: number) => {
            console.warn(`[pixello] ${held} photo(s) from an interrupted upload were waiting; processing them now.`);
            try {
                const res = await api<{ adopted?: number }>(`/photos/upload-complete?event_id=${id}`, {
                    method: "POST",
                    body: JSON.stringify({ uploaded: [], failed: [], final: true }),
                });
                const total = held + (res.adopted ?? 0);
                toast.info(`An earlier upload didn't finish. Processing the ${total} photo${total === 1 ? "" : "s"} that reached storage.`);
                const p = await loadStats();
                loadPhotos();
                if (p && (p.awaiting_upload ?? 0) > 0) {
                    toast.warning(`${p.awaiting_upload} photo${p.awaiting_upload === 1 ? "" : "s"} never finished uploading. Please upload them again.`);
                }
            } catch (err) {
                console.warn(`[pixello] couldn't release the interrupted upload: ${reason(err)}`);
                toast.error(err instanceof Error ? err.message : "Couldn't process the photos from the earlier upload");
            }
        },
        [id, loadStats, loadPhotos]
    );

    const loadGuests = useCallback(async () => {
        try {
            const data = await api<{ guests: Guest[] }>(`/guests/event/${id}`);
            setGuests(data.guests);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't load guests");
        } finally {
            setLoadingGuests(false);
        }
    }, [id]);

    // Initial load
    useEffect(() => {
        if (!user) return;
        api<EventItem>(`/events/${id}`)
            .then((ev) => {
                if (ev.status !== "active") {
                    router.replace(`/dashboard/events/${id}/payment`);
                    return;
                }
                setEvent(ev);
                loadStats().then((p) => {
                    if (p && (p.held ?? 0) > 0) releaseInterrupted(p.held ?? 0);
                });
                loadPhotos();
                if (ev.face_scan_enabled !== false) loadGuests();
            })
            .catch((err) => {
                toast.error(err.message);
                router.replace("/dashboard");
            });
    }, [user, id, router, loadStats, loadPhotos, loadGuests, releaseInterrupted]);

    // While photos are still being processed, keep thumbnails and counts up to date
    const processing = progress?.processing ?? progress?.pending ?? 0;
    const hasWork = processing > 0;
    useEffect(() => {
        if (!event || !hasWork) return;
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        let delay = POLL_START_MS;
        let loadedDone = finishedCount(progressRef.current);
        let loadedAt = Date.now();
        let inFlight = false;
        let lastCount = finishedCount(progressRef.current); // how many photos had finished at the last look
        let lastMovedAt = Date.now(); // when that number last went up

        const tick = async () => {
            if (document.hidden) {
                timer = setTimeout(tick, POLL_MAX_MS); // nobody is looking: check back rarely
                return;
            }
            inFlight = true;
            try {
                const p = await loadStatus();
                if (cancelled) return;
                if (!p) {
                    timer = setTimeout(tick, POLL_MAX_MS); // couldn't reach the server: don't hammer it
                    return;
                }
                // Say what is wrong on every check, so a stuck or failing batch is never silent
                const count = finishedCount(p);
                if (count !== lastCount) {
                    lastCount = count;
                    lastMovedAt = Date.now();
                }
                const quietMs = Date.now() - lastMovedAt;
                const isStalled = quietMs >= STALL_MS;
                setStalled(isStalled);
                if (isStalled) {
                    console.warn(
                        `[pixello] nothing has finished for ${Math.round(quietMs / 1000)}s while ${p.processing ?? p.pending} ` +
                            "photo(s) are waiting. The server may be busy or stuck: check the backend terminal for [worker] lines."
                    );
                }
                if (p.errors > 0) {
                    console.warn(`[pixello] ${p.errors} photo(s) failed so far. Hover a "Failed" tile for the reason.`);
                }

                const changed = finishedCount(p) !== loadedDone;
                if (changed && Date.now() - loadedAt >= GALLERY_EVERY_MS) {
                    loadedDone = finishedCount(p);
                    loadedAt = Date.now();
                    await loadPhotos();
                }
                delay = changed ? POLL_START_MS : Math.min(delay * 1.5, POLL_MAX_MS);
                if (!cancelled) timer = setTimeout(tick, delay);
            } finally {
                inFlight = false;
            }
        };

        // Coming back to the tab: catch up right away instead of waiting out the slow hidden-tab timer
        const onVisible = () => {
            if (document.hidden || inFlight || cancelled) return;
            clearTimeout(timer);
            loadedAt = 0; // the gallery is stale after being away, so allow an immediate reload
            delay = POLL_START_MS;
            tick();
        };
        document.addEventListener("visibilitychange", onVisible);

        timer = setTimeout(tick, delay);
        return () => {
            cancelled = true;
            clearTimeout(timer);
            document.removeEventListener("visibilitychange", onVisible);
            setStalled(false); // a new batch starts without an old warning
        };
    }, [event, hasWork, loadStatus, loadPhotos]);

    // When everything has finished, refresh once so the last photos and the storage figure show up
    const wasWorking = useRef(false);
    useEffect(() => {
        if (!event) return;
        if (hasWork) {
            wasWorking.current = true;
        } else if (wasWorking.current) {
            wasWorking.current = false;
            loadStats();
            loadPhotos();
        }
    }, [event, hasWork, loadStats, loadPhotos]);

    const refreshAfterChange = () => {
        loadStats();
        loadPhotos();
    };

    const guestLink = event ? `${window.location.origin}/event/${event.slug}` : "";

    const copyLink = async () => {
        await navigator.clipboard.writeText(guestLink);
        setCopied(true);
        toast.success("Guest link copied");
        setTimeout(() => setCopied(false), 2000);
    };

    const deleteEvent = async () => {
        if (!event) return;
        const typed = prompt(
            `This permanently deletes "${event.name}" with all photos, guests and face data.\n\nType the event name to confirm:`
        );
        if (typed !== event.name) {
            if (typed !== null) toast.error("Name didn't match — event not deleted");
            return;
        }
        setDeleting(true);
        try {
            await api(`/events/${id}`, { method: "DELETE" });
            toast.success("Event deleted");
            router.push("/dashboard");
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't delete event");
            setDeleting(false);
        }
    };

    if (authLoading || !user || !event) {
        return (
            <div className="flex h-[60vh] items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-indigo-600" />
            </div>
        );
    }

    // Older servers don't send the plan fields: treat such events as face scan, which is what they always were
    const faceScan = event.face_scan_enabled !== false;
    const plan = planInfo(event.plan);

    const remaining = storage && event.storage_capacity_gb ? Math.max(storage.capacity_bytes - storage.used_bytes, 0) : null;
    const usedPct = storage && storage.capacity_bytes > 0 ? Math.min((storage.used_bytes / storage.capacity_bytes) * 100, 100) : 0;

    return (
        <div className="max-w-6xl mx-auto px-4 py-10 space-y-8">
            <Link href="/dashboard" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
                <ArrowLeft className="w-4 h-4" />
                All events
            </Link>

            {/* Header */}
            <header className="flex flex-wrap items-start justify-between gap-4">
                <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-3">
                        <h1 className="text-3xl font-bold tracking-tight">{event.name}</h1>
                        <StatusBadge status={event.status} />
                        <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-1 text-xs font-semibold text-muted-foreground">
                            {plan.label}
                        </span>
                        {faceScan && event.secret_code && (
                            <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300 px-2.5 py-1 text-xs font-semibold">
                                <Lock className="w-3 h-3" />
                                Code: {event.secret_code}
                            </span>
                        )}
                    </div>
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <CalendarDays className="w-4 h-4" />
                        {formatDateRange(event.start_date ?? event.date, event.end_date)}
                    </p>
                </div>
                {faceScan && (
                    <div className="flex gap-2">
                        <Button variant="outline" onClick={copyLink}>
                            {copied ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4" />}
                            Copy guest link
                        </Button>
                        <Button asChild variant="outline">
                            <a href={guestLink} target="_blank" rel="noopener noreferrer">
                                <ExternalLink className="w-4 h-4" />
                                Guest page
                            </a>
                        </Button>
                    </div>
                )}
            </header>

            {/* Stats */}
            <section className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="sm:col-span-2 rounded-2xl border border-border bg-card p-5 space-y-3">
                    <div className="flex items-baseline justify-between">
                        <p className="text-sm font-medium text-muted-foreground">Storage</p>
                        <p className="text-sm">
                            <span className="font-bold">{formatBytes(storage?.used_bytes ?? 0)}</span>
                            {event.storage_capacity_gb ? ` of ${event.storage_capacity_gb} GB` : " used"}
                        </p>
                    </div>
                    <div className="h-2.5 w-full rounded-full bg-muted overflow-hidden">
                        <div
                            className={`h-full rounded-full transition-[width] duration-500 ${
                                usedPct > 90 ? "bg-red-500" : usedPct > 75 ? "bg-amber-500" : "bg-indigo-600"
                            }`}
                            style={{ width: `${usedPct}%` }}
                        />
                    </div>
                </div>
                <Stat label="Photos" value={progress ? `${progress.processed}/${progress.total}` : "—"} sub={processing > 0 ? `${processing} processing…` : undefined} />
                {faceScan ? (
                    <Stat label="Guests" value={String(storage?.guest_count ?? "—")} sub={progress ? `${progress.total_faces} faces indexed` : undefined} />
                ) : (
                    <Stat label="Plan" value={plan.short} sub="No face scan" />
                )}
            </section>

            {/* Something is wrong with progress: say so instead of leaving stale numbers */}
            {(statusError || (hasWork && stalled)) && (
                <p
                    role="status"
                    className="flex items-start gap-2 rounded-lg border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-sm text-amber-800 dark:text-amber-300"
                >
                    <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                    <span>
                        {statusError
                            ? `Can't reach the server to check progress (${statusError}). ${hasWork ? "Retrying…" : "Reload the page to try again."}`
                            : "Nothing has finished for over a minute, so the server may be busy or stuck. Check the backend terminal for [worker] lines."}
                    </span>
                </p>
            )}

            {/* Tabs */}
            <div className="space-y-6">
                <div className="flex gap-1 border-b border-border" role="tablist">
                    {(faceScan ? (["photos", "guests"] as const) : (["photos"] as const)).map((t) => (
                        <button
                            key={t}
                            role="tab"
                            aria-selected={tab === t}
                            onClick={() => setTab(t)}
                            className={`px-4 py-2.5 text-sm font-semibold capitalize -mb-px border-b-2 transition-colors ${
                                tab === t
                                    ? "border-indigo-600 text-indigo-600 dark:text-indigo-400"
                                    : "border-transparent text-muted-foreground hover:text-foreground"
                            }`}
                        >
                            {t}
                            <span className="ml-2 text-xs rounded-full bg-muted px-2 py-0.5">
                                {t === "photos" ? photos.length : guests.length}
                            </span>
                        </button>
                    ))}
                </div>

                {tab === "photos" || !faceScan ? (
                    <div className="space-y-6">
                        <UploadPanel eventId={id} remainingBytes={remaining} onUploaded={refreshAfterChange} />
                        <PhotoGrid eventId={id} photos={photos} loading={loadingPhotos} onChanged={refreshAfterChange} />
                    </div>
                ) : (
                    <GuestsTable guests={guests} loading={loadingGuests} onChanged={loadGuests} />
                )}
            </div>

            {/* Danger zone */}
            <section className="rounded-2xl border border-red-200 dark:border-red-900/50 p-5 flex flex-wrap items-center justify-between gap-4">
                <div>
                    <h2 className="font-bold text-red-600 dark:text-red-400">Delete event</h2>
                    <p className="text-sm text-muted-foreground">Removes all photos, guest selfies and face data. This can&apos;t be undone.</p>
                </div>
                <Button variant="destructive" onClick={deleteEvent} disabled={deleting}>
                    {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                    Delete event
                </Button>
            </section>
        </div>
    );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
    return (
        <div className="rounded-2xl border border-border bg-card p-5">
            <p className="text-sm font-medium text-muted-foreground">{label}</p>
            <p className="text-3xl font-bold mt-1">{value}</p>
            {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
        </div>
    );
}
