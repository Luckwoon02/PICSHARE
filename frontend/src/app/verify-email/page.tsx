import { Suspense } from "react";
import type { Metadata } from "next";
import { VerifyEmail } from "@/components/auth-flows";

export const metadata: Metadata = { title: "Confirm email" };

export default function VerifyEmailPage() {
    return (
        <Suspense>
            <VerifyEmail />
        </Suspense>
    );
}
