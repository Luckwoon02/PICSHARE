import { Metadata } from "next";
import SelectionClient from "@/components/selection-client";

export const metadata: Metadata = {
    title: "Choose your photos",
    // A private link between a photographer and their client: keep it out of search results
    robots: { index: false, follow: false },
};

export default function SelectionPage() {
    return <SelectionClient />;
}
