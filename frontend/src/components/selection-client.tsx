"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, ChevronLeft, ChevronRight, Heart, ImageIcon, Loader2, Lock, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { API_URL } from "@/lib/api";

// What a client sees: a link + code from their photographer, then a gallery where they pick photos.
// They only ever get thumbnails and a mid-size preview, never the original files.

interface PickPhoto {
    id: string;
    filename: string | null;
    width: number | null;
    height: number | null;
    thumbnail_url: string | null;
    preview_url: string | null;
    selected: boolean;
}

interface PhotoPage {
    photos: PickPhoto[];
    total: number;
    page: number;
    has_more: boolean;
    selected_total: number;
    status: string;
}

interface LinkInfo {
    client_name: string;
    event_name: string;
    status: string;
}

class CallError extends Error {
    constructor(public status: number, message: string) {
        super(message);
    }
}

const SAVE_DELAY_MS = 600; // taps are saved together instead of one request each

async function call<T>(token: string, path: string, session: string | null, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (session) headers.set("Authorization", `Bearer ${session}`);
    if (init.body) headers.set("Content-Type", "application/json");
    let res: Response;
    try {
        res = await fetch(`${API_URL}/select/${token}${path}`, { ...init, headers });
    } catch {
        throw new CallError(0, "Can't reach the server. Check your connection and try again.");
    }
    const body = await res.json().catch(() => null);
    if (!res.ok) {
        throw new CallError(res.status, typeof body?.detail === "string" ? body.detail : `Something went wrong (${res.status})`);
    }
    return body as T;
}

const sessionKey = (token: string) => `pixello-select-${token}`;
const readSession = (token: string) => {
    try {
        return sessionStorage.getItem(sessionKey(token));
    } catch {
        return null;
    }
};
const writeSession = (token: string, value: string | null) => {
    try {
        if (value) sessionStorage.setItem(sessionKey(token), value);
        else sessionStorage.removeItem(sessionKey(token));
    } catch {}
};

const plural = (n: number) => `${n} photo${n === 1 ? "" : "s"}`;

// ---------------------------------------------------------------------------

export default function SelectionClient() {
    const { token } = useParams<{ token: string }>();
    const [phase, setPhase] = useState<"loading" | "invalid" | "code" | "gallery">("loading");
    const [info, setInfo] = useState<LinkInfo | null>(null);
    const [session, setSession] = useState<string | null>(null);
    const [note, setNote] = useState<string | null>(null);

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const i = await call<LinkInfo>(token, "", null);
                if (!alive) return;
                setInfo(i);
                const saved = readSession(token);
                setSession(saved);
                setPhase(saved ? "gallery" : "code");
            } catch {
                if (alive) setPhase("invalid");
            }
        })();
        return () => {
            alive = false;
        };
    }, [token]);

    const signedOut = useCallback(() => {
        writeSession(token, null);
        setSession(null);
        setNote("Please enter your code again.");
        setPhase("code");
    }, [token]);

    if (phase === "loading") {
        return (
            <div className="flex min-h-screen items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-indigo-600" />
            </div>
        );
    }

    if (phase === "invalid" || !info) {
        return (
            <Centered>
                <ImageIcon className="w-10 h-10 mx-auto text-muted-foreground" />
                <h1 className="text-xl font-bold">This link isn&apos;t available</h1>
                <p className="text-sm text-muted-foreground">
                    Check that you copied the whole link, or ask your photographer to send it again.
                </p>
            </Centered>
        );
    }

    if (phase === "code" || !session) {
        return (
            <CodeScreen
                token={token}
                info={info}
                note={note}
                onVerified={(s, fresh) => {
                    writeSession(token, s);
                    setSession(s);
                    setInfo(fresh);
                    setPhase("gallery");
                }}
            />
        );
    }

    return <Gallery token={token} session={session} info={info} onSignedOut={signedOut} />;
}

