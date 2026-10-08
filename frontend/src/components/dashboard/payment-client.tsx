"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, CalendarDays, CheckCircle2, CreditCard, FlaskConical, HardDrive, Layers, Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, formatDateRange, formatMoney, planInfo, type EventItem, type PaymentConfig } from "@/lib/api";
import { useAuth } from "@/lib/use-auth";

type Busy = null | "pay" | "fail" | "skip";

export default function PaymentClient() {
    const { id } = useParams<{ id: string }>();
    const router = useRouter();
    const { user, loading: authLoading } = useAuth({ required: true });

    const [event, setEvent] = useState<EventItem | null>(null);
    const [config, setConfig] = useState<PaymentConfig | null>(null);
    const [busy, setBusy] = useState<Busy>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!user) return;
        Promise.all([api<EventItem>(`/events/${id}`), api<PaymentConfig>("/payments/config")])
            .then(([ev, cfg]) => {
                if (ev.status === "active") {
                    router.replace(`/dashboard/events/${id}`);
                    return;
                }
                setEvent(ev);
                setConfig(cfg);
            })
            .catch((err) => {
                toast.error(err.message);
                router.replace("/dashboard");
            });
    }, [user, id, router]);

    if (authLoading || !user || !event || !config) {
        return (
            <div className="flex h-[60vh] items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-indigo-600" />
            </div>
        );
    }

    const finish = (message: string) => {
        toast.success(message);
        router.push(`/dashboard/events/${id}`);
    };

    const pay = async (outcome: "success" | "failure") => {
        setBusy(outcome === "success" ? "pay" : "fail");
        setError(null);
        try {
            const checkout = await api<{ payment_id: string }>(`/payments/events/${id}/checkout`, { method: "POST" });
            await api(`/payments/events/${id}/confirm`, {
                method: "POST",
                body: JSON.stringify({ payment_id: checkout.payment_id, outcome }),
            });
            finish("Payment successful — your event is live!");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Payment failed");
            setBusy(null);
        }
    };

    const skip = async () => {
        setBusy("skip");
        setError(null);
        try {
            await api(`/payments/events/${id}/skip`, { method: "POST" });
            finish("Event created (payment skipped)");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Couldn't skip payment");
            setBusy(null);
        }
    };

    const total = formatMoney(event.amount_cents, config.currency);
    const isMock = config.provider === "mock";

    return (
        <div className="max-w-4xl mx-auto px-4 py-10 space-y-8">
            <div className="space-y-4">
                <Link href="/dashboard" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
                    <ArrowLeft className="w-4 h-4" />
                    Back to events
                </Link>
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Review &amp; pay</h1>
                    <p className="text-muted-foreground mt-1">Step 2 of 2 — your event is created as soon as payment completes.</p>
                </div>
            </div>

            <div className="grid md:grid-cols-2 gap-6">
                {/* Order */}
                <section className="rounded-2xl border border-border bg-card p-6 space-y-5">
                    <h2 className="font-bold">Order summary</h2>
                    <div>
                        <p className="text-lg font-semibold">{event.name}</p>
                        <div className="mt-2 space-y-1.5 text-sm text-muted-foreground">
                            <p className="flex items-center gap-2">
                                <CalendarDays className="w-4 h-4" />
                                {formatDateRange(event.start_date ?? event.date, event.end_date)}
                            </p>
                            <p className="flex items-center gap-2">
                                <HardDrive className="w-4 h-4" />
                                {event.storage_capacity_gb} GB photo storage
                            </p>
                            <p className="flex items-center gap-2">
                                <Layers className="w-4 h-4" />
                                {planInfo(event.plan).label}
                            </p>
                        </div>
                    </div>
                    <div className="flex items-baseline justify-between border-t border-border pt-4">
                        <span className="text-sm text-muted-foreground">Total due</span>
                        <span className="text-3xl font-extrabold">{total}</span>
                    </div>
                </section>

                {/* Checkout */}
                <section className="rounded-2xl border border-border bg-card p-6 space-y-5">
                    <h2 className="font-bold flex items-center gap-2">
                        <CreditCard className="w-4 h-4 text-indigo-600" />
                        Payment
                    </h2>

                    {isMock && (
                        <div className="flex gap-3 rounded-xl border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-950/30 p-3 text-sm text-amber-800 dark:text-amber-300">
                            <FlaskConical className="w-4 h-4 mt-0.5 shrink-0" />
                            <p>Test mode — no real payment is taken. Use the buttons below to simulate the outcome.</p>
                        </div>
                    )}

                    {error && (
                        <p role="alert" className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-lg px-3 py-2">
                            {error}
                        </p>
                    )}

                    <div className="space-y-3">
                        <Button
                            onClick={() => pay("success")}
                            disabled={busy !== null}
                            className="w-full h-11 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold"
                        >
                            {busy === "pay" ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                            {busy === "pay" ? "Processing…" : `Pay ${total}`}
                        </Button>

                        {isMock && (
                            <Button variant="outline" onClick={() => pay("failure")} disabled={busy !== null} className="w-full h-10">
                                {busy === "fail" && <Loader2 className="w-4 h-4 animate-spin" />}
                                Simulate failed payment
                            </Button>
                        )}

                        {!config.payment_required && (
                            <Button variant="ghost" onClick={skip} disabled={busy !== null} className="w-full h-10 text-muted-foreground">
                                {busy === "skip" && <Loader2 className="w-4 h-4 animate-spin" />}
                                Skip payment (development only)
                            </Button>
                        )}
                    </div>

                    <p className="flex items-center gap-2 text-xs text-muted-foreground">
                        <ShieldCheck className="w-4 h-4" />
                        Payments are processed securely. We never store card details.
                    </p>
                </section>
            </div>
        </div>
    );
}
