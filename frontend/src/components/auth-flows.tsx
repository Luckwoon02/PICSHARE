"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, CheckCircle2, Loader2, Lock, Mail, MailCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, setToken, type User } from "@/lib/api";

const linkClass = "font-semibold text-indigo-600 dark:text-indigo-400 hover:underline";

function Shell({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
    return (
        <div className="flex items-center justify-center px-4 py-12 min-h-[calc(100vh-4rem-3.5rem)]">
            <div className="w-full max-w-sm space-y-6 text-center">
                <span className="mx-auto w-12 h-12 rounded-2xl bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 flex items-center justify-center">
                    {icon}
                </span>
                <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
                {children}
            </div>
        </div>
    );
}

function Field({ id, label, icon: Icon, children }: {
    id: string;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    children: React.ReactNode;
}) {
    return (
        <div className="space-y-2 text-left">
            <Label htmlFor={id}>{label}</Label>
            <div className="relative">
                <Icon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                {children}
            </div>
        </div>
    );
}

function ErrorText({ children }: { children: React.ReactNode }) {
    return (
        <p role="alert" className="text-sm text-left text-destructive bg-destructive/10 border border-destructive/20 rounded-lg px-3 py-2">
            {children}
        </p>
    );
}

export function ForgotPasswordForm() {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sentTo, setSentTo] = useState<string | null>(null);

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        setError(null);
        const email = String(new FormData(e.currentTarget).get("email") ?? "");
        setLoading(true);
        try {
            await api("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }) });
            setSentTo(email);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Something went wrong");
        } finally {
            setLoading(false);
        }
    };

    if (sentTo) {
        return (
            <Shell icon={<MailCheck className="w-6 h-6" />} title="Check your email">
                <p className="text-muted-foreground">
                    If <span className="font-medium text-foreground">{sentTo}</span> has an account, we&apos;ve sent a link to
                    choose a new password. It works once and expires in an hour.
                </p>
                <p className="text-sm text-muted-foreground">
                    <Link href="/login" className={linkClass}>Back to log in</Link>
                </p>
            </Shell>
        );
    }

    return (
        <Shell icon={<Lock className="w-6 h-6" />} title="Forgot your password?">
            <p className="text-muted-foreground">Enter your email and we&apos;ll send you a link to reset it.</p>
            <form onSubmit={handleSubmit} className="space-y-4">
                <Field id="email" label="Email" icon={Mail}>
                    <Input id="email" name="email" type="email" placeholder="you@example.com" required autoComplete="email" className="pl-10 h-11" />
                </Field>
                {error && <ErrorText>{error}</ErrorText>}
                <Button type="submit" disabled={loading} className="w-full h-11 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold">
                    {loading && <Loader2 className="w-4 h-4 animate-spin" />}
                    {loading ? "Sending…" : "Send reset link"}
                </Button>
            </form>
            <p className="text-sm text-muted-foreground">
                Remembered it? <Link href="/login" className={linkClass}>Log in</Link>
            </p>
        </Shell>
    );
}

export function ResetPasswordForm() {
    const router = useRouter();
    const token = useSearchParams().get("token") ?? "";
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    if (!token) {
        return (
            <Shell icon={<AlertCircle className="w-6 h-6" />} title="Link not valid">
                <p className="text-muted-foreground">This reset link is incomplete. Request a new one.</p>
                <Link href="/forgot-password" className={linkClass}>Send a new link</Link>
            </Shell>
        );
    }

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        setError(null);
        const fd = new FormData(e.currentTarget);
        const password = String(fd.get("password") ?? "");
        if (password !== String(fd.get("confirm") ?? "")) {
            setError("Passwords don't match");
            return;
        }
        setLoading(true);
        try {
            await api("/auth/reset-password", { method: "POST", body: JSON.stringify({ token, password }) });
            toast.success("Password changed. Log in with the new one.");
            router.push("/login");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Something went wrong");
            setLoading(false);
        }
    };

    return (
        <Shell icon={<Lock className="w-6 h-6" />} title="Choose a new password">
            <form onSubmit={handleSubmit} className="space-y-4">
                <Field id="password" label="New password" icon={Lock}>
                    <Input id="password" name="password" type="password" required minLength={8} placeholder="At least 8 characters" autoComplete="new-password" className="pl-10 h-11" />
                </Field>
                <Field id="confirm" label="Confirm password" icon={Lock}>
                    <Input id="confirm" name="confirm" type="password" required autoComplete="new-password" className="pl-10 h-11" />
                </Field>
                {error && (
                    <ErrorText>
                        {error}{" "}
                        {error.includes("expired") && <Link href="/forgot-password" className="font-semibold underline">Send a new link</Link>}
                    </ErrorText>
                )}
                <Button type="submit" disabled={loading} className="w-full h-11 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold">
                    {loading && <Loader2 className="w-4 h-4 animate-spin" />}
                    {loading ? "Saving…" : "Change password"}
                </Button>
            </form>
        </Shell>
    );
}

