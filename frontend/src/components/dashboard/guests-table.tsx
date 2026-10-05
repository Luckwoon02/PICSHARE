"use client";

import React, { useState } from "react";
import { ExternalLink, Loader2, Search, Trash2, Users } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { API_URL, api } from "@/lib/api";

export interface Guest {
    id: string;
    name: string;
    email: string;
    phone?: string | null;
    status: string;
    match_count: number;
    created_at: string;
    gallery_link: string | null;
}

const STATUS_STYLES: Record<string, string> = {
    completed: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
    processing: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
};

export function GuestsTable({
    guests,
    loading,
    onChanged,
}: {
    guests: Guest[];
    loading: boolean;
    onChanged: () => void;
}) {
    const [query, setQuery] = useState("");

    const remove = async (guest: Guest) => {
        if (!confirm(`Remove ${guest.name}? They'll be able to submit a new selfie.`)) return;
        try {
            await api(`/guests/${guest.id}`, { method: "DELETE" });
            toast.success(`${guest.name} removed`);
            onChanged();
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't remove guest");
        }
    };

    if (loading) {
        return (
            <div className="flex justify-center py-16">
                <Loader2 className="w-7 h-7 animate-spin text-indigo-600" />
            </div>
        );
    }

    if (guests.length === 0) {
        return (
            <div className="text-center py-16 text-muted-foreground">
                <Users className="w-12 h-12 mx-auto mb-3 opacity-40" />
                <p className="font-medium">No guests yet</p>
                <p className="text-sm">Share the event link — guests appear here after submitting a selfie.</p>
            </div>
        );
    }

    const q = query.trim().toLowerCase();
    const shown = q ? guests.filter((g) => g.name.toLowerCase().includes(q) || (g.email ?? "").toLowerCase().includes(q)) : guests;

    return (
        <div className="space-y-4">
            <div className="relative max-w-sm">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search by name or email"
                    className="pl-9"
                />
            </div>

            <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full text-sm">
                    <thead className="bg-muted/50 text-xs uppercase tracking-wider text-muted-foreground">
                        <tr>
                            <th className="text-left p-3 font-semibold">Guest</th>
                            <th className="text-left p-3 font-semibold">Status</th>
                            <th className="text-right p-3 font-semibold">Matches</th>
                            <th className="p-3" />
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                        {shown.map((g) => (
                            <tr key={g.id} className="hover:bg-muted/30">
                                <td className="p-3">
                                    <div className="flex items-center gap-3">
                                        {/* eslint-disable-next-line @next/next/no-img-element */}
                                        <img
                                            src={`${API_URL}/guests/selfie/${g.id}`}
                                            alt=""
                                            className="w-10 h-10 rounded-full object-cover bg-muted"
                                            onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                                        />
                                        <div className="min-w-0">
                                            <p className="font-semibold truncate">{g.name}</p>
                                            {g.email && <p className="text-xs text-muted-foreground truncate">{g.email}</p>}
                                        </div>
                                    </div>
                                </td>
                                <td className="p-3">
                                    <span
                                        className={`inline-block rounded-full px-2.5 py-1 text-xs font-semibold ${
                                            STATUS_STYLES[g.status] ?? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                                        }`}
                                    >
                                        {g.status}
                                    </span>
                                </td>
                                <td className="p-3 text-right font-bold text-indigo-600 dark:text-indigo-400">{g.match_count}</td>
                                <td className="p-3">
                                    <div className="flex justify-end gap-2">
                                        {g.gallery_link && (
                                            <Button asChild variant="outline" size="sm">
                                                <a href={g.gallery_link} target="_blank" rel="noopener noreferrer">
                                                    <ExternalLink className="w-3.5 h-3.5" />
                                                    Gallery
                                                </a>
                                            </Button>
                                        )}
                                        <Button
                                            variant="outline"
                                            size="icon-sm"
                                            onClick={() => remove(g)}
                                            aria-label={`Remove ${g.name}`}
                                            className="text-red-600 border-red-200 dark:border-red-900 hover:bg-red-50 dark:hover:bg-red-950/30"
                                        >
                                            <Trash2 className="w-3.5 h-3.5" />
                                        </Button>
                                    </div>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {shown.length === 0 && <p className="p-6 text-center text-sm text-muted-foreground">No guests match your search.</p>}
            </div>
        </div>
    );
}
