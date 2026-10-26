"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronUp, Copy, Download, FileText, KeyRound, Loader2, Plus, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { API_URL, api, getToken } from "@/lib/api";

// The photographer's side of photo selection: one link + code per client, and what each client picked.
// A pick is just a note of which photos the client chose, so nothing is copied or stored twice.

interface SelectionLink {
    id: string;
    client_name: string;
    token: string;
    code: string;
    path: string;
    status: "open" | "submitted";
    submitted_at: string | null;
    created_at: string;
    selected_count: number;
}

interface PickedPhoto {
    id: string;
    filename: string | null;
    thumbnail_url: string | null;
}

const fullUrl = (link: SelectionLink) => `${window.location.origin}${link.path}`;

export function SelectionPanel({ eventId, eventName }: { eventId: string; eventName: string }) {
    const [links, setLinks] = useState<SelectionLink[] | null>(null);
    const [name, setName] = useState("");
    const [creating, setCreating] = useState(false);

    const load = useCallback(async () => {
        try {
            const data = await api<{ links: SelectionLink[] }>(`/selection/links?event_id=${eventId}`);
            setLinks(data.links);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't load selection links");
            setLinks((prev) => prev ?? []);
        }
    }, [eventId]);

    useEffect(() => {
        queueMicrotask(() => void load());
        // Clients pick while this page is open in another tab: catch up when coming back to it
        const onVisible = () => document.visibilityState === "visible" && void load();
        document.addEventListener("visibilitychange", onVisible);
        return () => document.removeEventListener("visibilitychange", onVisible);
    }, [load]);

    const create = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!name.trim()) return;
        setCreating(true);
        try {
            await api(`/selection/links`, {
                method: "POST",
                body: JSON.stringify({ event_id: eventId, client_name: name.trim() }),
            });
            setName("");
            await load();
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't create the link");
        } finally {
            setCreating(false);
        }
    };

    return (
        <div className="space-y-5">
            <form onSubmit={create} className="rounded-2xl border border-border bg-card p-5 space-y-3">
                <div>
                    <h2 className="font-bold">Share photos with a client</h2>
                    <p className="text-sm text-muted-foreground">
                        Make a link and code for each client. They open it, choose their photos, and you see their picks here.
                        They can look and choose, but not download.
                    </p>
                </div>
                <div className="flex flex-col sm:flex-row gap-2">
                    <Input
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="Client name, e.g. Asha & Raj"
                        aria-label="Client name"
                        maxLength={100}
                        className="h-11"
                    />
                    <Button type="submit" disabled={creating || !name.trim()} className="h-11 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold">
                        {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                        Create link
                    </Button>
                </div>
            </form>

            {links === null ? (
                <div className="flex justify-center py-10">
                    <Loader2 className="w-6 h-6 animate-spin text-indigo-600" />
                </div>
            ) : links.length === 0 ? (
                <p className="text-center text-sm text-muted-foreground py-8">No links yet. Create one above and send it to your client.</p>
            ) : (
                links.map((link) => <LinkCard key={link.id} link={link} eventName={eventName} onChanged={load} />)
            )}
        </div>
    );
}