const OAUTH_ERRORS: Record<string, string> = {
    cancelled: "Sign-in was cancelled.",
    unverified_email: "Google couldn't confirm that email address, so we can't sign you in with it.",
    expired: "That sign-in took too long or was started in another browser. Please try again.",
    failed: "Google sign-in didn't work. Please try again.",
};

/** Where the server sends the browser back to after Google. It carries a one-time code, or an error. */
export function OAuthCallback() {
    const router = useRouter();
    const params = useSearchParams();
    const code = params.get("code") ?? "";
    const errorKey = params.get("error");
    const [error, setError] = useState<string | null>(
        errorKey ? (OAUTH_ERRORS[errorKey] ?? OAUTH_ERRORS.failed) : code ? null : OAUTH_ERRORS.failed,
    );
    // The code works once, and React runs effects twice in development: only ask the server once.
    const asked = useRef(false);

    useEffect(() => {
        if (!code || errorKey || asked.current) return;
        asked.current = true;
        api<{ access_token: string; user: User }>("/auth/oauth/exchange", {
            method: "POST",
            body: JSON.stringify({ token: code }),
        })
            .then((data) => {
                setToken(data.access_token);
                toast.success("Welcome!");
                router.replace("/dashboard");
            })
            .catch((err) => setError(err instanceof Error ? err.message : OAUTH_ERRORS.failed));
    }, [code, errorKey, router]);

    if (error) {
        return (
            <Shell icon={<AlertCircle className="w-6 h-6" />} title="Couldn't sign you in">
                <ErrorText>{error}</ErrorText>
                <Link href="/login" className={linkClass}>Back to log in</Link>
            </Shell>
        );
    }
    return (
        <Shell icon={<Loader2 className="w-6 h-6 animate-spin" />} title="Signing you in…">
            <></>
        </Shell>
    );
}

export function VerifyEmail() {
    const router = useRouter();
    const token = useSearchParams().get("token") ?? "";
    const [error, setError] = useState<string | null>(token ? null : "This link is incomplete.");
    // The link works once, and React runs effects twice in development: only ask the server once.
    const asked = useRef(false);

    useEffect(() => {
        if (!token || asked.current) return;
        asked.current = true;
        api<{ access_token: string; user: User }>("/auth/verify-email", {
            method: "POST",
            body: JSON.stringify({ token }),
        })
            .then((data) => {
                setToken(data.access_token);
                toast.success("Email confirmed. Welcome!");
                router.replace("/dashboard");
            })
            .catch((err) => setError(err instanceof Error ? err.message : "Something went wrong"));
    }, [token, router]);

    if (error) {
        return (
            <Shell icon={<AlertCircle className="w-6 h-6" />} title="Couldn't confirm your email">
                <ErrorText>{error}</ErrorText>
                <p className="text-sm text-muted-foreground">
                    If you already confirmed it, <Link href="/login" className={linkClass}>log in</Link>. Otherwise log in
                    and we&apos;ll offer to send a new link.
                </p>
            </Shell>
        );
    }
    return (
        <Shell icon={<CheckCircle2 className="w-6 h-6" />} title="Confirming your email…">
            <Loader2 className="w-5 h-5 animate-spin mx-auto text-muted-foreground" />
        </Shell>
    );
}
