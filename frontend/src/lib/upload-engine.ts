/**
 * The photo upload itself: ask the server for signed S3 links, send the photos straight to S3 a few at a
 * time, and tell the server which ones finished. It only uses fetch/XMLHttpRequest/FormData, so it runs in
 * a Web Worker (upload.worker.ts), which keeps going when the page is in the background or its main thread is
 * busy or throttled, and it can also run on the main thread as a fallback (see upload-client.ts).
 *
 * It must not import anything that needs `window`/`document` (cookies, toasts, React).
 */

const PARALLEL_UPLOADS = 6; // photos sent to S3 at once (browsers allow 6 connections per host anyway)
const COMPLETE_BATCH = 25; // tell the server about finished uploads in groups
const SIGN_BATCH = 50; // upload links requested per call, just before they're needed
const REPORT_EVERY_MS = 250; // how often progress is reported
const METER_EVERY_MS = 15000; // how often the upload prints a speed line

export interface UploadJob {
    apiUrl: string;
    token?: string;
    eventId: string;
    files: File[];
}

export interface UploadHooks {
    /** fraction of all bytes sent, 0..1 */
    progress(fraction: number): void;
    /** a problem worth putting in the console */
    warn(message: string): void;
    /** a routine status line for the console */
    info(message: string): void;
}

export interface UploadResult {
    succeeded: number;
    /** indexes into `files` of photos that did not make it (the whole selection if links couldn't be had) */
    failedIndexes: number[];
    /** why upload links could not be obtained, if that was the problem */
    signError: string | null;
    /** the first S3 refusal or network failure, if any photo failed to upload */
    firstUploadError: string | null;
    /** the server said the login is no longer valid */
    unauthorized: boolean;
}

interface SignedUpload {
    photo_id: string;
    url: string;
    fields: Record<string, string>;
}

class HttpError extends Error {
    constructor(
        public status: number,
        message: string
    ) {
        super(message);
    }
}

const reason = (err: unknown) => (err instanceof Error ? err.message : String(err));

function apiErrorMessage(body: unknown, fallback: string): string {
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
        const first = detail[0] as { msg?: string };
        if (first?.msg) return first.msg.replace(/^Value error, /, "");
    }
    return fallback;
}

async function postJson<T>(job: UploadJob, path: string, body: unknown): Promise<T> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (job.token) headers.Authorization = `Bearer ${job.token}`;
    let res: Response;
    try {
        res = await fetch(`${job.apiUrl}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
    } catch (err) {
        throw new HttpError(0, `Can't reach the server (${reason(err)}). Check your connection and try again.`);
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new HttpError(res.status, apiErrorMessage(data, `Request failed (${res.status})`));
    return data as T;
}

/** What S3 said when it turned a photo down, in words (S3 answers with a small XML document). */
function s3Refusal(name: string, status: number, body: string): string {
    const code = /<Code>([^<]*)<\/Code>/.exec(body)?.[1];
    const message = /<Message>([^<]*)<\/Message>/.exec(body)?.[1];
    let text = `S3 refused "${name}" (HTTP ${status}${code ? `, ${code}` : ""}${message ? `: ${message}` : ""})`;
    if (code === "AccessDenied" && message === "Access Denied") {
        text += ". The AWS user behind backend/.env may not be allowed to upload to this bucket.";
    }
    return text;
}

/** How many photos are being sent to S3 right now (for the speed lines). */
let inFlight = 0;

/** Send one photo straight to S3 with its signed link. Reports bytes sent so far. */
function sendToS3(
    url: string,
    fields: Record<string, string>,
    file: File,
    onProgress: (loadedBytes: number) => void
): Promise<void> {
    return new Promise((resolve, reject) => {
        const form = new FormData();
        Object.entries(fields).forEach(([k, v]) => form.append(k, v));
        form.append("file", file); // S3 requires the file to be the last field

        inFlight++;
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url);
        xhr.upload.onprogress = (e) => onProgress(e.loaded);
        xhr.onerror = () => {
            inFlight--;
            reject(new HttpError(0, `Upload of "${file.name}" failed. Check your connection.`));
        };
        xhr.onload = () => {
            inFlight--;
            if (xhr.status >= 200 && xhr.status < 300) resolve();
            else reject(new HttpError(xhr.status, s3Refusal(file.name, xhr.status, xhr.responseText)));
        };
        xhr.send(form);
    });
}