function Centered({ children }: { children: React.ReactNode }) {
    return (
        <div className="flex min-h-screen items-center justify-center px-4">
            <div className="w-full max-w-sm space-y-4 text-center">{children}</div>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Code screen
// ---------------------------------------------------------------------------

function CodeScreen({
    token,
    info,
    note,
    onVerified,
}: {
    token: string;
    info: LinkInfo;
    note: string | null;
    onVerified: (session: string, info: LinkInfo) => void;
}) {
    const [code, setCode] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(note);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!code.trim()) return;
        setBusy(true);
        setError(null);
        try {
            const res = await call<LinkInfo & { session: string }>(token, "/verify", null, {
                method: "POST",
                body: JSON.stringify({ code }),
            });
            onVerified(res.session, res);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Couldn't check the code");
        } finally {
            setBusy(false);
        }
    };

    return (
        <Centered>
            <div className="w-14 h-14 rounded-full bg-indigo-100 dark:bg-indigo-900/40 flex items-center justify-center mx-auto">
                <Lock className="w-6 h-6 text-indigo-600" />
            </div>
            <div className="space-y-1">
                <h1 className="text-2xl font-bold tracking-tight">{info.event_name}</h1>
                <p className="text-sm text-muted-foreground">
                    Hi {info.client_name}! Enter the code your photographer sent you to choose your photos.
                </p>
            </div>
            <form onSubmit={submit} className="space-y-3">
                <Input
                    value={code}
                    onChange={(e) => setCode(e.target.value.toUpperCase())}
                    placeholder="Your code"
                    aria-label="Your code"
                    autoFocus
                    autoCapitalize="characters"
                    autoComplete="off"
                    spellCheck={false}
                    maxLength={12}
                    className="h-12 text-center text-xl font-mono tracking-[0.3em]"
                />
                {error && (
                    <p role="alert" className="text-sm text-destructive">
                        {error}
                    </p>
                )}
                <Button
                    type="submit"
                    disabled={busy || !code.trim()}
                    className="w-full h-11 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold"
                >
                    {busy && <Loader2 className="w-4 h-4 animate-spin" />}
                    See my photos
                </Button>
            </form>
        </Centered>
    );
}

// ---------------------------------------------------------------------------
// Gallery
// ---------------------------------------------------------------------------

