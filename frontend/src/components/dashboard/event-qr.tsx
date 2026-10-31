"use client";

import React, { useEffect, useState } from "react";
import { Download, Loader2, Printer, QrCode, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { makeCard } from "@/lib/qr-card";

// A printable QR card for an event. Guests scan it with their phone camera, land on the event's
// guest page, take a selfie, and get their photos. No account or login is involved.

const BASE_KEY = "pixello-public-base";

const isLocal = (base: string) => /^https?:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/i.test(base);

function savedBase(): string {
    try {
        return localStorage.getItem(BASE_KEY) || window.location.origin;
    } catch {
        return window.location.origin;
    }
}

export function EventQr({ slug, eventName, code }: { slug: string; eventName: string; code: string | null }) {
    const [open, setOpen] = useState(false);
    return (
        <>
            <Button variant="outline" onClick={() => setOpen(true)}>
                <QrCode className="w-4 h-4" />
                QR code
            </Button>
            {open && <QrDialog slug={slug} eventName={eventName} code={code} onClose={() => setOpen(false)} />}
        </>
    );
}

function QrDialog({ slug, eventName, code, onClose }: { slug: string; eventName: string; code: string | null; onClose: () => void }) {
    const [base, setBase] = useState(savedBase);
    const [card, setCard] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const cleanBase = base.trim().replace(/\/+$/, "");
    const valid = /^https?:\/\/[^\s/]+/i.test(cleanBase);
    const url = `${cleanBase}/event/${slug}`;

    useEffect(() => {
        if (!valid) return;
        let alive = true;
        makeCard(url, eventName, code)
            .then((png) => {
                if (!alive) return;
                setCard(png);
                setError(null);
            })
            .catch((err) => alive && setError(err instanceof Error ? err.message : "Couldn't make the QR code"));
        return () => {
            alive = false;
        };
    }, [url, valid, eventName, code]);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    const changeBase = (value: string) => {
        setBase(value);
        try {
            localStorage.setItem(BASE_KEY, value);
        } catch {}
    };

    const download = () => {
        if (!card) return;
        const a = document.createElement("a");
        a.href = card;
        a.download = `${slug}-qr.png`;
        a.click();
    };

    const print = () => {
        if (!card) return;
        const w = window.open("", "_blank");
        if (!w) {
            toast.error("Allow pop-ups to print, or download the card and print it from there");
            return;
        }
        w.document.write(
            `<!doctype html><title>${eventName.replace(/[<&]/g, "")} QR</title>` +
                `<style>@page{margin:12mm}body{margin:0;display:flex;justify-content:center}img{max-width:100%;max-height:100vh}</style>` +
                `<img src="${card}" onload="setTimeout(function(){window.print()},150)">`
        );
        w.document.close();
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
            onClick={onClose}
            role="dialog"
            aria-modal="true"
            aria-label="Event QR code"
        >
            <div
                className="relative w-full max-w-md max-h-[92vh] overflow-y-auto rounded-2xl border border-border bg-background p-5 space-y-4 shadow-xl"
                onClick={(e) => e.stopPropagation()}
            >
                <button onClick={onClose} aria-label="Close" className="absolute right-3 top-3 rounded-full p-1.5 text-muted-foreground hover:bg-muted">
                    <X className="w-5 h-5" />
                </button>

                <div>
                    <h2 className="text-lg font-bold">QR code for guests</h2>
                    <p className="text-sm text-muted-foreground">
                        Print it or show it at the event. Guests scan it with their phone camera, take a selfie, and get their photos. No account needed.
                    </p>
                </div>

                <div className="rounded-xl border border-border bg-white p-2 flex items-center justify-center min-h-48">
                    {error ? (
                        <p className="text-sm text-destructive p-4">{error}</p>
                    ) : !valid ? (
                        <p className="text-sm text-muted-foreground p-4">Enter the web address below to see the QR code.</p>
                    ) : card ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={card} alt={`QR code for ${eventName}`} className="w-full h-auto rounded-lg" />
                    ) : (
                        <Loader2 className="w-6 h-6 animate-spin text-indigo-600" />
                    )}
                </div>

                <div className="flex gap-2">
                    <Button onClick={download} disabled={!card || !valid} className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold">
                        <Download className="w-4 h-4" />
                        Download PNG
                    </Button>
                    <Button variant="outline" onClick={print} disabled={!card || !valid} className="flex-1">
                        <Printer className="w-4 h-4" />
                        Print
                    </Button>
                </div>

                <div className="space-y-1.5">
                    <label htmlFor="qr-base" className="text-sm font-medium">
                        Web address guests will open
                    </label>
                    <Input
                        id="qr-base"
                        value={base}
                        onChange={(e) => changeBase(e.target.value)}
                        spellCheck={false}
                        autoComplete="off"
                        className="h-10 font-mono text-sm"
                    />
                    <p className="text-xs text-muted-foreground break-all">
                        Link in the QR code: <span className="font-mono">{valid ? url : "—"}</span>
                    </p>
                    {valid && isLocal(cleanBase) && (
                        <p className="rounded-lg border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                            <span className="font-semibold">localhost only works on this computer.</span> To test with a phone on the same Wi-Fi,
                            put your computer&apos;s address here instead, like <span className="font-mono">http://192.168.1.5:3000</span>.
                        </p>
                    )}
                </div>
            </div>
        </div>
    );
}