export async function runUpload(job: UploadJob, hooks: UploadHooks): Promise<UploadResult> {
    const { files, eventId } = job;
    const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
    const failed = new Set<number>();
    let succeeded = 0;
    let signError: string | null = null;
    let firstUploadError: string | null = null;
    let unauthorized = false;
    const noteError = (err: unknown) => {
        if (err instanceof HttpError && err.status === 401) unauthorized = true;
    };

    // Signed S3 links are requested a group at a time, just before they're needed. That lets the first photo
    // start right away, keeps the links fresh (they expire after an hour) and has no limit on how many photos
    // can be selected.
    const signedGroups = new Map<number, Promise<SignedUpload[]>>();
    const signGroup = (group: number) => {
        let promise = signedGroups.get(group);
        if (!promise) {
            const slice = files.slice(group * SIGN_BATCH, (group + 1) * SIGN_BATCH);
            promise = postJson<{ uploads: SignedUpload[] }>(job, `/photos/upload-urls?event_id=${eventId}`, {
                files: slice.map((f) => ({ name: f.name, size: f.size, content_type: f.type })),
            }).then((res) => res.uploads);
            signedGroups.set(group, promise);
        }
        return promise;
    };
    const signedUpload = async (i: number) => {
        const group = Math.floor(i / SIGN_BATCH);
        const position = i - group * SIGN_BATCH;
        // Start on the next group once this one is half used, so uploads never wait on signing
        if (position >= SIGN_BATCH / 2 && (group + 1) * SIGN_BATCH < files.length) {
            signGroup(group + 1).catch(() => {}); // a failure is reported when that group is needed
        }
        return (await signGroup(group))[position];
    };

    // Progress is reported a few times a second, not on every network event.
    const sent = new Array<number>(files.length).fill(0);
    let lastReport = 0;
    const report = (force = false) => {
        const now = Date.now();
        if (!force && now - lastReport < REPORT_EVERY_MS) return;
        lastReport = now;
        hooks.progress(sent.reduce((a, b) => a + b, 0) / Math.max(totalBytes, 1));
    };

    // Tell the server which uploads finished. Photos are only processed once the last call (final) arrives,
    // so processing doesn't compete with the upload for CPU and network.
    let flushing: Promise<void> = Promise.resolve();
    const doneIds: string[] = [];
    const failedIds: string[] = [];
    const flush = (force = false, final = false) => {
        if (!final && !force && doneIds.length < COMPLETE_BATCH) return;
        const body = { uploaded: doneIds.splice(0), failed: failedIds.splice(0), final };
        if (!final && body.uploaded.length === 0 && body.failed.length === 0) return;
        flushing = flushing.then(() =>
            postJson(job, `/photos/upload-complete?event_id=${eventId}`, body)
                .then(() => undefined)
                .catch((err) => {
                    noteError(err);
                    hooks.warn(`[pixello] couldn't tell the server the upload finished: ${reason(err)}`);
                })
        );
    };

    let next = 0;
    const worker = async () => {
        while (next < files.length) {
            const i = next++;
            let signed: SignedUpload;
            try {
                signed = await signedUpload(i);
            } catch (err) {
                noteError(err);
                if (!signError) signError = reason(err);
                hooks.warn(`[pixello] couldn't get an upload link for "${files[i].name}": ${reason(err)}`);
                failed.add(i);
                continue;
            }
            const { url, fields, photo_id } = signed;
            let ok = false;
            for (let attempt = 0; attempt < 2 && !ok; attempt++) {
                try {
                    await sendToS3(url, fields, files[i], (loaded) => {
                        sent[i] = loaded;
                        report();
                    });
                    ok = true;
                } catch (err) {
                    sent[i] = 0;
                    if (!firstUploadError) firstUploadError = reason(err);
                    hooks.warn(`[pixello] upload of "${files[i].name}" failed (try ${attempt + 1} of 2): ${reason(err)}`);
                    if (err instanceof HttpError && err.status === 403) break; // a permission refusal won't change on a second try
                }
            }
            if (ok) {
                sent[i] = files[i].size;
                succeeded++;
                doneIds.push(photo_id);
            } else {
                failed.add(i);
                failedIds.push(photo_id);
            }
            report();
            flush();
        }
    };

    // Every few seconds say how the upload is doing, so a slowdown or stall is visible in the console while it
    // happens (and shows whether it lines up with the tab going to the background).
    inFlight = 0;
    let lastMeterAt = Date.now();
    let lastMeterBytes = 0;
    let lastSpeed = -1;
    const meter = setInterval(() => {
        const now = Date.now();
        const seconds = (now - lastMeterAt) / 1000;
        const bytes = sent.reduce((a, b) => a + b, 0);
        const speed = Math.max(0, bytes - lastMeterBytes) / seconds / 1_000_000; // MB/s
        const finished = succeeded + failed.size;
        hooks.info(
            `[pixello] upload ${Math.round((bytes / Math.max(totalBytes, 1)) * 100)}% | ${speed.toFixed(1)} MB/s | ` +
                `${inFlight} in flight | ${finished} of ${files.length} photos finished`
        );
        if (seconds > (METER_EVERY_MS / 1000) * 1.5) {
            hooks.warn(`[pixello] the upload's clock was delayed: ${seconds.toFixed(0)}s since the last check instead of ${METER_EVERY_MS / 1000}s, so the browser is slowing this page down.`);
        }
        if (lastSpeed > 0 && speed < lastSpeed * 0.25) {
            hooks.warn(`[pixello] upload slowed down sharply: ${lastSpeed.toFixed(1)} -> ${speed.toFixed(1)} MB/s with ${inFlight} in flight.`);
        }
        if (finished < files.length && inFlight < Math.min(PARALLEL_UPLOADS, files.length - finished)) {
            hooks.warn(`[pixello] only ${inFlight} photo(s) are uploading at once (expected up to ${PARALLEL_UPLOADS}).`);
        }
        lastMeterAt = now;
        lastMeterBytes = bytes;
        lastSpeed = speed;
    }, METER_EVERY_MS);

    try {
        await Promise.all(Array.from({ length: Math.min(PARALLEL_UPLOADS, files.length) }, worker));
        report(true);
        flush(true, true);
        await flushing;
    } finally {
        clearInterval(meter);
    }

    return {
        succeeded,
        failedIndexes: [...failed].sort((a, b) => a - b),
        signError,
        firstUploadError,
        unauthorized,
    };
}
