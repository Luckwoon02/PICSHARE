"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Camera, Sparkles, ShieldCheck, Zap, ArrowRight, PartyPopper, CalendarPlus } from "lucide-react";

export default function HomeClient() {
    const router = useRouter();

    return (
        <div className="bg-background selection:bg-indigo-100/30">
            {/* Hero Section */}
            <header className="relative overflow-hidden bg-background pt-16 pb-32 border-b border-border">
                <div className="absolute inset-0 bg-[radial-gradient(45%_45%_at_50%_50%,rgba(99,102,241,0.05)_0%,rgba(0,0,0,0)_100%)]" />

                <div className="container relative mx-auto px-6 pt-12 text-center">
                    <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-50 dark:bg-indigo-950/30 text-indigo-600 dark:text-indigo-400 text-sm font-semibold mb-8 border border-indigo-100 dark:border-indigo-900/50 animate-fade-in">
                        <Sparkles className="w-4 h-4" />
                        <span>AI-Powered Event Photography</span>
                    </div>

                    <h1 className="text-5xl md:text-7xl font-extrabold tracking-tight text-foreground mb-8 max-w-4xl mx-auto leading-[1.1]">
                        Find every photo of yourself, <span className="text-indigo-600 dark:text-indigo-400">instantly.</span>
                    </h1>

                    <p className="text-lg md:text-xl text-muted-foreground mb-12 max-w-2xl mx-auto leading-relaxed">
                        No more hunting through thousands of event photos. Upload a selfie and our AI will find every moment you captured, delivered straight to your personal gallery.
                    </p>

                    <div className="flex flex-col sm:flex-row justify-center gap-4">
                        <Button
                            className="h-14 px-8 bg-indigo-600 hover:bg-indigo-700 text-white text-lg font-bold rounded-2xl shadow-xl shadow-indigo-200 dark:shadow-none transition-all hover:scale-105 active:scale-95 group"
                            onClick={() => router.push('/register')}
                        >
                            <CalendarPlus className="mr-2 w-5 h-5" />
                            Host an event
                            <ArrowRight className="ml-2 w-5 h-5 group-hover:translate-x-1 transition-transform" />
                        </Button>
                        <Button
                            variant="outline"
                            className="h-14 px-8 text-lg font-bold rounded-2xl transition-all hover:scale-105 active:scale-95 group"
                            onClick={() => router.push('/events')}
                        >
                            <PartyPopper className="mr-2 w-5 h-5 group-hover:rotate-12 transition-transform" />
                            Find my photos
                        </Button>
                    </div>
                </div>
            </header>

            {/* Features Grid */}
            <section className="py-24 bg-background">
                <div className="container mx-auto px-6">
                    <div className="grid md:grid-cols-3 gap-12">
                        <div className="space-y-4">
                            <div className="w-12 h-12 rounded-2xl bg-indigo-600 flex items-center justify-center text-white shadow-lg shadow-indigo-100 dark:shadow-none">
                                <Camera className="w-6 h-6" />
                            </div>
                            <h3 className="text-xl font-bold text-foreground">One Selfie Only</h3>
                            <p className="text-muted-foreground leading-relaxed">
                                Just one selfie is all we need to scan through thousands of high-resolution originals.
                            </p>
                        </div>

                        <div className="space-y-4">
                            <div className="w-12 h-12 rounded-2xl bg-indigo-600 flex items-center justify-center text-white shadow-lg shadow-indigo-100 dark:shadow-none">
                                <Zap className="w-6 h-6" />
                            </div>
                            <h3 className="text-xl font-bold text-foreground">Instant Delivery</h3>
                            <p className="text-muted-foreground leading-relaxed">
                                We identify your photos instantly and create a secure personal web gallery just for you.
                            </p>
                        </div>

                        <div className="space-y-4">
                            <div className="w-12 h-12 rounded-2xl bg-indigo-600 flex items-center justify-center text-white shadow-lg shadow-indigo-100 dark:shadow-none">
                                <ShieldCheck className="w-6 h-6" />
                            </div>
                            <h3 className="text-xl font-bold text-foreground">Private & Secure</h3>
                            <p className="text-muted-foreground leading-relaxed">
                                Your selfie is deleted immediately after processing. We never store your biometric data.
                            </p>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    );
}
