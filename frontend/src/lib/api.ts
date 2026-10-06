import Cookies from "js-cookie";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";

const TOKEN_COOKIE = "picshare_token";
const AUTH_EVENT = "picshare-auth-change";

// ---------------------------------------------------------------------------
// Types shared across the dashboard
// ---------------------------------------------------------------------------

export interface User {
    id: string;
    name: string;
    email: string;
}

export interface EventItem {
    _id: string;
    name: string;
    slug: string;
    date: string;
    start_date: string | null;
    end_date: string | null;
    storage_capacity_gb: number | null;
    status: "pending_payment" | "active";
    amount_cents: number;
    payment_status: string;
    secret_code?: string | null;
    created_at: string;
}

export interface PaymentConfig {
    payment_required: boolean;
    provider: string;
    currency: string;
    price_per_gb_cents: number;
    min_storage_gb: number;
    max_storage_gb: number;
}

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

export const getToken = () => Cookies.get(TOKEN_COOKIE);

export function setToken(token: string) {
    Cookies.set(TOKEN_COOKIE, token, { expires: 1, sameSite: "lax" });
    window.dispatchEvent(new Event(AUTH_EVENT));
}

export function clearToken() {
    Cookies.remove(TOKEN_COOKIE);
    window.dispatchEvent(new Event(AUTH_EVENT));
}

export const onAuthChange = (cb: () => void) => {
    window.addEventListener(AUTH_EVENT, cb);
    return () => window.removeEventListener(AUTH_EVENT, cb);
};

// ---------------------------------------------------------------------------
// Fetch wrapper
// ---------------------------------------------------------------------------

export class ApiError extends Error {
    constructor(public status: number, message: string) {
        super(message);
    }
}

function errorMessage(body: unknown, fallback: string): string {
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    // FastAPI validation errors: [{ loc, msg }, ...]
    if (Array.isArray(detail) && detail.length) {
        const first = detail[0] as { msg?: string };
        if (first?.msg) return first.msg.replace(/^Value error, /, "");
    }
    return fallback;
}

/**
 * Fetch JSON from the API, attaching the user's token. A 401 on an authenticated
 * call clears the session so route guards send the user back to /login.
 */
export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
    }

    let res: Response;
    try {
        res = await fetch(`${API_URL}${path}`, { ...init, headers });
    } catch {
        throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
    }

    const body = await res.json().catch(() => null);
    if (!res.ok) {
        if (res.status === 401 && token && !path.startsWith("/auth/login")) clearToken();
        throw new ApiError(res.status, errorMessage(body, `Request failed (${res.status})`));
    }
    return body as T;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

export function formatBytes(bytes: number): string {
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${bytes} B`;
}

export function formatMoney(cents: number, currency = "usd"): string {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
}

export function formatDateRange(start?: string | null, end?: string | null): string {
    const fmt = (d: string) =>
        new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    if (!start) return "";
    if (!end || fmt(start) === fmt(end)) return fmt(start);
    return `${fmt(start)} – ${fmt(end)}`;
}

// ---------------------------------------------------------------------------
// Upload with progress (fetch can't report upload progress)
// ---------------------------------------------------------------------------

/** Upload one file straight to S3 using a presigned POST. Reports bytes sent so far. */
export function uploadToS3(
    url: string,
    fields: Record<string, string>,
    file: File,
    onProgress: (loadedBytes: number) => void
): Promise<void> {
    return new Promise((resolve, reject) => {
        const form = new FormData();
        Object.entries(fields).forEach(([k, v]) => form.append(k, v));
        form.append("file", file); // S3 requires the file to be the last field

        const xhr = new XMLHttpRequest();
        xhr.open("POST", url);
        xhr.upload.onprogress = (e) => onProgress(e.loaded);
        xhr.onerror = () => reject(new ApiError(0, "Upload failed — check your connection and try again."));
        xhr.onload = () =>
            xhr.status >= 200 && xhr.status < 300
                ? resolve()
                : reject(new ApiError(xhr.status, `Storage rejected ${file.name} (${xhr.status})`));
        xhr.send(form);
    });
}

export function uploadWithProgress<T = unknown>(
    path: string,
    form: FormData,
    onProgress: (fraction: number) => void
): Promise<T> {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_URL}${path}`);
        const token = getToken();
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) onProgress(e.loaded / e.total);
        };
        xhr.onerror = () => reject(new ApiError(0, "Upload failed — check your connection and try again."));
        xhr.onload = () => {
            let body: unknown = null;
            try {
                body = JSON.parse(xhr.responseText);
            } catch {}
            if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
            else {
                if (xhr.status === 401) clearToken();
                reject(new ApiError(xhr.status, errorMessage(body, `Upload failed (${xhr.status})`)));
            }
        };
        xhr.send(form);
    });
}
