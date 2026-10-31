"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Camera, Eye, EyeOff, Loader2, Lock, Mail, MailCheck, ScanFace, ShieldCheck, User as UserIcon, Zap } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, getToken, setToken, type User } from "@/lib/api";

type Mode = "login" | "register";

const COPY = {
    login: {
        title: "Welcome back",
        subtitle: "Log in to manage your events and photos.",
        submit: "Log in",
        busy: "Logging in…",
        switchText: "New to Pixello?",
        switchLink: "Create an account",
        switchHref: "/register",
    },
    register: {
        title: "Create your account",
        subtitle: "Start collecting event photos in minutes.",
        submit: "Create account",
        busy: "Creating account…",
        switchText: "Already have an account?",
        switchLink: "Log in",
        switchHref: "/login",
    },
} as const;

const HIGHLIGHTS = [
    { icon: Camera, text: "Upload your event photos in bulk" },
    { icon: ScanFace, text: "Guests find themselves with one selfie" },
    { icon: Zap, text: "Pay only for the storage you need" },
    { icon: ShieldCheck, text: "Private, per-event galleries" },
];

export default function AuthForm({ mode }: { mode: Mode }) {
    const router = useRouter();
    const copy = COPY[mode];
    const [loading, setLoading] = useState(false);
    const [showPassword, setShowPassword] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // The email a confirmation link was just sent to (register), or that still needs confirming (login)
    const [sentTo, setSentTo] = useState<string | null>(null);
    const [unconfirmedEmail, setUnconfirmedEmail] = useState<string | null>(null);

    // Already signed in → straight to the dashboard
    useEffect(() => {
        if (getToken()) router.replace("/dashboard");
    }, [router]);

    const resend = async (email: string) => {
        try {
            await api("/auth/resend-verification", { method: "POST", body: JSON.stringify({ email }) });
            toast.success("If that account still needs confirming, a new link is on its way.");
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Something went wrong");
        }
    };

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        setError(null);
        setUnconfirmedEmail(null);
        const fd = new FormData(e.currentTarget);
        const email = String(fd.get("email") ?? "");
        const password = String(fd.get("password") ?? "");

        if (mode === "register" && password !== String(fd.get("confirm") ?? "")) {
            setError("Passwords don't match");
            return;
        }

        setLoading(true);
        try {
            if (mode === "register") {
                // No login yet: the account is usable once the emailed link has been opened.
                await api("/auth/register", {
                    method: "POST",
                    body: JSON.stringify({ name: String(fd.get("name") ?? ""), email, password }),
                });
                setSentTo(email);
                return;
            }
            const data = await api<{ access_token: string; user: User }>("/auth/login", {
                method: "POST",
                body: JSON.stringify({ email, password }),
            });
            setToken(data.access_token);
            toast.success("Welcome back!");
            router.push("/dashboard");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Something went wrong");
            if (err instanceof ApiError && err.status === 403) setUnconfirmedEmail(email);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="grid lg:grid-cols-2 min-h-[calc(100vh-4rem-3.5rem)]">
            {/* Brand panel */}
            <aside className="hidden lg:flex relative overflow-hidden flex-col justify-center gap-10 px-16 bg-gradient-to-br from-indigo-600 via-indigo-700 to-purple-800 text-white">
                <div className="absolute -top-24 -right-24 w-96 h-96 rounded-full bg-white/10 blur-3xl" />
                <div className="absolute -bottom-32 -left-16 w-96 h-96 rounded-full bg-purple-400/20 blur-3xl" />
                <div className="relative space-y-4 max-w-md">
                    <h2 className="text-4xl font-extrabold tracking-tight leading-tight">
                        Every guest gets their photos. You do nothing.
                    </h2>
                    <p className="text-indigo-100 text-lg">
                        Upload once, share one link. Face recognition handles the rest.
                    </p>
                </div>
                <ul className="relative space-y-4">
                    {HIGHLIGHTS.map(({ icon: Icon, text }) => (
                        <li key={text} className="flex items-center gap-3 text-indigo-50">
                            <span className="w-9 h-9 rounded-xl bg-white/15 flex items-center justify-center">
                                <Icon className="w-4 h-4" />
                            </span>
                            {text}
                        </li>
                    ))}
                </ul>
            </aside>

            {/* Form */}
            <div className="flex items-center justify-center px-4 py-12">
                <div className="w-full max-w-sm space-y-8">
                    {sentTo ? (
                        <div className="space-y-4 text-center">
                            <span className="mx-auto w-12 h-12 rounded-2xl bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 flex items-center justify-center">
                                <MailCheck className="w-6 h-6" />
                            </span>
                            <h1 className="text-3xl font-bold tracking-tight">Check your email</h1>
                            <p className="text-muted-foreground">
                                If <span className="font-medium text-foreground">{sentTo}</span> can be used for a new account, we&apos;ve
                                sent a link to confirm it. Open the link to finish signing up.
                            </p>
                            <p className="text-sm text-muted-foreground">
                                Nothing there?{" "}
                                <button
                                    type="button"
                                    onClick={() => resend(sentTo)}
                                    className="font-semibold text-indigo-600 dark:text-indigo-400 hover:underline"
                                >
                                    Send it again
                                </button>
                            </p>
                        </div>
                    ) : (
                    <>
                    <div className="space-y-2">
                        <h1 className="text-3xl font-bold tracking-tight">{copy.title}</h1>
                        <p className="text-muted-foreground">{copy.subtitle}</p>
                    </div>

                    <form onSubmit={handleSubmit} className="space-y-4" noValidate={false}>
                        {mode === "register" && (
                            <Field id="name" label="Full name" icon={UserIcon}>
                                <Input id="name" name="name" placeholder="Jane Doe" required autoComplete="name" className="pl-10 h-11" />
                            </Field>
                        )}
                        <Field id="email" label="Email" icon={Mail}>
                            <Input id="email" name="email" type="email" placeholder="you@example.com" required autoComplete="email" className="pl-10 h-11" />
                        </Field>
                        <Field id="password" label="Password" icon={Lock}>
                            <Input
                                id="password"
                                name="password"
                                type={showPassword ? "text" : "password"}
                                required
                                minLength={mode === "register" ? 8 : undefined}
                                placeholder={mode === "register" ? "At least 8 characters" : "Your password"}
                                autoComplete={mode === "register" ? "new-password" : "current-password"}
                                className="pl-10 pr-10 h-11"
                            />
                            <button
                                type="button"
                                onClick={() => setShowPassword((v) => !v)}
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                aria-label={showPassword ? "Hide password" : "Show password"}
                            >
                                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                            </button>
                        </Field>
                        {mode === "login" && (
                            <div className="text-right -mt-2">
                                <Link href="/forgot-password" className="text-sm font-medium text-indigo-600 dark:text-indigo-400 hover:underline">
                                    Forgot password?
                                </Link>
                            </div>
                        )}
                        {mode === "register" && (
                            <Field id="confirm" label="Confirm password" icon={Lock}>
                                <Input id="confirm" name="confirm" type={showPassword ? "text" : "password"} required autoComplete="new-password" className="pl-10 h-11" />
                            </Field>
                        )}

                        {error && (
                            <p role="alert" className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-lg px-3 py-2">
                                {error}
                                {unconfirmedEmail && (
                                    <>
                                        {" "}
                                        <button
                                            type="button"
                                            onClick={() => resend(unconfirmedEmail)}
                                            className="font-semibold underline"
                                        >
                                            Send the link again
                                        </button>
                                    </>
                                )}
                            </p>
                        )}

                        <Button
                            type="submit"
                            disabled={loading}
                            className="w-full h-11 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold"
                        >
                            {loading && <Loader2 className="w-4 h-4 animate-spin" />}
                            {loading ? copy.busy : copy.submit}
                        </Button>
                    </form>

                    <p className="text-sm text-center text-muted-foreground">
                        {copy.switchText}{" "}
                        <Link href={copy.switchHref} className="font-semibold text-indigo-600 dark:text-indigo-400 hover:underline">
                            {copy.switchLink}
                        </Link>
                    </p>
                    </>
                    )}
                </div>
            </div>
        </div>
    );
}

function Field({
    id,
    label,
    icon: Icon,
    children,
}: {
    id: string;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    children: React.ReactNode;
}) {
    return (
        <div className="space-y-2">
            <Label htmlFor={id}>{label}</Label>
            <div className="relative">
                <Icon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                {children}
            </div>
        </div>
    );
}
