"use client";

import React, { useState } from "react";
import { Check, ImageIcon, ImageOff, Loader2, RefreshCw, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

export interface Photo {
    id: string;
    original_file_name: string;
    faces_count: number;
    status: string;
    error_detail?: string | null;
    presigned_thumbnail_url?: string | null;
    presigned_original_url?: string | null;
}

export function PhotoGrid({
    eventId,
    photos,
    loading,
    onChanged,
}: {
    eventId: string;
    photos: Photo[];
    loading: boolean;
    onChanged: () => void;
}) {
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [deleting, setDeleting] = useState(false);
    const [retrying, setRetrying] = useState(false);

    // Failed outright, processed but the face scan failed, or stored without a preview
    const failedCount = photos.filter(
        (p) => p.status === "error" || (p.status === "processed" && (p.error_detail || !p.presigned_thumbnail_url))
    ).length;

    const retryFailed = async () => {
        setRetrying(true);
        try {
            const res = await api<{ retrying: number; needs_reupload: number; message: string }>(
                `/photos/retry/${eventId}`,
                { method: "POST" }
            );
            if (res.retrying > 0) toast.success(res.message);
            else toast.error(res.needs_reupload > 0 ? "These photos never reached storage — please upload them again" : "Nothing to retry");
            if (res.retrying > 0 && res.needs_reupload > 0) toast.info(`${res.needs_reupload} photo(s) must be re-uploaded`);
            onChanged();
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't retry photos");
        } finally {
            setRetrying(false);
        }
    };

    const toggle = (id: string) =>
        setSelected((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });

    const deleteSelected = async () => {
        if (!confirm(`Delete ${selected.size} photo(s)? This can't be undone.`)) return;
        setDeleting(true);
        try {
            const res = await api<{ message: string }>("/photos/delete/bulk", {
                method: "POST",
                body: JSON.stringify(Array.from(selected)),
            });
            toast.success(res.message);
            setSelected(new Set());
            onChanged();
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't delete photos");
        } finally {
            setDeleting(false);
        }
    };

    if (loading) {
        return (
            <div className="flex justify-center py-16">
                <Loader2 className="w-7 h-7 animate-spin text-indigo-600" />
            </div>
        );
    }

    if (photos.length === 0) {
        return (
            <div className="text-center py-16 text-muted-foreground">
                <ImageIcon className="w-12 h-12 mx-auto mb-3 opacity-40" />
                <p className="font-medium">No photos yet</p>
                <p className="text-sm">Upload some above and they&apos;ll show up here.</p>
            </div>
        );
    }

    const allSelected = selected.size === photos.length;

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                    <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={() => setSelected(allSelected ? new Set() : new Set(photos.map((p) => p.id)))}
                        className="w-4 h-4 accent-indigo-600"
                    />
                    {selected.size > 0 ? `${selected.size} selected` : `Select all (${photos.length})`}
                </label>
                <div className="flex items-center gap-2">
                    {failedCount > 0 && (
                        <Button variant="outline" size="sm" onClick={retryFailed} disabled={retrying}>
                            {retrying ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                            Retry {failedCount} failed
                        </Button>
                    )}
                    {selected.size > 0 && (
                        <Button variant="destructive" size="sm" onClick={deleteSelected} disabled={deleting}>
                            {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                            Delete selected
                        </Button>
                    )}
                </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
                {photos.map((photo) => (
                    <PhotoTile key={photo.id} photo={photo} selected={selected.has(photo.id)} onToggle={() => toggle(photo.id)} />
                ))}
            </div>
        </div>
    );
}

const IMAGE_RETRIES = 3;

function PhotoTile({ photo, selected, onToggle }: { photo: Photo; selected: boolean; onToggle: () => void }) {
    // A thumbnail can fail to load for a moment (busy network, many images at once),
    // so retry a few times before showing the placeholder. The count belongs to one URL,
    // so a fresh URL starts again from zero.
    const url = photo.presigned_thumbnail_url;
    const [failures, setFailures] = useState<{ url: string | null | undefined; count: number }>({ url, count: 0 });
    const attempt = failures.url === url ? failures.count : 0;
    const imgFailed = attempt >= IMAGE_RETRIES;
    const showImage = url && !imgFailed;

    const onImageError = () => {
        console.warn(
            `[picshare] preview for "${photo.original_file_name}" failed to load (try ${attempt + 1} of ${IMAGE_RETRIES}). ` +
                "Open the preview link in a new tab to see the error S3 returns."
        );
        setTimeout(
            () => setFailures((f) => ({ url, count: (f.url === url ? f.count : 0) + 1 })),
            1500 * (attempt + 1)
        );
    };

    // Stored and viewable, but the face scan didn't work out (the Retry button above offers it again)
    const scanFailed = photo.status === "processed" && !!photo.error_detail;

    // Processed, but there is nothing to show: no preview was made, or it won't load. Say so instead of
    // spinning forever (the spinner is only for photos that are really still being processed).
    const previewUnavailable = !showImage && photo.status === "processed";
    const placeholderTitle =
        photo.status === "error"
            ? photo.error_detail ?? "Processing failed"
            : previewUnavailable
              ? photo.error_detail ?? (url ? "The preview couldn't be loaded" : "No preview was made for this photo")
              : undefined;

    return (
        <div
            title={scanFailed ? photo.error_detail ?? undefined : placeholderTitle}
            className={`group relative aspect-square rounded-xl overflow-hidden border-2 bg-muted transition-all ${
                selected ? "border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-900" : "border-transparent"
            }`}
        >
            {showImage ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                    key={attempt}
                    src={photo.presigned_thumbnail_url!}
                    alt={photo.original_file_name}
                    loading="lazy"
                    className="w-full h-full object-cover"
                    onError={onImageError}
                />
            ) : (
                <div className="w-full h-full flex flex-col items-center justify-center gap-1 text-xs text-muted-foreground">
                    {photo.status === "error" ? (
                        <>
                            <X className="w-5 h-5 text-red-500" />
                            <span className="text-red-500 font-medium" title={photo.error_detail ?? undefined}>
                                Failed
                            </span>
                        </>
                    ) : previewUnavailable ? (
                        <>
                            <ImageOff className="w-5 h-5 text-amber-500" />
                            <span className="text-amber-600 dark:text-amber-400 font-medium">Preview unavailable</span>
                        </>
                    ) : (
                        <>
                            <Loader2 className="w-5 h-5 animate-spin" />
                            Processing…
                        </>
                    )}
                </div>
            )}

            <button
                type="button"
                onClick={onToggle}
                aria-label={selected ? "Deselect photo" : "Select photo"}
                className="absolute inset-0 w-full h-full"
            />

            <span
                className={`pointer-events-none absolute top-2 left-2 w-5 h-5 rounded-md border-2 flex items-center justify-center transition-opacity ${
                    selected
                        ? "bg-indigo-600 border-indigo-600 opacity-100"
                        : "bg-black/30 border-white opacity-0 group-hover:opacity-100"
                }`}
            >
                {selected && <Check className="w-3.5 h-3.5 text-white" />}
            </span>

            {scanFailed && (
                <span className="pointer-events-none absolute bottom-2 left-2 rounded-full bg-amber-500/90 text-white text-[10px] font-medium px-2 py-0.5">
                    Face scan failed
                </span>
            )}

            {photo.faces_count > 0 && (
                <span className="pointer-events-none absolute bottom-2 left-2 rounded-full bg-black/60 text-white text-[10px] font-medium px-2 py-0.5">
                    {photo.faces_count} face{photo.faces_count === 1 ? "" : "s"}
                </span>
            )}

            {photo.presigned_original_url && (
                <a
                    href={photo.presigned_original_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="absolute bottom-2 right-2 rounded-full bg-white/90 text-black text-[10px] font-semibold px-2 py-0.5 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                    View
                </a>
            )}
        </div>
    );
}