function Gallery({
    token,
    session,
    info,
    onSignedOut,
}: {
    token: string;
    session: string;
    info: LinkInfo;
    onSignedOut: () => void;
}) {
    const [photos, setPhotos] = useState<PickPhoto[]>([]);
    const [page, setPage] = useState(0); // last page loaded
    const [hasMore, setHasMore] = useState(true);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [onlyPicked, setOnlyPicked] = useState(false);
    const [picked, setPicked] = useState<Set<string>>(new Set());
    const [total, setTotal] = useState<number | null>(null); // photos in the current view
    const [pickedTotal, setPickedTotal] = useState(0);
    const [submitted, setSubmitted] = useState(info.status === "submitted");
    const [submitting, setSubmitting] = useState(false);
    const [open, setOpen] = useState<number | null>(null); // index of the photo in the large viewer

    const loadingRef = useRef(false);
    const viewGen = useRef(0); // bumped when the view is reset, so a late answer for the old view is ignored
    const sentinel = useRef<HTMLDivElement>(null);
    const pendingAdd = useRef(new Set<string>());
    const pendingRemove = useRef(new Set<string>());
    const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

    const failed = useCallback(
        (err: unknown, fallback: string) => {
            if (err instanceof CallError && err.status === 401) {
                onSignedOut();
                return;
            }
            if (err instanceof CallError && err.status === 409) setSubmitted(true);
            toast.error(err instanceof Error ? err.message : fallback);
        },
        [onSignedOut]
    );

    const loadPage = useCallback(
        async (pageNo: number, picksOnly: boolean) => {
            if (loadingRef.current) return;
            loadingRef.current = true;
            const gen = viewGen.current;
            setLoading(true);
            setError(null);
            try {
                const d = await call<PhotoPage>(token, `/photos?page=${pageNo}${picksOnly ? "&only_selected=true" : ""}`, session);
                if (gen !== viewGen.current) return;
                setPhotos((prev) => {
                    if (pageNo === 1) return d.photos;
                    const seen = new Set(prev.map((p) => p.id));
                    return [...prev, ...d.photos.filter((p) => !seen.has(p.id))];
                });
                setPicked((prev) => {
                    const next = new Set(prev);
                    for (const p of d.photos) if (p.selected) next.add(p.id);
                    return next;
                });
                setHasMore(d.has_more);
                setPage(pageNo);
                setTotal(d.total);
                setPickedTotal(d.selected_total);
                if (d.status === "submitted") setSubmitted(true);
            } catch (err) {
                if (gen !== viewGen.current) return;
                if (err instanceof CallError && err.status === 401) onSignedOut();
                else setError(err instanceof Error ? err.message : "Couldn't load photos");
            } finally {
                if (gen === viewGen.current) {
                    loadingRef.current = false;
                    setLoading(false);
                }
            }
        },
        [token, session, onSignedOut]
    );

    // First page (started a tick after mounting, so this effect itself doesn't set state)
    useEffect(() => {
        queueMicrotask(() => void loadPage(1, false));
    }, [loadPage]);

    // Next page when the end of the grid comes near
    useEffect(() => {
        const el = sentinel.current;
        if (!el || page === 0) return;
        const observer = new IntersectionObserver(
            (entries) => {
                if (entries[0].isIntersecting && hasMore && !loadingRef.current) void loadPage(page + 1, onlyPicked);
            },
            { rootMargin: "900px" }
        );
        observer.observe(el);
        return () => observer.disconnect();
    }, [page, hasMore, onlyPicked, loadPage]);

    // Save the taps made so far
    const flush = useCallback(
        async (keepalive = false) => {
            clearTimeout(timer.current);
            const add = [...pendingAdd.current];
            const remove = [...pendingRemove.current];
            if (add.length === 0 && remove.length === 0) return;
            pendingAdd.current.clear();
            pendingRemove.current.clear();
            try {
                const d = await call<{ selected_total: number }>(token, "/selection", session, {
                    method: "POST",
                    body: JSON.stringify({ add, remove }),
                    keepalive,
                });
                setPickedTotal(d.selected_total + pendingAdd.current.size - pendingRemove.current.size);
            } catch (err) {
                // Put the photos back the way they were, so the screen never claims something that wasn't saved
                setPicked((prev) => {
                    const next = new Set(prev);
                    add.forEach((id) => next.delete(id));
                    remove.forEach((id) => next.add(id));
                    return next;
                });
                setPickedTotal((t) => t - add.length + remove.length);
                failed(err, "Couldn't save your choice");
            }
        },
        [token, session, failed]
    );

    // Closing the tab or switching apps: send what's waiting right now
    useEffect(() => {
        const save = () => void flush(true);
        const onHide = () => document.visibilityState === "hidden" && save();
        window.addEventListener("pagehide", save);
        document.addEventListener("visibilitychange", onHide);
        return () => {
            window.removeEventListener("pagehide", save);
            document.removeEventListener("visibilitychange", onHide);
        };
    }, [flush]);

    const toggle = (id: string) => {
        if (submitted) return;
        const wasPicked = picked.has(id);
        setPicked((prev) => {
            const next = new Set(prev);
            if (wasPicked) next.delete(id);
            else next.add(id);
            return next;
        });
        setPickedTotal((t) => t + (wasPicked ? -1 : 1));
        if (wasPicked) {
            if (!pendingAdd.current.delete(id)) pendingRemove.current.add(id);
        } else if (!pendingRemove.current.delete(id)) {
            pendingAdd.current.add(id);
        }
        clearTimeout(timer.current);
        timer.current = setTimeout(() => void flush(), SAVE_DELAY_MS);
    };

    const showPicked = async (value: boolean) => {
        await flush();
        viewGen.current += 1; // anything still loading for the old view is dropped
        loadingRef.current = false;
        setOnlyPicked(value);
        setPhotos([]);
        setPage(0);
        setHasMore(true);
        setTotal(null);
        setOpen(null);
        await loadPage(1, value);
    };

    const submit = async () => {
        if (pickedTotal === 0) {
            toast.error("Pick at least one photo first");
            return;
        }
        if (!confirm(`Send ${plural(pickedTotal)} to your photographer? You won't be able to change your choice afterwards.`)) return;
        setSubmitting(true);
        try {
            await flush();
            await call(token, "/submit", session, { method: "POST" });
            setSubmitted(true);
            toast.success("Your choice has been sent to your photographer");
        } catch (err) {
            failed(err, "Couldn't send your choice");
        } finally {
            setSubmitting(false);
        }
    };

    const loadMore = () => {
        if (hasMore && !loadingRef.current) void loadPage(page + 1, onlyPicked);
    };

    return (
        <div className="min-h-screen bg-background">
            <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur">
                <div className="max-w-6xl mx-auto px-4 py-3 flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                        <h1 className="font-bold text-lg leading-tight truncate">{info.event_name}</h1>
                        <p className="text-xs text-muted-foreground truncate">Choosing for {info.client_name}</p>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button
                            variant={onlyPicked ? "default" : "outline"}
                            size="sm"
                            onClick={() => void showPicked(!onlyPicked)}
                            aria-pressed={onlyPicked}
                            className={onlyPicked ? "bg-indigo-600 hover:bg-indigo-700 text-white" : ""}
                        >
                            <Heart className={`w-4 h-4 ${onlyPicked ? "fill-current" : ""}`} />
                            {pickedTotal} picked
                        </Button>
                        {!submitted && (
                            <Button
                                size="sm"
                                onClick={submit}
                                disabled={submitting || pickedTotal === 0}
                                className="bg-indigo-600 hover:bg-indigo-700 text-white font-semibold"
                            >
                                {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
                                Send to photographer
                            </Button>
                        )}
                    </div>
                </div>
            </header>

            <main className="max-w-6xl mx-auto px-4 py-6 space-y-5">
                {submitted ? (
                    <div className="flex items-start gap-3 rounded-xl border border-green-200 dark:border-green-900/50 bg-green-50 dark:bg-green-950/30 p-4 text-green-800 dark:text-green-300">
                        <CheckCircle2 className="w-5 h-5 mt-0.5 shrink-0" />
                        <div className="text-sm">
                            <p className="font-semibold">All done: your choice has been sent.</p>
                            <p>Your photographer can see your {plural(pickedTotal)}. If you want to change something, ask them to reopen your selection.</p>
                        </div>
                    </div>
                ) : (
                    <p className="text-sm text-muted-foreground">
                        Tap a photo to see it bigger, and tap the heart to choose it. Your choices save automatically; press{" "}
                        <span className="font-medium text-foreground">Send to photographer</span> when you&apos;re finished.
                    </p>
                )}

                {error && (
                    <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                        {error}{" "}
                        <button className="underline font-medium" onClick={() => void loadPage(page + 1, onlyPicked)}>
                            Try again
                        </button>
                    </p>
                )}

                {total === 0 && !loading && !error ? (
                    <div className="text-center py-20 text-muted-foreground">
                        <ImageIcon className="w-12 h-12 mx-auto mb-3 opacity-40" />
                        <p className="font-medium">{onlyPicked ? "You haven't picked any photos yet" : "No photos are ready yet"}</p>
                        {!onlyPicked && <p className="text-sm">Please check back in a little while.</p>}
                    </div>
                ) : (
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2 sm:gap-3">
                        {photos.map((photo, i) => (
                            <Tile
                                key={photo.id}
                                photo={photo}
                                picked={picked.has(photo.id)}
                                locked={submitted}
                                onOpen={() => setOpen(i)}
                                onToggle={() => toggle(photo.id)}
                            />
                        ))}
                    </div>
                )}

                <div ref={sentinel} className="h-8 flex items-center justify-center">
                    {loading && <Loader2 className="w-5 h-5 animate-spin text-indigo-600" />}
                </div>
            </main>

            {open !== null && photos[open] && (
                <Viewer
                    photos={photos}
                    index={open}
                    picked={picked}
                    locked={submitted}
                    onIndex={setOpen}
                    onClose={() => setOpen(null)}
                    onToggle={toggle}
                    onNearEnd={loadMore}
                />
            )}
        </div>
    );
}

function Tile({
    photo,
    picked,
    locked,
    onOpen,
    onToggle,
}: {
    photo: PickPhoto;
    picked: boolean;
    locked: boolean;
    onOpen: () => void;
    onToggle: () => void;
}) {
    return (
        <div
            className={`relative aspect-square overflow-hidden rounded-xl bg-muted border-2 transition-colors ${
                picked ? "border-indigo-500" : "border-transparent"
            }`}
        >
            <button type="button" onClick={onOpen} className="absolute inset-0 w-full h-full" aria-label={`View ${photo.filename ?? "photo"}`}>
                {photo.thumbnail_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                        src={photo.thumbnail_url}
                        alt={photo.filename ?? ""}
                        loading="lazy"
                        decoding="async"
                        className="w-full h-full object-cover"
                    />
                )}
            </button>
            <HeartButton picked={picked} locked={locked} onClick={onToggle} className="absolute top-2 right-2" />
            {photo.filename && (
                <span className="pointer-events-none absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/60 to-transparent px-2 pb-1.5 pt-6 text-[10px] font-medium text-white truncate">
                    {photo.filename}
                </span>
            )}
        </div>
    );
}

