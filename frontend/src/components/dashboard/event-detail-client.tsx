"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, CalendarDays, Check, Copy, ExternalLink, Loader2, Lock, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, formatBytes, formatDateRange, type EventItem } from "@/lib/api";
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
    processed: number;
    errors: number;
    total_faces: number;
}

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

    const loadPhotos = useCallback(async () => {
        try {
            const data = await api<{ photos: Photo[] }>(`/photos/event/${id}/gallery?limit=1000`);
            setPhotos(data.photos);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't load photos");
        } finally {
            setLoadingPhotos(false);
        }
    }, [id]);

    const loadStats = useCallback(async () => {
        try {
            const [s, p] = await Promise.all([
                api<Storage>(`/events/${id}/storage`),
                api<Progress>(`/photos/status/${id}`),
            ]);
            setStorage(s);
            setProgress(p);
            return p;
        } catch {
            return null;
        }
    }, [id]);

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
                loadStats();
                loadPhotos();
                loadGuests();
            })
            .catch((err) => {
                toast.error(err.message);
                router.replace("/dashboard");
            });
    }, [user, id, router, loadStats, loadPhotos, loadGuests]);

    // While photos are still being processed, poll so thumbnails/counts catch up
    const pending = progress?.pending ?? 0;
    useEffect(() => {
        if (!event || pending === 0) return;
        const timer = setInterval(async () => {
            const p = await loadStats();
            await loadPhotos();
            if (p && p.pending === 0) clearInterval(timer);
        }, 4000);
        return () => clearInterval(timer);
    }, [event, pending, loadStats, loadPhotos]);

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
                        {event.secret_code && (
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
                <Stat label="Photos" value={progress ? `${progress.processed}/${progress.total}` : "—"} sub={pending > 0 ? `${pending} processing…` : undefined} />
                <Stat label="Guests" value={String(storage?.guest_count ?? "—")} sub={progress ? `${progress.total_faces} faces indexed` : undefined} />
            </section>

            {/* Tabs */}
            <div className="space-y-6">
                <div className="flex gap-1 border-b border-border" role="tablist">
                    {(["photos", "guests"] as const).map((t) => (
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

                {tab === "photos" ? (
                    <div className="space-y-6">
                        <UploadPanel eventId={id} remainingBytes={remaining} onUploaded={refreshAfterChange} />
                        <PhotoGrid photos={photos} loading={loadingPhotos} onChanged={refreshAfterChange} />
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