function LinkCard({ link, eventName, onChanged }: { link: SelectionLink; eventName: string; onChanged: () => Promise<void> }) {
    const [copied, setCopied] = useState<"message" | "link" | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [showPicks, setShowPicks] = useState(false);

    const copy = async (what: "message" | "link") => {
        const text =
            what === "link"
                ? fullUrl(link)
                : `Hi ${link.client_name}! Please choose your photos from ${eventName} here:\n${fullUrl(link)}\nYour code: ${link.code}`;
        try {
            await navigator.clipboard.writeText(text);
            setCopied(what);
            setTimeout(() => setCopied(null), 2000);
        } catch {
            toast.error("Couldn't copy. Select the text and copy it by hand.");
        }
    };

    const run = async (label: string, work: () => Promise<void>) => {
        setBusy(label);
        try {
            await work();
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Something went wrong");
        } finally {
            setBusy(null);
        }
    };

    const patch = (body: object) => api(`/selection/links/${link.id}`, { method: "PATCH", body: JSON.stringify(body) });

    const downloadZip = () =>
        run("zip", async () => {
            // The download carries its own short-lived pass, so the browser can stream it straight to disk
            const { url } = await api<{ url: string }>(`/selection/links/${link.id}/download-url`, { method: "POST" });
            window.location.href = `${API_URL}${url}`;
        });

    const downloadNames = () =>
        run("names", async () => {
            const res = await fetch(`${API_URL}/selection/links/${link.id}/filenames`, {
                headers: { Authorization: `Bearer ${getToken() ?? ""}` },
            });
            if (!res.ok) throw new Error("Couldn't get the file names");
            const blob = await res.blob();
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `${link.client_name.replace(/[^A-Za-z0-9._-]+/g, "-")}-selection.txt`;
            a.click();
            URL.revokeObjectURL(a.href);
        });

    const reopen = () =>
        run("reopen", async () => {
            await patch({ reopen: true });
            toast.success("Reopened: the client can change their choice again");
            await onChanged();
        });

    const newCode = () => {
        if (!confirm("Make a new code? Anyone using the old code is signed out, and you'll need to send the new one.")) return;
        return run("code", async () => {
            await patch({ new_code: true });
            await onChanged();
        });
    };

    const remove = () => {
        if (!confirm(`Delete the link for ${link.client_name}? Their picks are removed too. Your photos are not touched.`)) return;
        return run("delete", async () => {
            await api(`/selection/links/${link.id}`, { method: "DELETE" });
            await onChanged();
        });
    };

    const submitted = link.status === "submitted";

    return (
        <div className="rounded-2xl border border-border bg-card p-5 space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                    <h3 className="font-bold text-lg">{link.client_name}</h3>
                    <p className="text-sm text-muted-foreground">
                        {link.selected_count} photo{link.selected_count === 1 ? "" : "s"} picked
                        {submitted && link.submitted_at && ` · sent ${new Date(link.submitted_at + "Z").toLocaleString()}`}
                    </p>
                </div>
                <span
                    className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${
                        submitted
                            ? "bg-green-100 dark:bg-green-950/40 text-green-700 dark:text-green-300"
                            : "bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300"
                    }`}
                >
                    {submitted ? "Submitted" : "Choosing"}
                </span>
            </div>

            <div className="grid gap-2 sm:grid-cols-[1fr_auto] items-center rounded-xl bg-muted/50 p-3 text-sm">
                <p className="truncate">
                    <span className="text-muted-foreground">Link: </span>
                    <span className="font-mono">{link.path}</span>
                    <span className="mx-2 text-muted-foreground">Code:</span>
                    <span className="font-mono font-bold tracking-widest">{link.code}</span>
                </p>
                <div className="flex gap-2">
                    <Button size="sm" onClick={() => copy("message")} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                        {copied === "message" ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                        Copy message
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => copy("link")}>
                        {copied === "link" ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4" />}
                        Link only
                    </Button>
                </div>
            </div>

            <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => setShowPicks((v) => !v)} disabled={link.selected_count === 0}>
                    {showPicks ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    {showPicks ? "Hide picks" : "View picks"}
                </Button>
                <Button size="sm" variant="outline" onClick={downloadZip} disabled={link.selected_count === 0 || busy !== null}>
                    {busy === "zip" ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                    Download ZIP
                </Button>
                <Button size="sm" variant="outline" onClick={downloadNames} disabled={link.selected_count === 0 || busy !== null}>
                    {busy === "names" ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
                    File names
                </Button>
                {submitted && (
                    <Button size="sm" variant="outline" onClick={reopen} disabled={busy !== null}>
                        {busy === "reopen" ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
                        Reopen
                    </Button>
                )}
                <Button size="sm" variant="ghost" onClick={newCode} disabled={busy !== null}>
                    <KeyRound className="w-4 h-4" />
                    New code
                </Button>
                <Button size="sm" variant="ghost" onClick={remove} disabled={busy !== null} className="text-destructive hover:text-destructive">
                    <Trash2 className="w-4 h-4" />
                    Delete
                </Button>
            </div>

            {showPicks && <PicksGrid linkId={link.id} total={link.selected_count} />}
        </div>
    );
}

function PicksGrid({ linkId, total }: { linkId: string; total: number }) {
    const [photos, setPhotos] = useState<PickedPhoto[]>([]);
    const [page, setPage] = useState(0);
    const [hasMore, setHasMore] = useState(true);
    const [loading, setLoading] = useState(false);
    const busy = useRef(false); // one load at a time: two at once would add the same page twice

    const loadMore = useCallback(async () => {
        if (busy.current) return;
        busy.current = true;
        setLoading(true);
        try {
            const d = await api<{ photos: PickedPhoto[]; has_more: boolean }>(`/selection/links/${linkId}/photos?page=${page + 1}`);
            setPhotos((prev) => {
                const seen = new Set(prev.map((p) => p.id));
                return [...prev, ...d.photos.filter((p) => !seen.has(p.id))];
            });
            setHasMore(d.has_more);
            setPage((p) => p + 1);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't load the picks");
        } finally {
            busy.current = false;
            setLoading(false);
        }
    }, [linkId, page]);

    // Opening the grid loads the first page
    useEffect(() => {
        queueMicrotask(() => void loadMore());
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return (
        <div className="space-y-3">
            <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
                {photos.map((p) => (
                    <div key={p.id} className="relative aspect-square overflow-hidden rounded-lg bg-muted">
                        {p.thumbnail_url && (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img src={p.thumbnail_url} alt={p.filename ?? ""} loading="lazy" decoding="async" className="w-full h-full object-cover" />
                        )}
                        {p.filename && (
                            <span className="absolute bottom-0 inset-x-0 bg-black/60 px-1.5 py-0.5 text-[10px] text-white truncate">{p.filename}</span>
                        )}
                    </div>
                ))}
            </div>
            {loading && (
                <div className="flex justify-center">
                    <Loader2 className="w-5 h-5 animate-spin text-indigo-600" />
                </div>
            )}
            {hasMore && !loading && photos.length < total && (
                <div className="text-center">
                    <Button size="sm" variant="outline" onClick={() => void loadMore()}>
                        Show more
                    </Button>
                </div>
            )}
        </div>
    );
}
