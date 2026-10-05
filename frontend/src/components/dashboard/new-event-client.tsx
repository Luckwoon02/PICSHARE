"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, formatMoney, type EventItem, type PaymentConfig } from "@/lib/api";
import { useAuth } from "@/lib/use-auth";

const PRESETS = [5, 10, 50, 100];

export default function NewEventClient() {
    const { user, loading: authLoading } = useAuth({ required: true });
    const router = useRouter();

    const [config, setConfig] = useState<PaymentConfig | null>(null);
    const [startDate, setStartDate] = useState("");
    const [endDate, setEndDate] = useState("");
    const [capacity, setCapacity] = useState("10");
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        api<PaymentConfig>("/payments/config").then(setConfig).catch(() => toast.error("Couldn't load pricing"));
    }, []);

    if (authLoading || !user || !config) {
        return (
            <div className="flex h-[60vh] items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-indigo-600" />
            </div>
        );
    }

    const gb = parseFloat(capacity);
    const capacityValid = gb >= config.min_storage_gb && gb <= config.max_storage_gb;
    const price = capacityValid ? Math.ceil(gb * config.price_per_gb_cents) : 0;

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        const fd = new FormData(e.currentTarget);
        setSubmitting(true);
        try {
            const event = await api<EventItem>("/events", {
                method: "POST",
                body: JSON.stringify({
                    name: String(fd.get("name")),
                    start_date: new Date(startDate).toISOString(),
                    end_date: new Date(endDate).toISOString(),
                    storage_capacity_gb: gb,
                    secret_code: String(fd.get("secret_code") ?? "").trim() || null,
                }),
            });
            router.push(`/dashboard/events/${event._id}/payment`);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't create event");
            setSubmitting(false);
        }
    };

    return (
        <div className="max-w-5xl mx-auto px-4 py-10 space-y-8">
            <div className="space-y-4">
                <Link href="/dashboard" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
                    <ArrowLeft className="w-4 h-4" />
                    Back to events
                </Link>
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Create an event</h1>
                    <p className="text-muted-foreground mt-1">Step 1 of 2 — then you&apos;ll review and pay.</p>
                </div>
            </div>

            <form onSubmit={handleSubmit} className="grid lg:grid-cols-[1fr_340px] gap-8 items-start">
                <div className="rounded-2xl border border-border bg-card p-6 space-y-6">
                    <div className="space-y-2">
                        <Label htmlFor="name">Event name</Label>
                        <Input id="name" name="name" required maxLength={120} placeholder="Priya & Arjun's Wedding" className="h-11" />
                    </div>

                    <div className="grid sm:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label htmlFor="start">Start date</Label>
                            <Input
                                id="start"
                                type="date"
                                required
                                value={startDate}
                                onChange={(e) => {
                                    setStartDate(e.target.value);
                                    if (endDate && endDate < e.target.value) setEndDate(e.target.value);
                                }}
                                className="h-11"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="end">End date</Label>
                            <Input
                                id="end"
                                type="date"
                                required
                                min={startDate || undefined}
                                value={endDate}
                                onChange={(e) => setEndDate(e.target.value)}
                                className="h-11"
                            />
                        </div>
                    </div>

                    <div className="space-y-3">
                        <Label htmlFor="capacity">Storage capacity (GB)</Label>
                        <div className="flex flex-wrap items-center gap-2">
                            {PRESETS.map((p) => (
                                <button
                                    key={p}
                                    type="button"
                                    onClick={() => setCapacity(String(p))}
                                    className={`h-10 px-4 rounded-lg border text-sm font-semibold transition-colors ${
                                        gb === p
                                            ? "border-indigo-600 bg-indigo-600 text-white"
                                            : "border-border bg-background hover:border-indigo-400"
                                    }`}
                                >
                                    {p} GB
                                </button>
                            ))}
                            <Input
                                id="capacity"
                                type="number"
                                inputMode="decimal"
                                required
                                min={config.min_storage_gb}
                                max={config.max_storage_gb}
                                step="any"
                                value={capacity}
                                onChange={(e) => setCapacity(e.target.value)}
                                className="h-10 w-28"
                                aria-describedby="capacity-hint"
                            />
                        </div>
                        <p id="capacity-hint" className={`text-xs ${capacity && !capacityValid ? "text-destructive" : "text-muted-foreground"}`}>
                            Space for your original photos in S3. Between {config.min_storage_gb} and {config.max_storage_gb} GB.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="secret_code">
                            Guest access code <span className="text-muted-foreground font-normal">(optional)</span>
                        </Label>
                        <Input id="secret_code" name="secret_code" placeholder="Leave empty for open access" className="h-11" />
                        <p className="text-xs text-muted-foreground">Guests must enter this code before they can search for their photos.</p>
                    </div>
                </div>

                {/* Summary */}
                <aside className="rounded-2xl border border-border bg-card p-6 space-y-5 lg:sticky lg:top-24">
                    <h2 className="font-bold">Summary</h2>
                    <dl className="space-y-3 text-sm">
                        <Row label="Storage" value={capacityValid ? `${gb} GB` : "—"} />
                        <Row label="Rate" value={`${formatMoney(config.price_per_gb_cents, config.currency)} / GB`} />
                    </dl>
                    <div className="flex items-baseline justify-between border-t border-border pt-4">
                        <span className="text-sm text-muted-foreground">Total</span>
                        <span className="text-3xl font-extrabold">{formatMoney(price, config.currency)}</span>
                    </div>
                    <Button
                        type="submit"
                        disabled={submitting || !capacityValid || !startDate || !endDate}
                        className="w-full h-11 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold"
                    >
                        {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                        Continue to payment
                        {!submitting && <ArrowRight className="w-4 h-4" />}
                    </Button>
                    <p className="text-xs text-muted-foreground text-center">
                        Your event goes live once payment is complete.
                    </p>
                </aside>
            </form>
        </div>
    );
}

function Row({ label, value }: { label: string; value: string }) {
    return (
        <div className="flex justify-between">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-medium">{value}</dd>
        </div>
    );
}
