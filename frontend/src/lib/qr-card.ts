import QRCode from "qrcode";

// Draws the printable QR card for an event: its name, the QR code, a line of instructions and,
// when the event has one, the access code. Kept apart from the dialog so it can be tested.

function loadImage(src: string): Promise<HTMLImageElement> {
    return new Promise((resolve, reject) => {
        const img = new window.Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error("Couldn't draw the QR code"));
        img.src = src;
    });
}

/** Break text into lines that fit `maxWidth`, at most `maxLines` (the last line gets "…" if cut). */
function wrap(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, maxLines: number): string[] {
    const words = text.split(/\s+/).filter(Boolean);
    const lines: string[] = [];
    let line = "";
    for (let i = 0; i < words.length; i++) {
        const test = line ? `${line} ${words[i]}` : words[i];
        if (ctx.measureText(test).width <= maxWidth || !line) {
            line = test;
        } else {
            lines.push(line);
            line = words[i];
        }
        if (lines.length === maxLines) break;
    }
    if (lines.length < maxLines && line) lines.push(line);
    if (lines.join(" ").length < words.join(" ").length && lines.length) { // something was cut off
        let last = lines[lines.length - 1];
        while (last.length > 1 && ctx.measureText(`${last}…`).width > maxWidth) last = last.slice(0, -1);
        lines[lines.length - 1] = `${last}…`;
    }
    return lines;
}

/** Draws the card (event name, QR, short instructions, code if any) and returns it as a PNG data URL. */
export async function makeCard(url: string, eventName: string, code: string | null): Promise<string> {
    const qr = await loadImage(
        await QRCode.toDataURL(url, { width: 760, margin: 4, errorCorrectionLevel: "M", color: { dark: "#111827", light: "#ffffff" } })
    );
    const W = 900;
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = 1800; // more than any card needs; trimmed to its content at the end
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Couldn't draw the card");

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, W, canvas.height);
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    const font = (size: number, weight = "400", family = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif") =>
        (ctx.font = `${weight} ${size}px ${family}`);

    let y = 120;
    ctx.fillStyle = "#111827";
    font(60, "800");
    for (const line of wrap(ctx, eventName, W - 140, 2)) {
        ctx.fillText(line, W / 2, y);
        y += 72;
    }

    y += 10;
    ctx.fillStyle = "#4f46e5";
    font(40, "700");
    ctx.fillText("Scan to find your photos", W / 2, y);

    y += 50;
    ctx.strokeStyle = "#e5e7eb";
    ctx.lineWidth = 4;
    ctx.drawImage(qr, 70, y, 760, 760); // the image carries its own blank border, which scanners need
    ctx.strokeRect(70, y, 760, 760);
    y += 760 + 70;

    ctx.fillStyle = "#4b5563";
    font(30);
    for (const line of wrap(ctx, "Open your phone's camera, point it at the code, take a selfie and your photos appear.", W - 160, 2)) {
        ctx.fillText(line, W / 2, y);
        y += 42;
    }

    let end = y - 42 + 24; // just below the last line of instructions
    if (code) {
        y += 24;
        ctx.fillStyle = "#111827";
        font(44, "700", "ui-monospace, Menlo, Consolas, monospace");
        ctx.fillText(`Code: ${code}`, W / 2, y);
        end = y + 24;
    }

    // Trim to the content, with the same white border all round
    const out = document.createElement("canvas");
    out.width = W;
    out.height = end + 60;
    const outCtx = out.getContext("2d");
    if (!outCtx) throw new Error("Couldn't draw the card");
    outCtx.drawImage(canvas, 0, 0);
    return out.toDataURL("image/png");
}