function HeartButton({
    picked,
    locked,
    onClick,
    className = "",
    large = false,
}: {
    picked: boolean;
    locked: boolean;
    onClick: () => void;
    className?: string;
    large?: boolean;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={locked}
            aria-pressed={picked}
            aria-label={picked ? "Remove from my choice" : "Choose this photo"}
            className={`${className} flex items-center justify-center rounded-full shadow-md transition-transform active:scale-90 disabled:opacity-80 ${
                large ? "w-12 h-12" : "w-9 h-9"
            } ${picked ? "bg-indigo-600 text-white" : "bg-white/90 text-slate-700 hover:bg-white"}`}
        >
            <Heart className={`${large ? "w-6 h-6" : "w-[18px] h-[18px]"} ${picked ? "fill-current" : ""}`} />
        </button>
    );
}

// ---------------------------------------------------------------------------
// Large viewer
// ---------------------------------------------------------------------------

function Viewer({
    photos,
    index,
    picked,
    locked,
    onIndex,
    onClose,
    onToggle,
    onNearEnd,
}: {
    photos: PickPhoto[];
    index: number;
    picked: Set<string>;
    locked: boolean;
    onIndex: (i: number) => void;
    onClose: () => void;
    onToggle: (id: string) => void;
    onNearEnd: () => void;
}) {
    const photo = photos[index];
    const touchX = useRef<number | null>(null);

    const go = useCallback(
        (delta: number) => {
            const next = index + delta;
            if (next < 0 || next >= photos.length) return;
            onIndex(next);
            if (next >= photos.length - 3) onNearEnd();
        },
        [index, photos.length, onIndex, onNearEnd]
    );

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
            else if (e.key === "ArrowLeft") go(-1);
            else if (e.key === "ArrowRight") go(1);
            else if (e.key === " " || e.key.toLowerCase() === "h") {
                e.preventDefault();
                onToggle(photos[index].id);
            }
        };
        window.addEventListener("keydown", onKey);
        const previous = document.body.style.overflow;
        document.body.style.overflow = "hidden"; // the page behind doesn't scroll
        return () => {
            window.removeEventListener("keydown", onKey);
            document.body.style.overflow = previous;
        };
    }, [go, onClose, onToggle, photos, index]);

    // Load the neighbours now so flipping through is instant
    useEffect(() => {
        for (const i of [index - 1, index + 1]) {
            const url = photos[i]?.preview_url;
            if (url) new window.Image().src = url;
        }
    }, [index, photos]);

    return (
        <div
            className="fixed inset-0 z-50 flex flex-col bg-black/95"
            role="dialog"
            aria-modal="true"
            aria-label="Photo viewer"
            onTouchStart={(e) => (touchX.current = e.touches[0].clientX)}
            onTouchEnd={(e) => {
                if (touchX.current === null) return;
                const dx = e.changedTouches[0].clientX - touchX.current;
                touchX.current = null;
                if (Math.abs(dx) > 60) go(dx < 0 ? 1 : -1);
            }}
        >
            <div className="flex items-center justify-between px-4 py-3 text-white">
                <p className="text-sm truncate">
                    {photo.filename ?? "Photo"} <span className="text-white/60">· {index + 1} of {photos.length}</span>
                </p>
                <button onClick={onClose} aria-label="Close" className="p-2 -mr-2 rounded-full hover:bg-white/10">
                    <X className="w-6 h-6" />
                </button>
            </div>

            <div className="relative flex-1 min-h-0 flex items-center justify-center">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                    key={photo.id}
                    src={photo.preview_url ?? photo.thumbnail_url ?? ""}
                    alt={photo.filename ?? ""}
                    className="max-h-full max-w-full object-contain select-none"
                    draggable={false}
                />
                {index > 0 && (
                    <button
                        onClick={() => go(-1)}
                        aria-label="Previous photo"
                        className="hidden sm:flex absolute left-3 top-1/2 -translate-y-1/2 w-11 h-11 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
                    >
                        <ChevronLeft className="w-6 h-6" />
                    </button>
                )}
                {index < photos.length - 1 && (
                    <button
                        onClick={() => go(1)}
                        aria-label="Next photo"
                        className="hidden sm:flex absolute right-3 top-1/2 -translate-y-1/2 w-11 h-11 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
                    >
                        <ChevronRight className="w-6 h-6" />
                    </button>
                )}
            </div>

            <div className="flex justify-center py-4">
                <HeartButton large picked={picked.has(photo.id)} locked={locked} onClick={() => onToggle(photo.id)} />
            </div>
        </div>
    );
}
