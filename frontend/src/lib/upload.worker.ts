/**
 * Web Worker that runs the photo upload (see upload-engine.ts). The upload runs here, not in the page, so it is
 * not held up when the page is in a background tab or its main thread is busy.
 */
import { runUpload, type UploadJob } from "./upload-engine";

const ctx = self as unknown as {
    onmessage: ((e: MessageEvent<UploadJob>) => void) | null;
    postMessage: (message: unknown) => void;
};

ctx.onmessage = async (e) => {
    ctx.postMessage({ type: "started" });
    try {
        const result = await runUpload(e.data, {
            progress: (fraction) => ctx.postMessage({ type: "progress", fraction }),
            warn: (message) => console.warn(message),
            info: (message) => console.info(message),
        });
        ctx.postMessage({ type: "done", result });
    } catch (err) {
        ctx.postMessage({ type: "crashed", message: err instanceof Error ? err.message : String(err) });
    }
};
