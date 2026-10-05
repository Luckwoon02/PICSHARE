import type { Metadata } from "next";
import NewEventClient from "@/components/dashboard/new-event-client";

export const metadata: Metadata = { title: "Create event" };

export default function NewEventPage() {
    return <NewEventClient />;
}
