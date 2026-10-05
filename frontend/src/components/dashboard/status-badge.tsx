import type { EventItem } from "@/lib/api";

const STYLES: Record<EventItem["status"], { label: string; cls: string; dot: string }> = {
    active: {
        label: "Active",
        cls: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
        dot: "bg-green-500",
    },
    pending_payment: {
        label: "Awaiting payment",
        cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
        dot: "bg-amber-500",
    },
};

export function StatusBadge({ status }: { status: EventItem["status"] }) {
    const s = STYLES[status] ?? STYLES.active;
    return (
        <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${s.cls}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
            {s.label}
        </span>
    );
}
