"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, CalendarDays, CreditCard, HardDrive, Layers, Loader2, Plus, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, formatDateRange, PLAN_INFO, type EventItem } from "@/lib/api";
import { useAuth } from "@/lib/use-auth";
import { StatusBadge } from "@/components/dashboard/status-badge";

export default function DashboardClient() {
    const { user, loading: authLoading } = useAuth({ required: true });
    const [events, setEvents] = useState<EventItem[] | null>(null);

    useEffect(() => {
        if (!user) return;
        api<EventItem[]>("/events")
            .then(setEvents)
            .catch((err) => {
                toast.error(err.message);
                setEvents([]);
            });
    }, [user]);

    if (authLoading || !user || events === null) {
        return (
            <div className="flex h-[60vh] items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-indigo-600" />
            </div>
        );
    }

    const active = events.filter((e) => e.status === "active").length;

    return (
        <div className="max-w-6xl mx-auto px-4 py-10 space-y-8">
            <header className="flex flex-wrap items-end justify-between gap-4">
                <div>
                    <p className="text-sm text-muted-foreground">Hi, {user.name.split(" ")[0]} 👋</p>
                    <h1 className="text-3xl font-bold tracking-tight">Your events</h1>
                </div>
                <Button asChild className="bg-indigo-600 hover:bg-indigo-700 text-white h-10">
                    <Link href="/dashboard/events/new">
                        <Plus className="w-4 h-4" />
                        Create event
                    </Link>
                </Button>
            </header>

            {events.length > 0 && (
                <div className="grid grid-cols-3 gap-3 sm:gap-4">
                    <Stat label="Total events" value={events.length} />
                    <Stat label="Active" value={active} />
                    <Stat label="Awaiting payment" value={events.length - active} />
                </div>
            )}

            {events.length === 0 ? (
                <div className="rounded-3xl border-2 border-dashed border-border py-20 px-6 text-center space-y-4">
                    <div className="mx-auto w-16 h-16 rounded-2xl bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 flex items-center justify-center">
                        <Sparkles className="w-8 h-8" />
                    </div>
                    <h2 className="text-xl font-bold">Create your first event</h2>
                    <p className="text-muted-foreground max-w-md mx-auto">
                        Pick the dates and how much storage you need, then upload your photos. Guests will be able to find
                        theirs with a selfie.
                    </p>
                    <Button asChild className="bg-indigo-600 hover:bg-indigo-700 text-white">
                        <Link href="/dashboard/events/new">
                            <Plus className="w-4 h-4" />
                            Create event
                        </Link>
                    </Button>
                </div>
            ) : (
                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
                    {events.map((event) => (
                        <EventCard key={event._id} event={event} />
                    ))}
                </div>
            )}
        </div>
    );
}

function Stat({ label, value }: { label: string; value: number }) {
    return (
        <div className="rounded-2xl border border-border bg-card p-4">
            <p className="text-2xl font-bold">{value}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{label}</p>
        </div>
    );
}

function EventCard({ event }: { event: EventItem }) {
    const pending = event.status === "pending_payment";
    const href = pending ? `/dashboard/events/${event._id}/payment` : `/dashboard/events/${event._id}`;

    return (
        <Link
            href={href}
            className="group rounded-2xl border border-border bg-card p-5 space-y-4 shadow-sm hover:shadow-lg hover:border-indigo-300 dark:hover:border-indigo-700 transition-all"
        >
            <div className="flex items-start justify-between gap-3">
                <h3 className="font-bold text-lg leading-snug group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors">
                    {event.name}
                </h3>
                <StatusBadge status={event.status} />
            </div>

            <div className="space-y-1.5 text-sm text-muted-foreground">
                <p className="flex items-center gap-2">
                    <CalendarDays className="w-4 h-4 shrink-0" />
                    {formatDateRange(event.start_date ?? event.date, event.end_date)}
                </p>
                <p className="flex items-center gap-2">
                    <HardDrive className="w-4 h-4 shrink-0" />
                    {event.storage_capacity_gb ? `${event.storage_capacity_gb} GB storage` : "Unlimited storage"}
                </p>
                <p className="flex items-center gap-2">
                    <Layers className="w-4 h-4 shrink-0" />
                    {PLAN_INFO[event.plan].label}
                </p>
            </div>

            <div className="flex items-center justify-between pt-3 border-t border-border text-sm font-semibold text-indigo-600 dark:text-indigo-400">
                {pending ? (
                    <span className="flex items-center gap-2">
                        <CreditCard className="w-4 h-4" />
                        Complete payment
                    </span>
                ) : (
                    <span>Manage photos</span>
                )}
                <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </div>
        </Link>
    );
}
