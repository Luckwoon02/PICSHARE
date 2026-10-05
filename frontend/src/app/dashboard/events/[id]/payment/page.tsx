import type { Metadata } from "next";
import PaymentClient from "@/components/dashboard/payment-client";

export const metadata: Metadata = { title: "Payment" };

export default function PaymentPage() {
    return <PaymentClient />;
}
