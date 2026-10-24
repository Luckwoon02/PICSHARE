"use client";

import React, { useEffect, useRef, useState } from "react";
import { ImageIcon, Loader2, UploadCloud, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { API_URL, clearToken, formatBytes, getToken } from "@/lib/api";
import { keepUploadAlive, startUpload, type UploadResult } from "@/lib/upload-client";

const ALLOWED_TYPES = ["image/jpeg", "image/png"];

const reason = (err: unknown) => (err instanceof Error ? err.message : String(err));

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
        const baseTitle = document.title;
        const stopKeepingAlive = keepUploadAlive(); // stops the browser freezing/sleeping the page mid-upload
        let result: UploadResult;
        try {
            // The upload runs in a background worker (see lib/upload-engine.ts), so it carries on while this tab is
            // in the background or the page is busy. Progress comes back here for the bar and the tab title.
            result = await startUpload({ apiUrl: API_URL, token: getToken(), eventId, files }, (fraction) => {
                setProgress(fraction);
                document.title = `Uploading ${Math.round(fraction * 100)}% · ${baseTitle}`;
            });
        } catch (err) {
            console.error(`[pixello] upload stopped: ${reason(err)}`);
            toast.error(err instanceof Error ? err.message : "Upload failed");
            return;
        } finally {
            stopKeepingAlive();
            document.title = baseTitle;
            setProgress(null);
        }

        if (result.unauthorized) clearToken(); // the login ran out: send the user back to sign in
        const failed = result.failedIndexes.map((i) => files[i]);
        if (result.signError) toast.error(result.signError);
        if (result.succeeded > 0) {
            toast.success(`Uploaded ${result.succeeded} photo${result.succeeded === 1 ? "" : "s"} — preparing them in the background`);
        }
        if (failed.length > 0) {
            toast.error(`${failed.length} photo${failed.length === 1 ? "" : "s"} failed to upload — press Upload to try again`, {
                description: result.firstUploadError ?? undefined,
            });
        }
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
                            <p className="flex justify-between gap-3 text-xs text-muted-foreground">
                                <span>Switch tabs or apps if you like, but keep this page open until the upload finishes. Your photos are prepared right after.</span>
                                <span>{Math.round((progress ?? 0) * 100)}%</span>
                            </p>
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
