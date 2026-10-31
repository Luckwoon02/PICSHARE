import { Suspense } from "react";
import type { Metadata } from "next";
import { OAuthCallback } from "@/components/auth-flows";

export const metadata: Metadata = { title: "Signing in" };

export default function OAuthCallbackPage() {
    return (
        <Suspense>
            <OAuthCallback />
        </Suspense>
    );
}
