"use client";

import React, { useEffect, useRef, useState } from "react";
import { ImageIcon, Loader2, UploadCloud, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, formatBytes, uploadToS3 } from "@/lib/api";

const PARALLEL_UPLOADS = 6; // photos sent to S3 at once
const COMPLETE_BATCH = 25; // tell the server about finished uploads in groups
const SIGN_BATCH = 50; // upload links requested per call, just before they're needed
const PAINT_EVERY_MS = 250; // how often the progress bar is redrawn
const ALLOWED_TYPES = ["image/jpeg", "image/png"];

const reason = (err: unknown) => (err instanceof Error ? err.message : String(err));

interface SignedUpload {
    photo_id: string;
    url: string;
    fields: Record<string, string>;
}

export function UploadPanel({
    eventId,
    remainingBytes,
    onUploaded,
}: {
    eventId: string;
    /** null = unlimited */
    remainingBytes: number | null;
    onUploaded: () => void;
}) {
    const [files, setFiles] = useState<File[]>([]);
    const [dragOver, setDragOver] = useState(false);
    const [progress, setProgress] = useState<number | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
    const overLimit = remainingBytes !== null && totalBytes > remainingBytes;
    const uploading = progress !== null;

    // Closing the tab stops the upload, so ask before letting that happen mid-batch
    useEffect(() => {
        if (!uploading) return;
        const warn = (e: BeforeUnloadEvent) => {
            e.preventDefault();
            e.returnValue = "";
        };
        window.addEventListener("beforeunload", warn);
        return () => window.removeEventListener("beforeunload", warn);
    }, [uploading]);

    const addFiles = (list: FileList | File[]) => {
        const images = Array.from(list).filter((f) => ALLOWED_TYPES.includes(f.type));
        if (images.length < Array.from(list).length) toast.error("Only JPG or PNG images can be uploaded");
        // De-dupe by name+size so re-dropping the same batch doesn't double up
        setFiles((prev) => {
            const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
            return [...prev, ...images.filter((f) => !seen.has(`${f.name}:${f.size}`))];
        });
    };

    const upload = async () => {
        setProgress(0);
        const failed: File[] = [];
        let succeeded = 0;
        let signError: unknown = null;
        try {
            // 1. Signed S3 links are requested a group at a time, just before they're needed. That lets
            //    the first photo start right away, keeps the links fresh (they expire after an hour)
            //    and has no limit on how many photos can be selected.
            const signedGroups = new Map<number, Promise<SignedUpload[]>>();
            const signGroup = (group: number) => {
                let promise = signedGroups.get(group);
                if (!promise) {
                    const slice = files.slice(group * SIGN_BATCH, (group + 1) * SIGN_BATCH);
                    promise = api<{ uploads: SignedUpload[] }>(`/photos/upload-urls?event_id=${eventId}`, {
                        method: "POST",
                        body: JSON.stringify({
                            files: slice.map((f) => ({ name: f.name, size: f.size, content_type: f.type })),
                        }),
                    }).then((res) => res.uploads);
                    signedGroups.set(group, promise);
                }
                return promise;
            };
            const signedUpload = async (i: number) => {
                const group = Math.floor(i / SIGN_BATCH);
                const position = i - group * SIGN_BATCH;
                // Start on the next group once this one is half used, so uploads never wait on signing
                if (position >= SIGN_BATCH / 2 && (group + 1) * SIGN_BATCH < files.length) {
                    signGroup(group + 1).catch(() => {}); // a failure is reported when that group is needed
                }
                return (await signGroup(group))[position];
            };

            // 2. Send photos straight to S3, a few at a time
            const sent = new Array(files.length).fill(0);
            let lastPaint = 0;
            const report = (force = false) => {
                const now = performance.now();
                if (!force && now - lastPaint < PAINT_EVERY_MS) return; // progress events are far more frequent than needed
                lastPaint = now;
                setProgress(sent.reduce((a, b) => a + b, 0) / Math.max(totalBytes, 1));
            };
            let flushing: Promise<void> = Promise.resolve();
            const doneIds: string[] = [];
            const failedIds: string[] = [];

            // 3. Tell the server which uploads finished. Photos are only processed once the last call
            //    (final) arrives, so processing doesn't compete with the upload for CPU and network.
            const flush = (force = false, final = false) => {
                if (!final && !force && doneIds.length < COMPLETE_BATCH) return;
                const body = { uploaded: doneIds.splice(0), failed: failedIds.splice(0), final };
                if (!final && body.uploaded.length === 0 && body.failed.length === 0) return;
                flushing = flushing.then(() =>
                    api(`/photos/upload-complete?event_id=${eventId}`, { method: "POST", body: JSON.stringify(body) })
                        .then(() => undefined)
                        .catch((err) => {
                            console.warn(`[picshare] couldn't tell the server the upload finished: ${reason(err)}`);
                            toast.error(err instanceof Error ? err.message : "Couldn't confirm the upload");
                        })
                );
            };

            let next = 0;
            const worker = async () => {
                while (next < files.length) {
                    const i = next++;
                    let signed: SignedUpload;
                    try {
                        signed = await signedUpload(i);
                    } catch (err) {
                        if (!signError) signError = err;
                        console.warn(`[picshare] couldn't get an upload link for "${files[i].name}": ${reason(err)}`);
                        failed.push(files[i]);
                        continue;
                    }
                    const { url, fields, photo_id } = signed;
                    let ok = false;
                    for (let attempt = 0; attempt < 2 && !ok; attempt++) {
                        try {
                            await uploadToS3(url, fields, files[i], (loaded) => {
                                sent[i] = loaded;
                                report();
                            });
                            ok = true;
                        } catch (err) {
                            sent[i] = 0;
                            console.warn(`[picshare] upload of "${files[i].name}" failed (try ${attempt + 1} of 2): ${reason(err)}`);
                        }
                    }
                    if (ok) {
                        sent[i] = files[i].size;
                        succeeded++;
                        doneIds.push(photo_id);
                    } else {
                        failed.push(files[i]);
                        failedIds.push(photo_id);
                    }
                    report();
                    flush();
                }
            };
            await Promise.all(Array.from({ length: Math.min(PARALLEL_UPLOADS, files.length) }, worker));
            report(true);
            flush(true, true);
            await flushing;
        } catch (err) {
            console.error(`[picshare] upload stopped: ${reason(err)}`);
            toast.error(err instanceof Error ? err.message : "Upload failed");
            return;
        } finally {
            setProgress(null);
        }

        if (signError) toast.error(signError instanceof Error ? signError.message : "Couldn't start the upload");
        if (succeeded > 0) toast.success(`Uploaded ${succeeded} photo${succeeded === 1 ? "" : "s"} — preparing them in the background`);
        if (failed.length > 0) toast.error(`${failed.length} photo${failed.length === 1 ? "" : "s"} failed to upload — press Upload to try again`);
        setFiles(failed);
        if (inputRef.current) inputRef.current.value = "";
        onUploaded();
    };

    return (
        <div className="rounded-2xl border border-border bg-card p-5 space-y-4">
            <div
                role="button"
                tabIndex={0}
                onClick={() => !uploading && inputRef.current?.click()}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !uploading && inputRef.current?.click()}
                onDragOver={(e) => {
                    e.preventDefault();
                    setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                    e.preventDefault();
                    setDragOver(false);
                    if (!uploading) addFiles(e.dataTransfer.files);
                }}
                className={`flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed py-10 px-4 text-center cursor-pointer transition-colors ${
                    dragOver
                        ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/20"
                        : "border-border bg-muted/30 hover:border-indigo-400"
                }`}
            >
                <input
                    ref={inputRef}
                    type="file"
                    multiple
                    accept="image/jpeg,image/png"
                    className="hidden"
                    onChange={(e) => e.target.files && addFiles(e.target.files)}
                />
                <div className="w-12 h-12 rounded-full bg-indigo-100 dark:bg-indigo-900/40 flex items-center justify-center">
                    <UploadCloud className="w-6 h-6 text-indigo-600" />
                </div>
                <p className="font-semibold">Drag &amp; drop photos here, or click to browse</p>
                <p className="text-xs text-muted-foreground">
                    JPG or PNG
                    {remainingBytes !== null && ` · ${formatBytes(Math.max(remainingBytes, 0))} of storage left`}
                </p>
            </div>

            {files.length > 0 && (
                <div className="space-y-3">
                    <div className="flex items-center justify-between gap-3 text-sm">
                        <span className="flex items-center gap-2 font-medium">
                            <ImageIcon className="w-4 h-4 text-indigo-600" />
                            {files.length} photo{files.length === 1 ? "" : "s"} · {formatBytes(totalBytes)}
                        </span>
                        {!uploading && (
                            <button
                                onClick={() => setFiles([])}
                                className="flex items-center gap-1 text-muted-foreground hover:text-foreground"
                            >
                                <X className="w-3.5 h-3.5" />
                                Clear
                            </button>
                        )}
                    </div>

                    {overLimit && (
                        <p role="alert" className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-lg px-3 py-2">
                            These photos need {formatBytes(totalBytes)} but only {formatBytes(Math.max(remainingBytes ?? 0, 0))} is left.
                            Remove some, or create another event with more storage.
                        </p>
                    )}

                    {uploading && (
                        <div className="space-y-1">
                            <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                                <div
                                    className="h-full bg-indigo-600 rounded-full transition-[width]"
                                    style={{ width: `${Math.round((progress ?? 0) * 100)}%` }}
                                />
                            </div>
                            <p className="text-xs text-muted-foreground text-right">{Math.round((progress ?? 0) * 100)}%</p>
                        </div>
                    )}

                    <Button
                        onClick={upload}
                        disabled={uploading || overLimit}
                        className="w-full h-11 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold"
                    >
                        {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <UploadCloud className="w-4 h-4" />}
                        {uploading ? "Uploading…" : `Upload ${files.length} photo${files.length === 1 ? "" : "s"}`}
                    </Button>
                </div>
            )}
        </div>
    );
}
