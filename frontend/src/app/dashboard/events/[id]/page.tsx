import type { Metadata } from "next";
import EventDetailClient from "@/components/dashboard/event-detail-client";

export const metadata: Metadata = { title: "Event" };

export default function EventDetailPage() {
    return <EventDetailClient />;
}
