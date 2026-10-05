"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, clearToken, getToken, onAuthChange, type User } from "@/lib/api";

type State = { user: User | null; loading: boolean };

/**
 * Resolves the current user from the stored token. Pass `{ required: true }` on pages
 * that need a session — they redirect to /login when there is none.
 */
export function useAuth({ required = false }: { required?: boolean } = {}) {
    const router = useRouter();
    const [state, setState] = useState<State>({ user: null, loading: true });

    useEffect(() => {
        let cancelled = false;
        const load = () => {
            // api() clears an invalid token itself, so any failure here means "signed out"
            const request = getToken() ? api<User>("/auth/me").catch(() => null) : Promise.resolve(null);
            request.then((user) => {
                if (!cancelled) setState({ user, loading: false });
            });
        };
        load();
        const unsubscribe = onAuthChange(load);
        return () => {
            cancelled = true;
            unsubscribe();
        };
    }, []);

    useEffect(() => {
        if (required && !state.loading && !state.user) router.replace("/login");
    }, [required, state, router]);

    const logout = useCallback(() => {
        clearToken();
        router.push("/login");
    }, [router]);

    return { ...state, logout };
}
