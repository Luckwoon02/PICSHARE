import { Metadata } from "next";
import EventsListClient from "@/components/events-list-client";

export const metadata: Metadata = {
    title: "Events | Pixello",
    description: "Browse and join events to find your photos",
};

export default function EventsPage() {
    return <EventsListClient />;
}
